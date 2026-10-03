"""What the model is told: the rules it must follow and the statistics it may quote.

Numbers come from nutrition.processing; this module only chooses and formats them.
"""
import json

from ..config import METRICS
from ..processing import category_means, compare, describe, top_items

SYSTEM_RULES = """You are a nutrition analyst for the menu data supplied below.
Rules:
- Use only the supplied data and tool results. Never invent items or numbers.
- DATA only contains the drinks and/or food files that were provided. If one is missing, say once that it was not
  provided, analyse only the other, and do not compare drinks with food or guess at the missing one.
- If a nutrient is not in the data, say so plainly. For sugar, use carbs as the closest measure and say so;
  carbs include starch, so call them carbs, not sugar or sweetness.
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


def facts(frames: dict, reports: dict, detailed: bool = True) -> str:
    """Compact JSON of statistics for the prompt.

    The free Groq tier allows ~8k tokens a minute, so this stays small: averages and ranges
    always, plus top items and category averages only for summaries (questions use tools instead).
    """
    pack = {}
    for kind, df in frames.items():
        unit = lambda m: METRICS[m]["unit"]
        ranked = lambda m, lowest=False: [f"{t['name']}: {t['value']:g} {unit(m)}" for t in top_items(df, m, 3, lowest)]
        stats = describe(df)
        metrics = stats["metrics"]
        info = {
            "items": stats["items"],
            "missing": [METRICS[m]["label"] for m in ("sugar", "caffeine") if m not in metrics],
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
            info["highest3"] = {m: ranked(m) for m in metrics}
            info["lowest3"] = {m: ranked(m, lowest=True) for m in ("calories", stats["sweetness_metric"]) if m in metrics}
            if "calories" in metrics:
                info["avg_calories_by_category"] = category_means(df, "calories").round().astype(int).to_dict()
        pack[kind] = info
    if {"drinks", "food"} <= set(frames):
        pack["drinks compared with food"] = [
            {"nutrient": f"{r['metric']} ({r['unit']})", "drinks_avg": r["drinks"], "food_avg": r["food"], "higher": r["higher"]}
            for r in compare(frames["drinks"], frames["food"])
        ]
    return json.dumps(pack, ensure_ascii=False, separators=(",", ":"), default=str)
