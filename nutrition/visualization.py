"""Part 1.2 of the brief: charts comparing nutrients across items and between drinks and food.

Each function takes processed DataFrames and returns a Plotly figure as a JSON-ready dict,
which the browser draws with Plotly.js (theme colours and fonts are applied there).
"""
import json

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .config import METRICS
from .processing import calorie_bands, category_means, compare, macro_energy, metrics_in
from .processing.stats import CALORIE_BANDS

COLORS = {"drinks": "#5B8CFF", "food": "#FFD23F"}
OUTLINE = dict(color="#111111", width=2)  # every bar and slice is outlined, like the page
MACRO_COLORS = {METRICS[m]["label"]: c for m, c in {"fat": "#FF5FA2", "carbs": "#FF8A3D", "protein": "#3DDC97"}.items()}
# Green to red, lightest range first; keyed by label so an empty range can't shift the colours.
BAND_COLORS = dict(zip([band[2] for band in CALORIE_BANDS], ["#3DDC97", "#FFD23F", "#FF8A3D", "#FF4D4D"]))
GRAM_METRICS = ("fat", "carbs", "sugar", "fiber", "protein")


def _tint(hex_color: str, amount: float) -> str:
    """Mix a hex colour with white; amount 0 keeps the colour, 1 gives white."""
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (255 - c) * amount):02X}" for c in rgb)


def _label(metric: str) -> str:
    """Axis title such as 'Calories (kcal)'."""
    spec = METRICS[metric]
    return f"{spec['label']} ({spec['unit']})"


def _to_dict(fig: go.Figure, legend_bottom: bool = False) -> dict:
    """Apply the shared margins and legend position, then convert the figure to JSON for the browser."""
    legend = dict(orientation="h", y=-0.08, x=0.5, xanchor="center") if legend_bottom else dict(orientation="h", y=1.1, x=0)
    fig.update_layout(margin=dict(l=10, r=24, t=30, b=10), legend=legend)
    fig.update_xaxes(automargin=True, title_standoff=12)
    fig.update_yaxes(automargin=True, title_standoff=12)
    return json.loads(str(fig.to_json()))


def averages_chart(drinks: pd.DataFrame, food: pd.DataFrame) -> dict:
    """Grouped bars: average grams of each macronutrient per item, drinks vs food."""
    rows = [r for r in compare(drinks, food) if r["metric"] in GRAM_METRICS]
    fig = go.Figure()
    for kind in ("drinks", "food"):
        values = [r[kind] for r in rows]
        fig.add_bar(name=kind.title(), x=[r["label"] for r in rows], y=values,
                    marker=dict(color=COLORS[kind], cornerradius=6, line=OUTLINE), text=values, textposition="outside", cliponaxis=False,
                    hovertemplate="%{x}: %{y} g<extra>" + kind.title() + "</extra>")
    fig.update_layout(barmode="group", yaxis_title="Average grams per item", bargap=0.3)
    return _to_dict(fig)


def top_items_chart(frames: dict, metric: str, n: int = 10, lowest: bool = False) -> dict:
    """Horizontal bars of the n highest (or lowest) items for a metric across the chosen datasets."""
    parts = [df.assign(dataset=kind) for kind, df in frames.items() if metric in metrics_in(df)]
    fig = go.Figure()
    fig.update_layout(xaxis_title=_label(metric), showlegend=False)
    if parts:
        combined = pd.concat(parts).dropna(subset=[metric])
        ranked = combined.sort_values(metric, ascending=lowest).head(n).iloc[::-1]
        fig.add_bar(
            orientation="h", x=ranked[metric].tolist(), y=ranked["name"].tolist(),
            marker=dict(color=[COLORS[d] for d in ranked["dataset"]], cornerradius=4, line=OUTLINE),
            texttemplate="%{x:,.4~g}", textposition="outside", cliponaxis=False,
            customdata=ranked["dataset"].str.title().tolist(),
            hovertemplate="%{y}<br>%{x} " + METRICS[metric]["unit"] + "<extra>%{customdata}</extra>",
        )
        fig.update_xaxes(range=[0, float(ranked[metric].max()) * 1.12 or 1])
    return _to_dict(fig)


def distribution_chart(frames: dict, metric: str) -> dict:
    """Box plot with every item as a point, so outliers can be identified by hovering."""
    fig = go.Figure()
    for kind, df in frames.items():
        if metric in metrics_in(df):
            col = df.dropna(subset=[metric])
            fig.add_box(name=kind.title(), y=col[metric].tolist(), text=col["name"].tolist(),
                        boxpoints="all", jitter=0.45, pointpos=0,
                        marker=dict(size=7, color=COLORS[kind], line=dict(color="#111111", width=1)),
                        line=dict(color="#111111", width=2), fillcolor=COLORS[kind],
                        hovertemplate="%{text}<br>%{y} " + METRICS[metric]["unit"] + "<extra></extra>")
    fig.update_layout(yaxis_title=_label(metric), showlegend=False)
    return _to_dict(fig)


def _donuts(parts: dict, colors: dict) -> go.Figure:
    """One donut per dataset, side by side. `parts` maps dataset -> pandas Series (label -> value),
    and `colors` maps each label to its colour."""
    fig = make_subplots(rows=1, cols=max(len(parts), 1), specs=[[{"type": "domain"}] * max(len(parts), 1)])
    for i, (kind, series) in enumerate(parts.items(), start=1):
        fig.add_pie(labels=series.index.tolist(), values=series.round().tolist(), hole=0.6, sort=False,
                    marker=dict(colors=[colors[label] for label in series.index], line=OUTLINE),
                    title=dict(text=kind.title(), font=dict(size=15)), textinfo="percent",
                    hovertemplate="%{label}: %{value} (%{percent})<extra>" + kind.title() + "</extra>",
                    row=1, col=i)
    fig.update_layout(legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"))
    return fig


def macro_split_chart(frames: dict) -> dict:
    """Donuts showing what share of calories comes from fat, carbs and protein (9/4/4 kcal per g)."""
    parts = {
        kind: macro_energy(df).rename(lambda m: METRICS[m]["label"])
        for kind, df in frames.items() if {"fat", "carbs", "protein"} <= set(metrics_in(df))
    }
    return _to_dict(_donuts(parts, MACRO_COLORS), legend_bottom=True)


def category_mix_chart(frames: dict) -> dict:
    """Sunburst: inner ring splits the menu into drinks and food, outer ring into their categories."""
    ids, labels, parents, values, colors = [], [], [], [], []

    def add(node_id, label, parent, value, color):
        ids.append(node_id)
        labels.append(label)
        parents.append(parent)
        values.append(int(value))
        colors.append(color)

    for kind, df in frames.items():
        counts = df["category"].value_counts()
        add(kind, kind.title(), "", counts.sum(), COLORS[kind])
        for j, (category, count) in enumerate(counts.items()):
            add(f"{kind}/{category}", category, kind, count, _tint(COLORS[kind], (0.25, 0.5)[j % 2]))
    fig = go.Figure(go.Sunburst(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=colors, line=OUTLINE), leaf=dict(opacity=1),
        insidetextorientation="horizontal", hovertemplate="%{label}: %{value} items (%{percentRoot:.0%} of menu)<extra></extra>",
    ))
    # Hide labels that would not fit their segment instead of shrinking them to noise.
    fig.update_layout(uniformtext=dict(minsize=11, mode="hide"))
    return _to_dict(fig)


def calorie_bands_chart(frames: dict) -> dict:
    """Donuts of how many items fall into each calorie range."""
    parts = {kind: calorie_bands(df) for kind, df in frames.items() if "calories" in metrics_in(df)}
    return _to_dict(_donuts(parts, BAND_COLORS), legend_bottom=True)


def scatter_chart(frames: dict, x: str = "calories", y: str = "protein") -> dict:
    """Each item as a dot; dot size follows fat so heavier items stand out."""
    fig = go.Figure()
    for kind, df in frames.items():
        if x in metrics_in(df) and y in metrics_in(df):
            col = df.dropna(subset=[x, y])
            size = (col["fat"].fillna(0) * 0.6 + 5).clip(upper=24).tolist() if "fat" in col else 7
            fig.add_scatter(name=kind.title(), mode="markers", x=col[x].tolist(), y=col[y].tolist(),
                            text=col["name"].tolist(),
                            marker=dict(color=COLORS[kind], size=size, sizemode="diameter", opacity=0.7,
                                        line=dict(color="#111111", width=1)),
                            hovertemplate="%{text}<br>%{x} kcal, %{y} g protein<extra></extra>")
    fig.update_layout(xaxis_title=_label(x), yaxis_title=_label(y))
    return _to_dict(fig)


def category_chart(frames: dict, metric: str = "calories") -> dict:
    """Average of a metric for each menu category, both datasets on one sorted axis."""
    fig = go.Figure()
    longest = 0.0
    for kind, df in frames.items():
        if metric in metrics_in(df) and "category" in df:
            grouped = category_means(df, metric)
            longest = max(longest, float(grouped.max()))
            fig.add_bar(name=kind.title(), orientation="h", x=grouped.tolist(), y=grouped.index.tolist(),
                        marker=dict(color=COLORS[kind], cornerradius=4, line=OUTLINE), text=grouped.tolist(), textposition="outside", cliponaxis=False,
                        hovertemplate="%{y}: %{x} " + METRICS[metric]["unit"] + "<extra></extra>")
    fig.update_layout(xaxis_title=f"Average {_label(metric).lower()}", barmode="relative")
    if longest:
        fig.update_xaxes(range=[0, longest * 1.12])
    return _to_dict(fig)


def all_charts(frames: dict, metric: str, n: int, lowest: bool) -> dict:
    """Every chart on the Charts page. `metric` drives the ranking, spread and category charts."""
    return {
        "averages": averages_chart(frames["drinks"], frames["food"]) if {"drinks", "food"} <= set(frames) else None,
        "top": top_items_chart(frames, metric, n, lowest),
        "distribution": distribution_chart(frames, metric),
        "macros": macro_split_chart(frames),
        "mix": category_mix_chart(frames),
        "bands": calorie_bands_chart(frames),
        "scatter": scatter_chart(frames),
        "categories": category_chart(frames, metric),
    }
