import os
from collections.abc import Iterator
from typing import Any

import groq
from groq.types.chat import ChatCompletionMessageParam as Message
from groq.types.chat import ChatCompletionToolParam as Tool

from ..config import GROQ_MODEL

# Low reasoning effort keeps answers fast; reasoning text is not returned to the user.
MODEL_OPTIONS = {"model": GROQ_MODEL, "reasoning_effort": "low", "include_reasoning": False}


class LLMError(RuntimeError):
    """A problem talking to the LLM, worded for the person using the app."""


def get_client() -> groq.Groq:
    """A Groq client using the key from .env, or a clear error if the key is missing."""
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise LLMError("No Groq API key found. Add GROQ_API_KEY to the .env file and restart the app.")
    return groq.Groq(api_key=key, max_retries=2, timeout=60)


def _rejected_tool_call(exc: groq.APIError) -> str | None:
    """Why Groq refused the model's tool call (error code 'tool_use_failed'), or None for any other 400."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    if isinstance(error, dict) and error.get("code") == "tool_use_failed":
        return str(error.get("message", "")).removeprefix("Tool call validation failed: ")[:300]
    return None


def complete(groq_client: groq.Groq, messages: list[Message], **options: Any):
    """One chat completion, returning the model's message.

    Groq checks the model's tool call against our schema and answers 400 'tool_use_failed' when
    the call is invalid (for example a limit above the maximum). That is the model's slip, so it is
    told what was wrong and gets one more try instead of the person seeing an error.
    """
    try:
        return groq_client.chat.completions.create(messages=messages, **options, **MODEL_OPTIONS).choices[0].message
    except groq.BadRequestError as exc:
        reason = _rejected_tool_call(exc)
        if reason is None:
            raise
        retry: list[Message] = [*messages, {"role": "user", "content": f"That tool call was rejected: {reason} Call the tool again with valid arguments."}]
        return groq_client.chat.completions.create(messages=retry, **options, **MODEL_OPTIONS).choices[0].message


def stream_completion(groq_client: groq.Groq, messages: list[Message], **options: Any) -> Iterator[tuple[str, Any]]:
    """Stream one completion as ("text", chunk) pairs, then a last ("tool_calls", [...]) pair.

    The list is empty when the model simply answered. Tool-call arguments arrive in pieces, so they are
    stitched together by index. An invalid tool call is retried once, like in `complete`, but only if no
    text has gone out yet; otherwise the person would see the answer start twice.
    """
    for attempt in range(2):
        sent_text = False
        try:
            stream = groq_client.chat.completions.create(messages=messages, stream=True, **options, **MODEL_OPTIONS)
            pieces: dict[int, dict[str, str]] = {}
            for part in stream:
                if not part.choices:
                    continue
                delta = part.choices[0].delta
                if delta.content:
                    sent_text = True
                    yield "text", delta.content
                for call in delta.tool_calls or []:
                    slot = pieces.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = call.id or slot["id"]
                    if call.function:
                        slot["name"] = slot["name"] or call.function.name or ""  # the name comes once, the arguments in pieces
                        slot["arguments"] += call.function.arguments or ""
            yield "tool_calls", [pieces[i] for i in sorted(pieces)]
            return
        except groq.APIError as exc:
            reason = _rejected_tool_call(exc)
            if reason is None or sent_text or attempt == 1:
                raise
            messages = [*messages, {"role": "user", "content": f"That tool call was rejected: {reason} Call the tool again with valid arguments."}]


def friendly_error(exc: Exception) -> LLMError:
    """Turn a Groq exception into a message the person using the app can act on."""
    if isinstance(exc, groq.AuthenticationError):
        return LLMError("Groq rejected the API key. Check GROQ_API_KEY in the .env file.")
    if isinstance(exc, groq.RateLimitError):
        return LLMError("Groq's rate limit was reached. Wait a minute and try again.")
    if isinstance(exc, groq.APIConnectionError):
        return LLMError("Couldn't reach Groq. Check your internet connection and try again.")
    if isinstance(exc, groq.APIStatusError):
        return LLMError(f"Groq returned an error ({exc.status_code}). Try again shortly.")
    return LLMError(f"The AI request failed: {exc}")
