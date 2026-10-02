"""Bonus 1 of the brief: answer free-text questions about the menu.

Uses tool calling: the model asks for exact figures through three tools, each of which maps
onto a function in nutrition.processing.queries. This module only translates between the
model's JSON and those functions.
"""
import json

from ..config import METRICS
from ..processing import queries
from . import client
from .context import SYSTEM_RULES, facts

MAX_TOOL_ROUNDS = 4

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

TOOLS = [
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
        "description": "List items sorted by a nutrient, highest or lowest first. Items tied at the cut-off are included.",
        "parameters": {"type": "object", "properties": {
            "dataset": _DATASET, "metric": _METRIC, "order": {"type": "string", "enum": ["highest", "lowest"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25}, "filters": _FILTERS,
        }, "required": ["dataset", "metric", "order"]},
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


def answer_question(question: str, history: list, frames: dict, reports: dict) -> dict:
    """Answer a free-text question. Returns {'answer': markdown, 'steps': [tool calls made]}."""
    groq_client = client.get_client()
    categories = {k: sorted(df["category"].unique().tolist()) for k, df in frames.items()}
    messages = [{"role": "system", "content": (
        SYSTEM_RULES
        + "\nUse the tools for any exact figure not already in DATA. Keep answers under 180 words."
        + "\nFilters: 'under' or 'less than' means the 'under' filter; 'at most' or 'up to' means 'at_most'."
        + "\nList every item a tool returns for a ranking, including ties."
        + f"\nCategories: {json.dumps(categories, ensure_ascii=False)}"
        + "\n\nDATA:\n" + facts(frames, reports, detailed=False)
    )}]
    # A few earlier turns let follow-up questions ("and for food?") make sense.
    for turn in (history or [])[-4:]:
        if turn.get("role") in ("user", "assistant") and isinstance(turn.get("content"), str):
            messages.append({"role": turn["role"], "content": turn["content"][:800]})
    messages.append({"role": "user", "content": question})

    # Tool-calling loop: the model either answers, or asks for one or more tool calls. We run
    # each call with pandas, append the result to the conversation and let the model continue.
    # MAX_TOOL_ROUNDS stops a model that keeps calling tools from looping forever.
    steps = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            reply = groq_client.chat.completions.create(
                messages=messages, tools=TOOLS, tool_choice="auto", temperature=0.2, **client.MODEL_OPTIONS,
            ).choices[0].message
            if not reply.tool_calls:
                return {"answer": (reply.content or "").strip() or "No answer was returned. Try rephrasing.", "steps": steps}
            messages.append({"role": "assistant", "content": reply.content or "",
                             "tool_calls": [tc.model_dump() for tc in reply.tool_calls]})
            for call in reply.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                    output = run_tool(call.function.name, args, frames)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    args, output = {}, {"error": f"Invalid tool call: {exc}"}
                steps.append({"tool": call.function.name, "args": args, "result": output})
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(output, ensure_ascii=False, separators=(",", ":"), default=str)})
        final = groq_client.chat.completions.create(
            messages=messages + [{"role": "user", "content": "Answer now with the data gathered."}],
            temperature=0.2, **client.MODEL_OPTIONS,
        ).choices[0].message
        return {"answer": (final.content or "").strip(), "steps": steps}
    except Exception as exc:
        raise client.friendly_error(exc) from exc
