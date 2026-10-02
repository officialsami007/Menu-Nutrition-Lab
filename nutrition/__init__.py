"""Starbucks menu nutrition analysis.

The pipeline is: loading (read and clean a CSV) -> processing (enrich, then analyse) ->
visualization / summarization. `load_menu` runs the first two steps for one file.
"""
from .config import DEFAULT_FILES
from .loading import load_csv
from .processing import enrich


def load_menu(source, kind: str, filename: str | None = None):
    """Load, clean and enrich one CSV. Returns (DataFrame, cleaning report)."""
    df, report = load_csv(source, kind)
    report["filename"] = filename or getattr(source, "name", kind)
    return enrich(df, kind), report


def load_default_menu() -> dict:
    """The provided drinks and food files: {'drinks': (df, report), 'food': (df, report)}."""
    return {kind: load_menu(path, kind, path.name) for kind, path in DEFAULT_FILES.items()}
