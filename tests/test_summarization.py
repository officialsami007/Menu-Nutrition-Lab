from types import SimpleNamespace

import pytest

import app as web
from nutrition import load_default_menu
from nutrition.summarization import client, questions


@pytest.fixture
def http():
    with web.app.test_client() as test_client:
        yield test_client


@pytest.fixture(scope="module")
def menu():
    return {k: df for k, (df, _) in load_default_menu().items()}


def fake_groq(*messages):
    """A stand-in Groq client that returns the given assistant messages in order."""
    replies = iter(messages)
    create = lambda **_: SimpleNamespace(choices=[SimpleNamespace(message=next(replies))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_question_runs_tools_against_the_data(http, monkeypatch):
    """The model first asks for a tool, then answers using the tool result."""
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="aggregate", arguments='{"dataset": "drinks", "metric": "calories", "stat": "mean"}'),
        model_dump=lambda: {"id": "call_1", "type": "function", "function": {"name": "aggregate", "arguments": "{}"}},
    )
    monkeypatch.setattr(client, "get_client", lambda: fake_groq(
        SimpleNamespace(content="", tool_calls=[call]),
        SimpleNamespace(content="Drinks average **138.6 kcal**.", tool_calls=None),
    ))
    data = http.post("/api/ask", json={"question": "Average drink calories?"}).get_json()
    assert data["answer"] == "Drinks average **138.6 kcal**."
    assert data["steps"][0]["result"]["drinks"]["value"] == 138.6


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


def test_follow_up_questions_include_earlier_turns(http, monkeypatch):
    sent = {}
    def create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Food: 16.4 g.", tool_calls=None))])
    monkeypatch.setattr(client, "get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    history = [{"role": "user", "content": "Average fat in drinks?"}, {"role": "assistant", "content": "2.7 g."}]
    http.post("/api/ask", json={"question": "And for food?", "history": history})
    assert [m["content"] for m in sent["messages"][1:]] == ["Average fat in drinks?", "2.7 g.", "And for food?"]


def test_tool_calling_stops_after_max_rounds(menu, monkeypatch):
    """A model that never stops calling tools is cut off and asked to answer."""
    call = SimpleNamespace(id="c", function=SimpleNamespace(name="find_items", arguments='{"dataset": "drinks", "text": "latte"}'),
                           model_dump=lambda: {"id": "c", "type": "function", "function": {"name": "find_items", "arguments": "{}"}})
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        final = "tools" not in kwargs
        msg = SimpleNamespace(content="Done." if final else "", tool_calls=None if final else [call])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(client, "get_client", lambda: fake)
    result = questions.answer_question("loop", [], menu, {k: {"rows_read": 1, "rows_kept": 1} for k in menu})
    rounds = questions.MAX_TOOL_ROUNDS
    assert result["answer"] == "Done." and len(result["steps"]) == rounds and len(calls) == rounds + 1
