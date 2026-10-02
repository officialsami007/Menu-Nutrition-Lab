"""Part 2 and Bonus 1 of the brief: LLM features on top of the processed data (Groq)."""
from .client import LLMError
from .questions import answer_question
from .summary import FOCUS_PROMPTS, stream_summary
