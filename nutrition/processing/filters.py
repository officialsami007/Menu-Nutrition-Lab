"""Filtering and sorting rows, e.g. "drinks with caffeine" or "food under 500 calories"."""
import re

import pandas as pd

from ..config import METRICS


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Filter rows by search text, category, caffeine and min/max bounds on any nutrient.

    `filters` keys: search, category, caffeine ('yes'/'no'), and '<metric>_min' / '<metric>_max'
    (inclusive) or '<metric>_below' (strictly less, for "food under 500 calories").
    Rows with no value for a bounded nutrient are excluded, since they can't be shown to meet it.
    Invalid values are ignored, so a half-typed number in the UI doesn't break the table.
    """
    out = df
    search = (filters.get("search") or "").strip()
    if search:
        out = out[out["name"].str.contains(re.escape(search), case=False, na=False)]
    category = filters.get("category")
    if category and category != "all" and "category" in out:
        out = out[out["category"] == category]
    caffeine = filters.get("caffeine")
    if caffeine in ("yes", "no") and "caffeinated" in out:
        out = out[out["caffeinated"] == (caffeine == "yes")]
    for metric in METRICS:
        if metric not in out:
            continue
        for bound, op in (("min", "ge"), ("max", "le"), ("below", "lt")):
            raw = filters.get(f"{metric}_{bound}")
            if raw in (None, ""):
                continue
            try:
                limit = float(raw)
            except (TypeError, ValueError):
                continue
            out = out[getattr(out[metric], op)(limit)]
    return out


def sort_rows(df: pd.DataFrame, column: str | None, descending: bool) -> pd.DataFrame:
    """Sort by a column, keeping items with no value at the bottom."""
    if column and column in df:
        return df.sort_values(column, ascending=not descending, na_position="last", kind="stable")
    return df
