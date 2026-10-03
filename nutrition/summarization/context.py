"""What the model is told: the rules it must follow and the statistics it may quote.

Numbers come from nutrition.processing; this module only chooses and formats them.
"""
import json

from ..config import METRICS
from ..processing import category_means, compare, describe

SYSTEM_RULES = """You are a nutrition analyst for the menu data supplied below.
Rules:
- Use only the supplied data and tool results. Never invent items or numbers.
- DATA only contains the drinks and/or food files that were provided. If one is missing, say once that it was not
  provided, analyse only the other, and do not compare drinks with food or guess at the missing one.
- If a nutrient is not in the data, say so plainly. Only when sugar is listed under "missing", use carbs as the
  closest measure and say so; carbs include starch, so call them carbs, not sugar. When sugar is not missing, use the
  real sugar values and never say sugar is reported as carbs.
- When caffeine_source is "data", caffeine comes in mg from the file: never say it is estimated from names.
- If caffeine is "estimated from names", there are no milligram values, so drinks cannot be averaged
  or ranked by caffeine. Say that in one sentence, then give the count of drinks flagged as caffeinated
  and explain the flag is estimated from item names. Keep that answer short and do not list names unless
  the user asks which drinks (or for names, a list or a "top N"); when they do, show the list at once
  instead of offering it. Never call a list "top" or "most caffeinated": the order means nothing. For a
  "top 5" request, say strength cannot be compared and show 5 examples as examples.
- Write in plain English for a general audience. Use Markdown: short headings, bullet points and bold item names.
  Never mention field, tool or data-structure names; just state the facts.
- Always state units (kcal, g, mg).
- Quote numbers exactly as given. Do not do your own arithmetic or comparisons: to compare drinks
  with food, use "drinks compared with food", whose "higher" field already says which is higher."""


# The values a summary talks about next to an item. Kept short: the free Groq tier limits tokens per minute.
ITEM_DETAIL = ("calories", "fat", "carbs", "sugar", "protein")


def item_line(row, metric: str, metrics: list, detail: bool = True) -> str:
    """'Name: 64g carbs (350 kcal, 13g fat, ...)': the ranked value first, then the item's other main values,
    so the model can quote any number about the item instead of borrowing a nearby figure."""
    fmt = lambda m: f"{row[m]:g} kcal" if m == "calories" else f"{row[m]:g}{METRICS[m]['unit']} {METRICS[m]['label'].lower()}"
    others = [fmt(m) for m in ITEM_DETAIL if detail and m in metrics and m != metric and row[m] == row[m]]  # x == x skips NaN
    return f"{row['name']}: {fmt(metric)}" + (f" ({', '.join(others)})" if others else "")


def ranked_items(df, metric: str, metrics: list, n: int, lowest: bool = False, detail: bool = True) -> list[str]:
    """The n highest (or lowest) items for a nutrient, each with its main values when `detail` is set."""
    rows = df.dropna(subset=[metric]).sort_values(metric, ascending=lowest).head(n)
    return [item_line(row, metric, metrics, detail) for _, row in rows.iterrows()]


def facts(frames: dict, reports: dict, detailed: bool = True) -> str:
    """Compact JSON of statistics for the prompt.

    The free Groq tier allows ~8k tokens a minute, so this stays small: averages and ranges
    always, plus top items and category averages only for summaries (questions use tools instead).
    """
    pack = {}
    for kind, df in frames.items():
        stats = describe(df)
        metrics = stats["metrics"]
        info = {
            "items": stats["items"],
            "missing": [spec["label"] for m, spec in METRICS.items() if m not in metrics],
            "caffeinated_items": stats["caffeinated_items"] if kind == "drinks" else None,
            "caffeine_source": "data" if stats["caffeine_source"] == "column" else "estimated from names",
            # metric: [mean, median, min, max]
            "mean_median_min_max": {f"{m} ({METRICS[m]['unit']})": [s["mean"], s["median"], s["min"], s["max"]]
                                    for m, s in stats["per_metric"].items()},
            "fat_to_protein_ratio": stats.get("fat_protein_ratio"),
            "calorie_share_pct": stats.get("macro_energy_share"),
            "rows_removed_while_cleaning": reports[kind]["rows_read"] - reports[kind]["rows_kept"],
        }
        if detailed:
            # Calories and sugar (or carbs) are what summaries are asked about most, so they get five items;
            # those and protein carry each item's other values, the rest just the ranked figure.
            main = [m for m in ("calories", stats["sweetness_metric"]) if m in metrics]
            info["highest"] = {m: ranked_items(df, m, metrics, 5 if m in main else 3, detail=m in (*main, "protein"))
                               for m in metrics}
            info["lowest"] = {m: ranked_items(df, m, metrics, 5, lowest=True) for m in main}
            if "calories" in metrics:
                by_category = category_means(df, "calories").round().astype(int)  # sorted lowest first
                info["avg_calories_by_category"] = by_category.to_dict()
                # Named outright: models misread which entry of the list is the largest.
                info["heaviest_category"] = f"{by_category.index[-1]} ({by_category.iloc[-1]} kcal avg)"
                info["lightest_category"] = f"{by_category.index[0]} ({by_category.iloc[0]} kcal avg)"
        pack[kind] = info
    if {"drinks", "food"} <= set(frames):
        pack["drinks compared with food"] = [
            {"nutrient": f"{r['metric']} ({r['unit']})", "drinks_avg": r["drinks"], "food_avg": r["food"], "higher": r["higher"],
             # e.g. 2.94 with higher "drinks": drinks average 2.94 times food. Always >= 1, so it reads one way only.
             "times_higher": round(max(r["drinks"], r["food"]) / min(r["drinks"], r["food"]), 2) if min(r["drinks"], r["food"]) else None}
            for r in compare(frames["drinks"], frames["food"])
        ]
    return json.dumps(pack, ensure_ascii=False, separators=(",", ":"), default=str)
