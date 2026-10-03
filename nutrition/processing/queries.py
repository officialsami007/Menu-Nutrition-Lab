"""Exact answers to common questions (averages, rankings, look-ups) across one or both datasets.

The LLM question feature calls these through tool calling, so every number it quotes is
calculated here with pandas rather than by the model.
"""
from .filters import apply_filters
from .tables import metrics_in, num, records

STATS = ("mean", "median", "sum", "min", "max", "count")


class QueryError(ValueError):
    """A query that can't be answered from the data, e.g. a nutrient the file doesn't have."""


def select(frames: dict, dataset: str, metric: str | None = None) -> dict:
    """Pick 'drinks', 'food' or 'both'; with a metric, keep only datasets that have it."""
    if dataset == "both":
        chosen = dict(frames)
    elif dataset in frames:
        chosen = {dataset: frames[dataset]}
    elif dataset in ("drinks", "food"):
        raise QueryError(f"No {dataset} file was provided, so there is no {dataset} data.")
    else:
        raise QueryError("Unknown dataset. Use drinks, food or both.")
    if metric:
        chosen = {k: df for k, df in chosen.items() if metric in metrics_in(df)}
        if not chosen:
            raise QueryError(f"{metric} is not available in {dataset}.")
    return chosen


def aggregate(frames: dict, dataset: str, metric: str, stat: str = "mean",
              by_category: bool = False, filters: dict | None = None) -> dict:
    """One statistic (mean, sum, max...) of a nutrient, optionally per category or after filtering."""
    if stat not in STATS:
        raise QueryError(f"Unknown statistic '{stat}'. Use one of {', '.join(STATS)}.")
    result = {}
    for kind, df in select(frames, dataset, metric).items():
        df = apply_filters(df, filters or {})
        if by_category:
            result[kind] = df.groupby("category")[metric].agg(stat).round(1).to_dict()
        else:
            result[kind] = {"value": num(df[metric].agg(stat)), "items_used": int(df[metric].count())}
    return result


def rank(frames: dict, dataset: str, metric: str, lowest: bool = False,
         limit: int = 5, filters: dict | None = None) -> dict:
    """Items sorted by a nutrient. Items tied with the last one shown are included,
    so "the highest" never hides an equal item."""
    result = {}
    for kind, df in select(frames, dataset, metric).items():
        ranked = apply_filters(df, filters or {}).dropna(subset=[metric]).sort_values(metric, ascending=lowest)
        if len(ranked):
            cutoff = ranked[metric].iloc[min(limit, len(ranked)) - 1]
            ranked = ranked[ranked.index.isin(ranked.head(limit).index) | (ranked[metric] == cutoff)]
        result[kind] = records(ranked[["name", "category"] + metrics_in(df)])
    return result


def list_items(frames: dict, dataset: str, filters: dict | None = None, limit: int = 100, show: tuple = ()) -> dict:
    """Names of the items that pass the filters, plus how many matched in total.

    `show` names nutrients whose value is written after each name, for questions like "more than 30 g
    of protein". Without it the model would have names only and could guess the numbers.
    """
    result = {}
    for kind, df in select(frames, dataset).items():
        matches = apply_filters(df, filters or {}).head(limit)
        shown = [m for m in show if m in df]
        labels = [name + "".join(f" ({m}: {num(row[m])})" for m in shown) for name, row in zip(matches["name"], matches.to_dict("records"))]
        result[kind] = {"total": len(apply_filters(df, filters or {})), "items": labels}
    return result


def find(frames: dict, dataset: str, text: str, limit: int = 10) -> dict:
    """Full nutrition for items whose name contains `text`."""
    result = {}
    for kind, df in select(frames, dataset).items():
        hits = df[df["name"].str.contains(text, case=False, regex=False)].head(limit)
        result[kind] = records(hits[["name", "category"] + metrics_in(df)])
    return result


def pool_sizes(frames: dict, dataset: str, filters: dict | None = None) -> dict:
    """Items per dataset before and after the filters, so an answer can show its working."""
    try:
        return {kind: {"all": len(df), "kept": len(apply_filters(df, filters or {}))}
                for kind, df in select(frames, dataset).items()}
    except QueryError:
        return {}


def any_estimated_caffeine(frames: dict) -> bool:
    """True when caffeine comes from item names rather than real values, so answers can say so."""
    return any("caffeine" not in metrics_in(df) for df in frames.values())

