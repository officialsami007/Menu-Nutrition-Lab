"""Bonus 1 of the brief: answer free-text questions about the menu.

Uses tool calling: the model asks for exact figures through four tools, each of which maps
onto a function in nutrition.processing.queries. This module only translates between the
model's JSON and those functions.
"""
import json

from ..config import METRICS
from ..processing import queries
from . import client, notes
from .context import SYSTEM_RULES, facts

MAX_TOOL_ROUNDS = 4
HISTORY_MESSAGES = 6  # the last three question-and-answer pairs
HISTORY_CHARS = 700
FOLD_AT_MOST = 12  # older messages folded into the notes at once; guards the token budget

_DATASET = {"type": "string", "enum": ["drinks", "food", "both"]}
_METRIC = {"type": "string", "enum": list(METRICS)}
_FILTERS = {
    "type": "object",
    "description": "Optional filters applied before calculating.",
    "properties": {
        "category": {"type": "string", "description": "Exact category name from the data."},
        "caffeinated": {"type": "boolean"},
        "name_contains": {"type": "string"},
        "at_least": {"type": "object", "description": "Minimums, e.g. {\"protein\": 10}", "additionalProperties": {"type": "number"}},
        "at_most": {"type": "object", "description": "Maximums, value included, e.g. {\"calories\": 500}", "additionalProperties": {"type": "number"}},
        "under": {"type": "object", "description": "Strictly less than, for 'under'/'less than', e.g. {\"calories\": 500}", "additionalProperties": {"type": "number"}},
    },
}

TOOLS: list[client.Tool] = [
    {"type": "function", "function": {
        "name": "aggregate",
        "description": "Calculate a statistic of one nutrient, optionally per category.",
        "parameters": {"type": "object", "properties": {
            "dataset": _DATASET, "metric": _METRIC, "stat": {"type": "string", "enum": list(queries.STATS)},
            "by_category": {"type": "boolean"}, "filters": _FILTERS,
        }, "required": ["dataset", "metric", "stat"]},
    }},
    {"type": "function", "function": {
        "name": "rank_items",
        "description": "Top or bottom items sorted by a nutrient (at most 25), with their nutrition. Items tied at the cut-off are included. To list many item names, use list_items instead.",
        "parameters": {"type": "object", "properties": {
            "dataset": _DATASET, "metric": _METRIC, "order": {"type": "string", "enum": ["highest", "lowest"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25}, "filters": _FILTERS,
        }, "required": ["dataset", "metric", "order"]},
    }},
    {"type": "function", "function": {
        "name": "list_items",
        "description": "The names of items that match the filters, with how many matched in total. Names only, except that a nutrient used in a filter is shown after each name. Use it for 'which drinks...' or 'list the...' questions; use rank_items when the answer needs other numbers.",
        "parameters": {"type": "object", "properties": {
            "dataset": _DATASET, "filters": _FILTERS, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        }, "required": ["dataset"]},
    }},
    {"type": "function", "function": {
        "name": "find_items",
        "description": "Look up full nutrition for items whose name contains the given text.",
        "parameters": {"type": "object", "properties": {"dataset": _DATASET, "text": {"type": "string"}},
                       "required": ["dataset", "text"]},
    }},
]


def to_filters(tool_filters: dict | None) -> dict:
    """Convert the tool's filter object into the format processing.apply_filters expects."""
    tool_filters = tool_filters or {}
    flat = {"search": tool_filters.get("name_contains"), "category": tool_filters.get("category")}
    if isinstance(tool_filters.get("caffeinated"), bool):
        flat["caffeine"] = "yes" if tool_filters["caffeinated"] else "no"
    # The tool uses everyday words; apply_filters uses _min / _max / _below suffixes.
    for word, suffix in (("at_least", "min"), ("at_most", "max"), ("under", "below")):
        for metric, value in (tool_filters.get(word) or {}).items():
            flat[f"{metric}_{suffix}"] = value
    return flat


def run_tool(name: str, args: dict, frames: dict) -> dict:
    """Execute one tool call. Problems are returned as {'error': ...} so the model can recover."""
    filters = to_filters(args.get("filters"))
    try:
        if name == "aggregate":
            result = queries.aggregate(frames, args.get("dataset", "both"), args["metric"], args.get("stat", "mean"),
                                       bool(args.get("by_category")), filters)
        elif name == "rank_items":
            result = queries.rank(frames, args.get("dataset", "both"), args["metric"], args.get("order") == "lowest",
                                  int(args.get("limit") or 5), filters)
        elif name == "list_items":
            # Nutrients the filters use are shown next to each name, so the answer never needs a guessed number.
            used = dict.fromkeys(m for word in ("at_least", "at_most", "under") for m in ((args.get("filters") or {}).get(word) or {}))
            result = queries.list_items(frames, args.get("dataset", "both"), filters, int(args.get("limit") or 100), tuple(used))
        elif name == "find_items":
            result = queries.find(frames, args.get("dataset", "both"), str(args.get("text", "")))
        else:
            return {"error": f"Unknown tool {name}."}
    except queries.QueryError as exc:
        return {"error": str(exc)}
    notes = []
    if "caffeine" in filters and queries.any_estimated_caffeine(frames):
        notes.append("Caffeine flag is estimated from item names.")
    # Models tend to stop at the number they asked for; ties make the list longer, so say so.
    if name == "rank_items" and any(len(rows) > int(args.get("limit") or 5) for rows in result.values()):
        notes.append("Items tied at the cut-off are included, so there are more than the limit. List all of them.")
    if notes:
        result["note"] = " ".join(notes)
    return result


def _clean_history(history) -> list[dict]:
    """The well-formed turns of a chat, each cut to HISTORY_CHARS."""
    return [{"role": t["role"], "content": t["content"][:HISTORY_CHARS]} for t in (history or [])
            if isinstance(t, dict) and t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str)]


def stream_answer(question: str, history: list, memory: dict | None, frames: dict, reports: dict):
    """Answer a free-text question as a stream of events (dicts with a "type").

    "text": a piece of the answer. "reset": drop the text so far (the model spoke, then asked for a tool).
    "step": one tool call with its result and the item counts before and after filtering. "memory": the chat's updated running notes ({"notes", "covered"}).
    "error": something failed mid-stream. A missing API key raises LLMError here, before anything is streamed.
    """
    groq_client = client.get_client()
    turns = _clean_history(history)
    recent = turns[-HISTORY_MESSAGES:]
    older_end = len(turns) - len(recent)
    memory = memory if isinstance(memory, dict) else {}
    chat_notes = str(memory.get("notes") or "")
    covered_value = memory.get("covered")
    covered = covered_value if isinstance(covered_value, int) else 0

    def run():
        nonlocal chat_notes
        try:
            # Messages that have fallen out of the recent window are folded into the notes first.
            if covered < older_end:
                chat_notes = notes.fold(groq_client, chat_notes, turns[max(covered, older_end - FOLD_AT_MOST):older_end])
                yield {"type": "memory", "notes": chat_notes, "covered": older_end}

            categories = {k: sorted(df["category"].unique().tolist()) for k, df in frames.items()}
            messages: list[client.Message] = [{"role": "system", "content": (
                SYSTEM_RULES
                + "\nUse the tools for any exact figure not already in DATA. Keep answers under 180 words."
                + "\nFilters: 'under' or 'less than' means the 'under' filter; 'at most' or 'up to' means 'at_most'."
                + "\nList every item a tool returns for a ranking, including ties."
                + "\nFor 'which', 'list' or 'names' questions call list_items (names) or rank_items (sorted by a nutrient) every time."
                + "\nNever reuse a list from earlier in the chat: it may have been partial. Never say you cannot list items."
                + "\nEvery item name you mention must come from DATA or a tool result. Never make up or guess item names, not even as examples."
                + "\nNever state a number that is not in DATA or a tool result. If you need values for items, call rank_items."
                + "\nQuestions often refer to earlier messages ('the 62', 'those drinks'); use the conversation to resolve them."
                + (f"\nNotes on the earlier conversation: {chat_notes}" if chat_notes else "")
                + f"\nCategories: {json.dumps(categories, ensure_ascii=False)}"
                + "\n\nDATA:\n" + facts(frames, reports, detailed=False)
            )}]
            # Recent turns are sent as they were, so follow-ups like "and for food?" make sense.
            for turn in recent:
                messages.append({"role": "user", "content": turn["content"]} if turn["role"] == "user"
                                else {"role": "assistant", "content": turn["content"]})
            messages.append({"role": "user", "content": question})

            # Tool-calling loop: each round the model either answers or asks for tools. We run the tools
            # with pandas, hand the results back and let it continue. MAX_TOOL_ROUNDS stops a model that
            # keeps calling tools; if the rounds run out we ask for an answer from what was gathered.
            retried_empty = False
            for _ in range(MAX_TOOL_ROUNDS):
                text, calls = "", []
                for kind, payload in client.stream_completion(groq_client, messages, tools=TOOLS, tool_choice="auto", temperature=0.2):
                    if kind == "text":
                        text += payload
                        yield {"type": "text", "text": payload}
                    else:
                        calls = payload
                if calls:
                    if text:
                        yield {"type": "reset"}
                    messages.append({"role": "assistant", "content": text, "tool_calls": [
                        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                        for c in calls]})
                    for call in calls:
                        try:
                            args = json.loads(call["arguments"] or "{}")
                            output = run_tool(call["name"], args, frames)
                        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                            args, output = {}, {"error": f"Invalid tool call: {exc}"}
                        counts = queries.pool_sizes(frames, args.get("dataset", "both"), to_filters(args.get("filters")))
                        yield {"type": "step", "tool": call["name"], "args": args, "result": output, "counts": counts}
                        messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": json.dumps(output, ensure_ascii=False, separators=(",", ":"), default=str)})
                    continue
                if text.strip():
                    break
                if retried_empty:  # the model sometimes returns nothing; one more try usually works
                    yield {"type": "text", "text": "No answer was returned. Try rephrasing."}
                    break
                retried_empty = True
            else:
                messages.append({"role": "user", "content": "Answer now with the data gathered."})
                for kind, payload in client.stream_completion(groq_client, messages, temperature=0.2):
                    if kind == "text":
                        yield {"type": "text", "text": payload}
        except Exception as exc:
            yield {"type": "error", "message": str(client.friendly_error(exc))}

    return run()


def answer_question(question: str, history: list, frames: dict, reports: dict) -> dict:
    """The whole answer at once, for the command line: {'answer': markdown, 'steps': [tool calls made]}."""
    answer, steps = "", []
    for event in stream_answer(question, history, None, frames, reports):
        if event["type"] == "text":
            answer += event["text"]
        elif event["type"] == "reset":
            answer = ""
        elif event["type"] == "step":
            steps.append({"tool": event["tool"], "args": event["args"], "result": event["result"]})
        elif event["type"] == "error":
            raise client.LLMError(event["message"])
    return {"answer": answer.strip(), "steps": steps}
