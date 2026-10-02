"""Small DataFrame helpers shared by the processing modules."""
import pandas as pd

from ..config import METRICS


def metrics_in(df: pd.DataFrame) -> list[str]:
    """Nutrient columns present in this DataFrame with at least one value, in METRICS order."""
    return [m for m in METRICS if m in df and df[m].notna().any()]


def num(value, digits: int = 1):
    """Round for display/JSON; NaN becomes None."""
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame rows as JSON-safe dicts (NaN becomes None)."""
    clean = df.astype(object).where(df.notna(), None)
    return clean.to_dict(orient="records")
