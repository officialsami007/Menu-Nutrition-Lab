import pandas as pd
import pytest

from nutrition import load_default_menu, processing, visualization
from nutrition.processing import queries


@pytest.fixture
def drinks():
    df = pd.DataFrame({
        "name": ["Caffè Latte", "Decaf Pike Place Roast", "Hot Chocolate", "Teavana Iced Green Tea"],
        "calories": [190.0, 5.0, 320.0, 30.0],
        "fat": [7.0, 0.0, 9.0, 0.0],
        "carbs": [19.0, 0.0, 47.0, 8.0],
        "protein": [13.0, 1.0, 14.0, 0.0],
    })
    return processing.enrich(df, "drinks")


@pytest.fixture
def food():
    df = pd.DataFrame({
        "name": ["Butter Croissant", "Turkey Pesto Panini"],
        "calories": [240.0, 560.0],
        "fat": [12.0, 22.0],
        "carbs": [28.0, 60.0],
        "protein": [5.0, 30.0],
    })
    return processing.enrich(df, "food")


def test_caffeine_is_estimated_from_names(drinks):
    flags = dict(zip(drinks["name"], drinks["caffeinated"]))
    assert flags == {"Caffè Latte": True, "Decaf Pike Place Roast": False,
                     "Hot Chocolate": False, "Teavana Iced Green Tea": True}
    assert processing.caffeine_source(drinks) == "name"


def test_caffeine_column_wins_over_names():
    df = pd.DataFrame({"name": ["Latte", "Water"], "calories": [100.0, 0.0], "caffeine": [150.0, 0.0]})
    out = processing.enrich(df, "drinks")
    assert out["caffeinated"].tolist() == [True, False]
    assert processing.caffeine_source(out) == "column"


def test_describe(drinks):
    stats = processing.describe(drinks)
    assert stats["per_metric"]["calories"]["total"] == 545
    assert stats["per_metric"]["calories"]["highest"]["name"] == "Hot Chocolate"
    assert stats["fat_protein_ratio"] == round(16 / 28, 2)
    assert stats["sweetness_metric"] == "carbs"  # no sugar column
    assert sum(stats["macro_energy_share"].values()) == pytest.approx(100, abs=0.2)


def test_compare(drinks, food):
    calories = next(r for r in processing.compare(drinks, food) if r["metric"] == "calories")
    assert calories["drinks"] == 136.2 and calories["food"] == 400 and calories["higher"] == "food"


def test_filters(drinks, food):
    assert len(processing.apply_filters(drinks, {"caffeine": "yes"})) == 2
    assert processing.apply_filters(food, {"calories_max": "500"})["name"].tolist() == ["Butter Croissant"]
    # "under" is strict, "max" is inclusive: the 560 kcal panini sits exactly on the boundary.
    assert processing.apply_filters(food, {"calories_below": "560"})["name"].tolist() == ["Butter Croissant"]
    assert len(processing.apply_filters(food, {"calories_max": "560"})) == 2
    assert processing.apply_filters(drinks, {"search": "latte"})["name"].tolist() == ["Caffè Latte"]
    assert len(processing.apply_filters(drinks, {"calories_max": "not a number"})) == 4


def test_every_chart_builds(drinks, food):
    figs = visualization.all_charts({"drinks": drinks, "food": food}, "calories", 5, lowest=False)
    assert set(figs) == {"averages", "top", "distribution", "macros", "mix", "bands", "scatter", "categories"}
    assert all(fig["data"] for fig in figs.values())


@pytest.fixture(scope="module")
def menu():
    return {k: df for k, (df, _) in load_default_menu().items()}


def test_query_aggregate_and_missing_nutrient(menu):
    assert queries.aggregate(menu, "drinks", "calories", "mean")["drinks"]["value"] == 138.6
    with pytest.raises(queries.QueryError, match="not available"):
        queries.aggregate(menu, "drinks", "caffeine", "mean")


def test_query_rank_keeps_ties(menu):
    rows = queries.rank(menu, "drinks", "protein", limit=1)["drinks"]
    assert len(rows) == 3 and {r["protein"] for r in rows} == {20.0}


def test_query_rank_with_filters(menu):
    rows = queries.rank(menu, "food", "protein", limit=1, filters={"calories_max": 400})["food"]
    assert rows[0]["name"] == "Smoked Turkey Protein Box"
