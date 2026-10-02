import pytest

from nutrition.config import DEFAULT_FILES
from nutrition.loading import DataLoadError, load_csv, to_number


def test_provided_drinks_file_is_cleaned():
    df, report = load_csv(DEFAULT_FILES["drinks"], "drinks")
    assert report["rows_read"] == 177
    assert report["dropped_no_data"] == 85  # rows that are all "-"
    assert report["duplicates_removed"] > 0
    assert df["name"].is_unique
    assert df["calories"].dtype == float
    assert set(report["metrics"]) == {"calories", "fat", "carbs", "fiber", "protein", "sodium"}


def test_provided_food_file_is_utf16_with_padded_headers():
    df, report = load_csv(DEFAULT_FILES["food"], "food")
    assert report["encoding"] == "utf-16"
    assert report["rows_kept"] == 113
    assert "protein" in df  # " Protein (g)" was mapped


def test_semicolon_file_with_units_placeholders_and_duplicates():
    raw = (
        "Item;Calories;Total Fat (g);Sugars (g);Caffeine (mg);Notes\n"
        "Latte;190;7;17;150;x\n"
        "Latte;190;7;17;150;x\n"
        'Mocha;"1,200";n/a;35 g;175;\n'
        "Ghost;-;-;-;-;\n"
    ).encode("cp1252")
    df, report = load_csv(raw, "drinks")
    assert report["delimiter"] == ";"
    assert report["duplicates_removed"] == 1
    assert report["dropped_no_data"] == 1
    assert report["ignored_columns"] == ["Notes"]
    mocha = df.set_index("name").loc["Mocha"]
    assert mocha["calories"] == 1200 and mocha["sugar"] == 35
    assert mocha.isna()["fat"]


@pytest.mark.parametrize("value, expected", [("45 mg", 45.0), ("1,200", 1200.0), ("12,5", 12.5), ("-", None), ("-5", None), (3, 3.0)])
def test_to_number(value, expected):
    result = to_number(value)
    assert (result != result) if expected is None else result == expected  # NaN check


@pytest.mark.parametrize("raw, message", [
    (b"", "empty"),
    (b"Item,Colour\nLatte,brown\n", "no nutrition columns"),
    (b"Calories,Fat\n10,1\n20,2\n", "item names"),
])
def test_unusable_files_raise_clear_errors(raw, message):
    with pytest.raises(DataLoadError, match=message):
        load_csv(raw, "test")
