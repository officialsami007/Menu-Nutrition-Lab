"""Part 3 of the brief: the separation of concerns is checked, not just described.

Each module's imports are read from its source code and compared with the layers it is allowed
to use, so a change that mixes loading, processing and summarization makes these tests fail.
"""
import ast
from pathlib import Path

import pandas as pd

from nutrition import load_menu, visualization
from nutrition.processing import calorie_bands, describe

ROOT = Path(__file__).resolve().parent.parent

# Which parts of the nutrition package each layer may import.
ALLOWED = {
    "nutrition/loading.py": {"config"},
    "nutrition/processing": {"config", "processing"},
    "nutrition/visualization.py": {"config", "processing"},
    "nutrition/summarization": {"config", "processing", "summarization"},
    "nutrition/store.py": set(),
}


def imports(path: Path) -> set[str]:
    """Top-level names imported by a file, with relative imports resolved inside the package."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.add(node.module.split(".")[0])
            else:
                # "from .x" inside processing/ means processing.x; "from ..x" means nutrition.x
                package = path.parent.relative_to(ROOT / "nutrition").parts
                base = list(package[: len(package) - (node.level - 1)])
                target = base + (node.module.split(".") if node.module else [])
                found.add("nutrition." + (target[0] if target else ""))
    return found


def package_layers(path: Path) -> set[str]:
    return {name.split(".", 1)[1] for name in imports(path) if name.startswith("nutrition.") and name != "nutrition."}


def files(layer: str) -> list[Path]:
    target = ROOT / layer
    return sorted(target.glob("*.py")) if target.is_dir() else [target]


def test_each_layer_only_imports_the_layers_below_it():
    for layer, allowed in ALLOWED.items():
        for path in files(layer):
            extra = package_layers(path) - allowed - {path.stem}
            assert not extra, f"{path.relative_to(ROOT)} imports {extra}, which is outside its layer"


def test_web_and_llm_code_stay_in_their_own_files():
    flask_users = {p.relative_to(ROOT).as_posix() for p in ROOT.glob("**/*.py")
                   if ".venv" not in p.parts and "flask" in imports(p)}
    groq_users = {p.relative_to(ROOT).as_posix() for p in (ROOT / "nutrition").rglob("*.py") if "groq" in imports(p)}
    assert flask_users <= {"app.py", "tests/test_app.py"}
    assert groq_users == {"nutrition/summarization/client.py"}


def test_new_columns_are_picked_up_without_code_changes():
    """Extensibility: a file that has real sugar and caffeine columns uses them automatically."""
    csv = b"Item,Calories,Sugars (g),Caffeine (mg)\nLatte,190,17,150\nHerbal Tea,0,0,0\n"
    df, report = load_menu(csv, "drinks")
    stats = describe(df)
    assert stats["sweetness_metric"] == "sugar" and stats["caffeine_source"] == "column"
    assert df.set_index("name")["caffeinated"].to_dict() == {"Latte": True, "Herbal Tea": False}


def test_calorie_bands_keep_their_colours_when_a_band_is_empty():
    df = pd.DataFrame({"name": ["Water", "Muffin"], "calories": [0.0, 470.0], "category": ["Other", "Bakery"]})
    assert calorie_bands(df).to_dict() == {"Under 150 kcal": 1, "450+ kcal": 1}
    pie = visualization.calorie_bands_chart({"food": df})["data"][0]
    assert pie["marker"]["colors"] == ["#3DDC97", "#FF4D4D"]  # green for light, red for heavy
