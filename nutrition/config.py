"""Shared settings: file locations, the nutrient columns we understand, and model defaults."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")  # GROQ_API_KEY and optional overrides
DATASET_DIR = BASE_DIR / "dataset"

DEFAULT_FILES = {
    "drinks": DATASET_DIR / "starbucks-menu-nutrition-drinks.csv",
    "food": DATASET_DIR / "starbucks-menu-nutrition-food.csv",
}

DATASETS = ("drinks", "food")

# The nutrients the app understands. `pattern` is matched against each header after it is
# lower-cased and stripped of everything except letters and digits, so
# " Carb. (g)", "carbs_g" and "Total Carbohydrates" all map to `carbs`.
METRICS = {
    "calories": {"label": "Calories", "unit": "kcal", "pattern": r"^(calories?|kcal|energy)(kcal)?$"},
    "fat": {"label": "Fat", "unit": "g", "pattern": r"^(total)?fats?(g)?$"},
    "carbs": {"label": "Carbs", "unit": "g", "pattern": r"^(total)?carb(s|ohydrates?)?(g)?$"},
    "fiber": {"label": "Fiber", "unit": "g", "pattern": r"^(dietary)?fib(er|re)(g)?$"},
    "protein": {"label": "Protein", "unit": "g", "pattern": r"^proteins?(g)?$"},
    "sodium": {"label": "Sodium", "unit": "mg", "pattern": r"^sodium(mg)?$"},
    "sugar": {"label": "Sugar", "unit": "g", "pattern": r"^(total)?sugars?(g)?$"},
    "caffeine": {"label": "Caffeine", "unit": "mg", "pattern": r"^caffeine(mg)?$"},
}

# Adult reference intakes per day (UK/EU front-of-pack labelling; caffeine is the commonly cited
# 400 mg upper limit). Used to show each value as a share of a day.
REFERENCE_INTAKE = {"calories": 2000, "fat": 70, "carbs": 260, "sugar": 90, "protein": 50, "fiber": 30, "sodium": 2400, "caffeine": 400}

# Traffic-light levels for nutrients to limit, as % of the daily reference intake per item:
# green up to LOW, amber up to HIGH, red above. Protein and fiber are not coloured.
LEVEL_THRESHOLDS = {"low": 10, "high": 25}
LIMIT_NUTRIENTS = ("calories", "fat", "carbs", "sugar", "sodium", "caffeine")

# Header names that identify the item-name column (first column is unnamed in the provided files).
NAME_PATTERN = r"^(unnamed0|items?|names?|itemname|product|productname|beverage|drink|food|menuitem)$"

# Values that mean "no data" in the source files.
MISSING_TOKENS = {"", "-", "–", "—", "n/a", "na", "nan", "null", "none", "?", "--"}

MAX_UPLOAD_MB = 5

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
