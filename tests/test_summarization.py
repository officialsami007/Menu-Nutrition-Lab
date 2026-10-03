import json
from types import SimpleNamespace

import groq
import httpx
import pytest

import app as web
from nutrition import load_default_menu
from nutrition.summarization import answer_question, client, questions


@pytest.fixture
def http():
    with web.app.test_client() as test_client:
        yield test_client


@pytest.fixture(scope="module")
def menu():
    return {k: df for k, (df, _) in load_default_menu().items()}


def text_chunks(*pieces):
    """The chunks Groq streams for a plain answer, plus an empty one like the usage chunk it sometimes ends with."""
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p, tool_calls=None))]) for p in pieces]
    return chunks + [SimpleNamespace(choices=[])]


def call_chunks(name, arguments, call_id="call_1"):
    """The chunks Groq streams for one tool call: the name comes first, the arguments in two pieces."""
    half = len(arguments) // 2

    def chunk(function, id=None):
        call = SimpleNamespace(index=0, id=id, function=function)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))])

    return [chunk(SimpleNamespace(name=name, arguments=arguments[:half]), call_id),
            chunk(SimpleNamespace(name=None, arguments=arguments[half:]))]


def scripted_groq(*steps):
    """A stand-in Groq client that plays the steps in order. A step is an Exception (raised), a list of
    chunks (a streamed reply) or a message (a reply that is not streamed). `calls` records each request."""
    calls, queue = [], iter(steps)

    def create(**kwargs):
        calls.append(kwargs)
        step = next(queue)
        if isinstance(step, Exception):
            raise step
        if kwargs.get("stream"):
            return iter(step)
        return SimpleNamespace(choices=[SimpleNamespace(message=step)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def bad_request(code, message="Tool call validation failed: limit above maximum"):
    response = httpx.Response(400, request=httpx.Request("POST", "http://groq.test"))
    return groq.BadRequestError("Error code: 400", response=response, body={"error": {"code": code, "message": message}})


def run(menu, monkeypatch, fake, question="Which drinks have caffeine?", history=None, memory=None):
    """All events of one streamed answer."""
    monkeypatch.setattr(client, "get_client", lambda: fake)
    reports = {k: {"rows_read": 1, "rows_kept": 1} for k in menu}
    return list(questions.stream_answer(question, history or [], memory, menu, reports))


def answer_in(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


def kinds(events):
    return [e["type"] for e in events]


def test_question_streams_a_tool_call_then_the_answer(http, monkeypatch):
    """The tool call arrives in pieces; it is stitched, run against the data, and the answer streams after it."""
    fake, _ = scripted_groq(call_chunks("aggregate", '{"dataset": "drinks", "metric": "calories", "stat": "mean"}'),
                            text_chunks("Drinks average ", "**138.6 kcal**."))
    monkeypatch.setattr(client, "get_client", lambda: fake)
    res = http.post("/api/ask", json={"question": "Average drink calories?"})
    events = [json.loads(line) for line in res.get_data(as_text=True).splitlines()]
    assert res.mimetype == "application/x-ndjson"
    assert kinds(events) == ["step", "text", "text"]
    assert events[0]["tool"] == "aggregate" and events[0]["result"]["drinks"]["value"] == 138.6
    assert answer_in(events) == "Drinks average **138.6 kcal**."


def test_missing_api_key_gives_clear_message(http, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    res = http.post("/api/ask", json={"question": "hi"})
    assert res.status_code == 502 and "GROQ_API_KEY" in res.get_json()["error"]


def test_tool_errors_are_returned_to_the_model(menu):
    result = questions.run_tool("aggregate", {"dataset": "drinks", "metric": "caffeine", "stat": "mean"}, menu)
    assert "not available" in result["error"]


def test_tool_filters_are_translated(menu):
    filters = questions.to_filters({"caffeinated": True, "at_most": {"calories": 100}, "under": {"fat": 5}, "name_contains": "tea"})
    assert filters == {"search": "tea", "category": None, "caffeine": "yes", "calories_max": 100, "fat_below": 5}
    result = questions.run_tool("rank_items", {"dataset": "drinks", "metric": "calories", "order": "lowest",
                                               "filters": {"caffeinated": True}}, menu)
    assert "estimated" in result["note"]


def fake_stream(captured, *pieces):
    """A stand-in Groq client whose completion streams the given text pieces."""
    def create(**kwargs):
        captured.update(kwargs)
        return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p))]) for p in pieces]
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_summary_streams_text_built_from_the_data(http, monkeypatch):
    sent = {}
    monkeypatch.setattr(client, "get_client", lambda: fake_stream(sent, "## Key ", "takeaways"))
    res = http.post("/api/summary", json={"focus": "sugar"})
    assert res.status_code == 200 and res.get_data(as_text=True) == "## Key takeaways"
    system, user = sent["messages"][0]["content"], sent["messages"][1]["content"]
    assert sent["stream"] is True and "sugar" in user.lower()
    # The real statistics are in the prompt: highest-carb drink and highest-calorie food item.
    assert "Cinnamon Dolce Frappuccino® Blended Coffee: 64 g" in system
    assert '"nutrient":"carbs (g)","drinks_avg":24.3,"food_avg":41.5,"higher":"food"' in system
    assert '"missing":["Sugar","Caffeine"]' in system


def test_summary_without_key_gives_clear_message(http, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    res = http.post("/api/summary", json={"focus": "overview"})
    assert res.status_code == 502 and "GROQ_API_KEY" in res.get_json()["error"]


def test_follow_up_questions_include_earlier_turns(menu, monkeypatch):
    fake, calls = scripted_groq(text_chunks("Food: 16.4 g."))
    history = [{"role": "user", "content": "Average fat in drinks?"}, {"role": "assistant", "content": "2.7 g."}]
    run(menu, monkeypatch, fake, "And for food?", history)
    assert [m["content"] for m in calls[0]["messages"][1:]] == ["Average fat in drinks?", "2.7 g.", "And for food?"]


def test_tool_calling_stops_after_max_rounds(menu, monkeypatch):
    """A model that never stops calling tools is cut off and asked to answer."""
    find = '{"dataset": "drinks", "text": "latte"}'
    rounds = questions.MAX_TOOL_ROUNDS
    fake, calls = scripted_groq(*[call_chunks("find_items", find) for _ in range(rounds)], text_chunks("Done."))
    events = run(menu, monkeypatch, fake)
    assert kinds(events).count("step") == rounds and answer_in(events) == "Done." and len(calls) == rounds + 1
    assert "tools" not in calls[-1]


def test_list_tool_lists_the_caffeinated_drinks(menu):
    result = questions.run_tool("list_items", {"dataset": "drinks", "filters": {"caffeinated": True}}, menu)
    assert result["drinks"]["total"] == 62 and len(result["drinks"]["items"]) == 62
    assert "estimated" in result["note"]


def test_text_before_a_tool_call_is_dropped(menu, monkeypatch):
    spoken = text_chunks("Let me check. ")[:-1] + call_chunks("aggregate", '{"dataset": "drinks", "metric": "fat", "stat": "mean"}')
    events = run(menu, monkeypatch, scripted_groq(spoken, text_chunks("2.7 g."))[0])
    assert kinds(events) == ["text", "reset", "step", "text"]
    assert answer_in(events[events.index({"type": "reset"}):]) == "2.7 g."


def test_invalid_tool_call_is_retried_with_the_reason(menu, monkeypatch):
    """Groq rejects a call whose arguments break the schema; the model is told why and tries again."""
    fake, calls = scripted_groq(bad_request("tool_use_failed"), text_chunks("There are 62 caffeinated drinks."))
    events = run(menu, monkeypatch, fake)
    assert answer_in(events) == "There are 62 caffeinated drinks." and len(calls) == 2
    note = calls[1]["messages"][-1]["content"]
    assert "rejected" in note and "limit above maximum" in note


def test_other_groq_errors_are_not_retried(menu, monkeypatch):
    fake, calls = scripted_groq(bad_request("invalid_request"))
    events = run(menu, monkeypatch, fake)
    assert kinds(events) == ["error"] and "400" in events[0]["message"] and len(calls) == 1


def test_an_invalid_tool_call_twice_gives_a_friendly_error(menu, monkeypatch):
    events = run(menu, monkeypatch, scripted_groq(bad_request("tool_use_failed"), bad_request("tool_use_failed"))[0])
    assert kinds(events) == ["error"]


def test_a_failure_after_text_was_sent_is_not_retried(menu, monkeypatch):
    """Retrying would make the answer start twice on screen."""
    def breaks_midway():
        yield text_chunks("Part of an answer")[0]
        raise bad_request("tool_use_failed")

    fake, calls = scripted_groq(breaks_midway())
    events = run(menu, monkeypatch, fake)
    assert kinds(events) == ["text", "error"] and len(calls) == 1


def test_an_empty_reply_is_retried_once(menu, monkeypatch):
    fake, calls = scripted_groq(text_chunks(""), text_chunks("Here you go."))
    assert answer_in(run(menu, monkeypatch, fake)) == "Here you go." and len(calls) == 2
    fake, calls = scripted_groq(text_chunks(""), text_chunks(""))
    assert answer_in(run(menu, monkeypatch, fake)) == "No answer was returned. Try rephrasing." and len(calls) == 2


def test_only_recent_history_is_sent_and_long_turns_are_cut(menu, monkeypatch):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 2000} for i in range(10)]
    fake, calls = scripted_groq(SimpleNamespace(content="notes"), text_chunks("ok"))  # the first reply folds the older turns
    run(menu, monkeypatch, fake, history=history)
    sent = calls[1]["messages"]
    assert [m["role"] for m in sent] == ["system"] + ["user", "assistant"] * 3 + ["user"]
    assert sent[1]["content"].startswith("turn 4") and len(sent[1]["content"]) == questions.HISTORY_CHARS
    assert "do not list names unless" in sent[0]["content"]


def chat_of(turns):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"} for i in range(turns)]


def test_older_messages_are_folded_into_notes_once(menu, monkeypatch):
    """Messages that fall out of the recent window become running notes, and are not folded again."""
    fake, calls = scripted_groq(SimpleNamespace(content="User asked about caffeine; 62 drinks flagged."), text_chunks("Sure."))
    events = run(menu, monkeypatch, fake, history=chat_of(10))
    assert events[0] == {"type": "memory", "notes": "User asked about caffeine; 62 drinks flagged.", "covered": 4}
    transcript = calls[0]["messages"][-1]["content"]
    assert "message 0" in transcript and "message 3" in transcript and "message 4" not in transcript
    assert "Notes on the earlier conversation: User asked about caffeine" in calls[1]["messages"][0]["content"]

    fake, calls = scripted_groq(text_chunks("Sure."))
    events = run(menu, monkeypatch, fake, history=chat_of(10), memory={"notes": "known", "covered": 4})
    assert "memory" not in kinds(events) and len(calls) == 1
    assert "Notes on the earlier conversation: known" in calls[0]["messages"][0]["content"]


def test_a_very_long_chat_only_folds_a_bounded_batch(menu, monkeypatch):
    fake, calls = scripted_groq(SimpleNamespace(content="notes"), text_chunks("ok"))
    events = run(menu, monkeypatch, fake, history=chat_of(40))
    transcript = calls[0]["messages"][-1]["content"].split("New messages:\n")[1]
    assert len(transcript.splitlines()) == questions.FOLD_AT_MOST and events[0]["covered"] == 34


def test_the_command_line_gets_the_whole_answer_and_the_steps(menu, monkeypatch):
    fake, _ = scripted_groq(call_chunks("aggregate", '{"dataset": "food", "metric": "protein", "stat": "mean"}'), text_chunks("About ", "11.5 g."))
    monkeypatch.setattr(client, "get_client", lambda: fake)
    result = answer_question("Average protein in food?", [], menu, {k: {"rows_read": 1, "rows_kept": 1} for k in menu})
    assert result["answer"] == "About 11.5 g." and result["steps"][0]["tool"] == "aggregate"
    monkeypatch.setattr(client, "get_client", lambda: scripted_groq(bad_request("invalid_request"))[0])
    with pytest.raises(client.LLMError, match="400"):
        answer_question("x", [], menu, {k: {"rows_read": 1, "rows_kept": 1} for k in menu})


def test_list_tool_shows_the_nutrient_it_was_filtered_on(menu):
    result = questions.run_tool("list_items", {"dataset": "food", "filters": {"at_least": {"protein": 32}}}, menu)
    assert result["food"]["total"] == 2 and all("(protein: " in item for item in result["food"]["items"])


