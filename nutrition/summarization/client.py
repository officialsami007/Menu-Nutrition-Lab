"""Groq connection and user-facing error messages."""
import os

import groq

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
