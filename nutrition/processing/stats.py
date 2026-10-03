import pandas as pd

from ..config import LEVEL_THRESHOLDS, LIMIT_NUTRIENTS, METRICS, REFERENCE_INTAKE
from .enrich import caffeine_source
from .tables import metrics_in, num

# kcal per gram, used to work out where an item's calories come from.
KCAL_PER_GRAM = {"fat": 9, "carbs": 4, "protein": 4}

# (from, up to but not including, label) for grouping items by calories.
CALORIE_BANDS = [(0, 150, "Under 150 kcal"), (150, 300, "150–299 kcal"), (300, 450, "300–449 kcal"), (450, float("inf"), "450+ kcal")]


def describe(df: pd.DataFrame) -> dict:
    """Totals, mean, median, range and the highest/lowest item for every nutrient, plus menu-level ratios."""
    metrics = metrics_in(df)
    per_metric = {}
    for m in metrics:
        col = df[m].dropna()
        top, low = df.loc[[col.idxmax()]].iloc[0], df.loc[[col.idxmin()]].iloc[0]
        per_metric[m] = {
            "total": num(col.sum()),
            "mean": num(col.mean()),
            "median": num(col.median()),
            "min": num(col.min()),
            "max": num(col.max()),
            "std": num(col.std()),
            "count": int(col.count()),
            "intake": intake(m, num(col.mean())),
            "highest": {"name": top["name"], "value": num(top[m])},
            "lowest": {"name": low["name"], "value": num(low[m])},
        }

    stats = {"items": int(len(df)), "metrics": metrics, "per_metric": per_metric}

    # Fat-to-protein ratio across the whole menu (total fat / total protein). A mean of per-item
    # ratios would be distorted by items with almost no protein, so the median is given alongside.
    if {"fat", "protein"} <= set(metrics):
        both = df[["fat", "protein"]].dropna()
        stats["fat_protein_ratio"] = num(both["fat"].sum() / both["protein"].sum(), 2) if both["protein"].sum() else None
        stats["fat_protein_ratio_median"] = num(df["fat_protein_ratio"].median(), 2) if "fat_protein_ratio" in df else None

    if set(KCAL_PER_GRAM) <= set(metrics):
        energy = macro_energy(df)
        total = energy.sum()
        stats["macro_energy_share"] = {m: num(v / total * 100) for m, v in energy.items()} if total else None

    # The provided files have no sugar column; carbs are the closest available measure.
    stats["sweetness_metric"] = "sugar" if "sugar" in metrics else "carbs" if "carbs" in metrics else None
    stats["caffeinated_items"] = int(df["caffeinated"].sum()) if "caffeinated" in df else 0
    stats["caffeine_source"] = caffeine_source(df)
    stats["categories"] = df["category"].value_counts().astype(int).to_dict() if "category" in df else {}
    return stats


def intake(metric: str, value) -> dict | None:
    """A value as % of the daily reference intake, with its traffic-light level
    ('low', 'medium', 'high', or None for nutrients that are not limited)."""
    if value is None or metric not in REFERENCE_INTAKE:
        return None
    pct = value / REFERENCE_INTAKE[metric] * 100
    level = None
    if metric in LIMIT_NUTRIENTS:
        level = "low" if pct <= LEVEL_THRESHOLDS["low"] else "medium" if pct <= LEVEL_THRESHOLDS["high"] else "high"
    return {"pct": num(pct), "level": level}


def compare(drinks: pd.DataFrame, food: pd.DataFrame) -> list[dict]:
    """Average of each nutrient both datasets have, with the difference, ratio and which is higher."""
    shared = [m for m in metrics_in(drinks) if m in metrics_in(food)]
    rows = []
    for m in shared:
        d, f = drinks[m].mean(), food[m].mean()
        rows.append({
            "metric": m,
            "label": METRICS[m]["label"],
            "unit": METRICS[m]["unit"],
            "drinks": num(d),
            "food": num(f),
            "difference": num(f - d),
            "ratio": num(f / d, 2) if d else None,
            "higher": "food" if f > d else "drinks" if d > f else "equal",
        })
    return rows


def top_items(df: pd.DataFrame, metric: str, n: int = 5, ascending: bool = False) -> list[dict]:
    """The n highest (or lowest) items for a nutrient as {name, value, category}."""
    if metric not in df:
        return []
    ranked = df.dropna(subset=[metric]).sort_values(metric, ascending=ascending).head(n)
    return [{"name": r["name"], "value": num(r[metric]), "category": r.get("category")} for _, r in ranked.iterrows()]


def macro_energy(df: pd.DataFrame) -> pd.Series:
    """Calories the whole menu gets from fat, carbs and protein (9, 4 and 4 kcal per gram)."""
    return pd.Series({m: df[m].sum() * kcal for m, kcal in KCAL_PER_GRAM.items()})


def calorie_bands(df: pd.DataFrame) -> pd.Series:
    """How many items fall into each calorie range, lightest first. Empty ranges are left out."""
    edges = [band[0] for band in CALORIE_BANDS] + [CALORIE_BANDS[-1][1]]
    labels = [band[2] for band in CALORIE_BANDS]
    counts = pd.cut(df["calories"], bins=edges, labels=labels, right=False).value_counts().reindex(labels, fill_value=0)
    return counts[counts > 0]


def category_means(df: pd.DataFrame, metric: str) -> pd.Series:
    """Average of a nutrient per category, sorted ascending."""
    return df.groupby("category")[metric].mean().round(1).sort_values()
