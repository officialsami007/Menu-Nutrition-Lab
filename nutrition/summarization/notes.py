from . import client

NOTES_LIMIT = 900  # characters; keeps the notes cheap to send on every question

INSTRUCTIONS = """You keep short notes on a conversation about a menu, so the chat can continue
without the full transcript. Update the notes with the new messages. Keep what the user asked, what they refer to
(for example "the 62 caffeinated drinks"), the key items and numbers that were given, and anything still unanswered.
Write plain sentences, at most 120 words, with no preamble."""


def fold(groq_client, notes: str, messages: list[dict]) -> str:
    """The notes updated with `messages` (dicts with a role and content)."""
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    request: list[client.Message] = [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": f"Current notes: {notes or '(none yet)'}\n\nNew messages:\n{transcript}"},
    ]
    reply = client.complete(groq_client, request, temperature=0.2)
    return (reply.content or notes).strip()[:NOTES_LIMIT]
