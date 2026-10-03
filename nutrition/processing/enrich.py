"""Adds the columns the analysis needs on top of a cleaned table: category, caffeine flag, ratios."""
import re

import numpy as np
import pandas as pd

# Ordered keyword rules: the first matching rule wins, so specific ones come first.
DRINK_CATEGORIES = [
    ("Frappuccino", r"frappuccino"),
    ("Smoothies", r"smoothie"),
    ("Tea", r"\btea\b|tazo|teavana|chai|matcha"),
    ("Refreshers & juice", r"refresher|juice|limeade|lemonade|evolution fresh|pink drink|violet drink|defense up"),
    ("Brewed & cold brew", r"cold brew|iced coffee|brewed|roast|clover|coffee traveler|misto"),
    ("Espresso drinks", r"espresso|latte|mocha|macchiato|cappuccino|americano|flat white|doubleshot"),
    ("Chocolate & crème", r"chocolate|cr[eè]me|steamed|milk box"),
    ("Sodas", r"\bale\b|soda"),
]
FOOD_CATEGORIES = [
    ("Protein boxes & bowls", r"protein box|protein bowl|bistro box"),
    ("Breakfast sandwiches", r"breakfast|egg bites|& egg|egg & |egg white|egg sandwich|tomatillo wrap"),
    ("Sandwiches & wraps", r"sandwich|panini|wrap|flatbread|foldover|homestyle|salami|cubano"),
    ("Salads", r"salad|tabbouleh"),
    ("Cake pops & cookies", r"cake pop|cookie|toffeedoodle|whoopie"),
    ("Bakery", r"bagel|croissant|muffin|scone|bread|loaf|danish|roll|bun|cake|fritter|doughnut|donut|brownie|bar\b|tart|straw"),
    ("Yogurt, oats & sides", r"yogurt|parfait|oatmeal|fruit|avocado|butter"),
]

# Caffeine is not in the provided files, so when the column is absent we estimate it from
# the item name: tea/espresso-based names always count, then exclusions (decaf, herbal,
# crème-based Frappuccinos...) are applied before the general coffee/tea keywords.
ALWAYS_CAFFEINE = r"chai|green tea|matcha|espresso|refresher"
NO_CAFFEINE = r"decaf|passion|hibiscus|lemon ginger|herbal|cr[eè]me|smoothie|juice|evolution fresh|steamed|milk box|\bale\b|soda|hot chocolate"
HAS_CAFFEINE = r"coffee|espresso|latte|mocha|macchiato|cappuccino|americano|flat white|cold brew|roast|doubleshot|misto|\btea\b|tazo|teavana|chai|matcha|refresher|pink drink|violet drink|energy"


def classify(name: str, rules) -> str:
    """The label of the first rule whose pattern appears in the item name, or 'Other'."""
    lowered = name.lower()
    for label, pattern in rules:
        if re.search(pattern, lowered):
            return label
    return "Other"


def infer_caffeine(name: str) -> bool:
    """Estimate whether a drink has caffeine from its name (no caffeine column in the data)."""
    lowered = name.lower()
    if re.search(ALWAYS_CAFFEINE, lowered):
        return True
    if re.search(NO_CAFFEINE, lowered):
        return False
    return bool(re.search(HAS_CAFFEINE, lowered))


def caffeine_source(df: pd.DataFrame) -> str:
    """'column' when caffeine values came from the file, otherwise 'name' (estimated)."""
    return "column" if "caffeine" in df and bool(df["caffeine"].notna().any()) else "name"


def enrich(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Add category, caffeine flag and derived ratios. Returns a new DataFrame."""
    out = df.copy()
    rules = DRINK_CATEGORIES if kind == "drinks" else FOOD_CATEGORIES
    out["category"] = [classify(str(name), rules) for name in out["name"].tolist()]

    if caffeine_source(out) == "column":
        out["caffeinated"] = out["caffeine"].fillna(0) > 0
    elif kind == "drinks":
        out["caffeinated"] = out["name"].map(infer_caffeine)
    else:
        out["caffeinated"] = False

    if {"fat", "protein"} <= set(out):
        # Ratio is undefined for items with no protein; leave those as NaN.
        out["fat_protein_ratio"] = (out["fat"] / out["protein"].replace(0, np.nan)).round(2)
    if {"protein", "calories"} <= set(out):
        out["protein_per_100kcal"] = (out["protein"] / out["calories"].replace(0, np.nan) * 100).round(1)
    return out
