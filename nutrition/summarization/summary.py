"""Part 2 of the brief: a written summary of the menu's nutrition, streamed as it is generated."""
from . import client
from .context import SYSTEM_RULES, facts

FOCUS_PROMPTS = {
    "overview": "Give a balanced overview of the whole menu.",
    "sugar": "Focus on sugar and sweetness: which items are highest and lowest, and how drinks compare with food.",
    "calories": "Focus on calories and calorie density: the heaviest and lightest items and categories.",
    "protein": "Focus on protein and fat: best protein sources, fat-to-protein balance, and lean options.",
}


def stream_summary(frames: dict, reports: dict, focus: str = "overview"):
    """Return a generator of text chunks. Raises LLMError before streaming if setup fails."""
    groq_client = client.get_client()
    messages = [
        {"role": "system", "content": SYSTEM_RULES + "\n\nDATA:\n" + facts(frames, reports)},
        {"role": "user", "content": (
            f"{FOCUS_PROMPTS.get(focus, FOCUS_PROMPTS['overview'])}\n"
            "Write a nutrition summary of at most 300 words with these sections: "
            "'Key takeaways' (3 bullets), 'Drinks', 'Food', 'Drinks vs food', 'Lighter choices'. "
            "Cite specific items with their numbers."
        )},
    ]
    try:
        stream = groq_client.chat.completions.create(messages=messages, stream=True, temperature=0.2, **client.MODEL_OPTIONS)
    except Exception as exc:
        raise client.friendly_error(exc) from exc

    # The request is made above so setup errors (bad key, rate limit) surface as a normal
    # HTTP error. Errors that happen mid-stream can only be appended to the text itself.
    def chunks():
        try:
            for part in stream:
                text = part.choices[0].delta.content if part.choices else None
                if text:
                    yield text
        except Exception as exc:
            yield f"\n\n*The summary stopped early: {client.friendly_error(exc)}*"

    return chunks()
