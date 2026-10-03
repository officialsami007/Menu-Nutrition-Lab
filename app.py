"""Bonus 2 of the brief: the Flask web interface.

Routes only: each one reads the request, calls the nutrition package and returns JSON.
Run with:  python app.py   then open http://127.0.0.1:5000
"""
import io
import json
import os
import secrets

from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context

from nutrition import load_default_menu, load_menu, processing, summarization, visualization
from nutrition.config import DATASETS, LEVEL_THRESHOLDS, LIMIT_NUTRIENTS, MAX_UPLOAD_MB, METRICS, REFERENCE_INTAKE
from nutrition.loading import DataLoadError
from nutrition.store import DataStore

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(16)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

store = DataStore(load_default_menu())  # the provided CSVs are loaded once, at start-up


def session_id() -> str:
    """A random id stored in the browser's cookie, so each visitor gets their own uploaded data."""
    return session.setdefault("sid", secrets.token_hex(8))


def current_data() -> tuple[dict, dict]:
    """(frames, reports) for this browser session, each keyed by 'drinks' / 'food'."""
    data = store.get(session_id())
    return {k: v[0] for k, v in data.items()}, {k: v[1] for k, v in data.items()}


def error(message, status=400):
    """JSON error response that the front end shows to the user."""
    return jsonify({"error": message}), status


@app.get("/")
def index():
    """The single page; the six views inside it are switched in the browser."""
    return render_template("index.html", metrics=METRICS, max_mb=MAX_UPLOAD_MB)


@app.get("/api/overview")
def overview():
    """Overview page: statistics for both datasets and the drinks vs food comparison."""
    frames, reports = current_data()
    return jsonify({
        "datasets": {
            kind: {
                "stats": processing.describe(frames[kind]),
                "report": reports[kind],
                "categories": sorted(frames[kind]["category"].unique().tolist()),
            }
            for kind in DATASETS
        },
        "comparison": processing.compare(frames["drinks"], frames["food"]),
        "metrics": METRICS,
        "reference_intake": REFERENCE_INTAKE,
        "levels": {"thresholds": LEVEL_THRESHOLDS, "limit": LIMIT_NUTRIENTS},
        "using_defaults": store.using_defaults(session_id()),
    })


@app.get("/api/charts")
def charts():
    """Charts page: all eight figures for the chosen nutrient and ranking order."""
    frames, _ = current_data()
    metric = request.args.get("metric", "calories")
    if metric not in METRICS:
        return error(f"Unknown metric '{metric}'.")
    n = min(max(request.args.get("n", 10, type=int), 3), 25)
    return jsonify(visualization.all_charts(frames, metric, n, lowest=request.args.get("order") == "lowest"))


def filtered_items():
    """The dataset named in ?dataset=, filtered and sorted by the other query parameters."""
    frames, _ = current_data()
    kind = request.args.get("dataset", "drinks")
    if kind not in frames:
        return kind, None, None
    rows = processing.apply_filters(frames[kind], request.args)
    return kind, frames[kind], processing.sort_rows(rows, request.args.get("sort"), request.args.get("desc") == "1")


@app.get("/api/items")
def items():
    """Explore page: the filtered, sorted rows for the table."""
    kind, df, rows = filtered_items()
    if df is None:
        return error("Choose drinks or food.")
    return jsonify({
        "dataset": kind,
        "total": len(df),
        "count": len(rows),
        "columns": ["name", "category"] + processing.metrics_in(df) + ["caffeinated"],
        "caffeine_source": processing.caffeine_source(df),
        "rows": processing.records(rows),
    })


@app.get("/api/items.csv")
def items_csv():
    """Explore page: the same filtered rows as a CSV download."""
    kind, df, rows = filtered_items()
    if df is None:
        return error("Choose drinks or food.")
    buffer = io.StringIO()
    rows.to_csv(buffer, index=False)
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=starbucks-{kind}-filtered.csv"})


@app.post("/api/upload")
def upload():
    """Your data page: replace the drinks and/or food data for this browser session."""
    files = {k: request.files[k] for k in DATASETS if k in request.files and request.files[k].filename}
    if not files:
        return error("Choose at least one CSV file to upload.")

    loaded, problems = {}, {}
    for kind, file in files.items():
        if not file.filename.lower().endswith(".csv"):
            problems[kind] = f"{file.filename} isn't a .csv file."
            continue
        try:
            loaded[kind] = load_menu(file.stream, kind, file.filename)
        except DataLoadError as exc:
            problems[kind] = str(exc)

    # All-or-nothing: if any uploaded file is unusable, nothing is replaced.
    if problems:
        return jsonify({"error": "Some files couldn't be used.", "problems": problems}), 400
    store.replace(session_id(), loaded)
    return jsonify({"loaded": {k: report for k, (_, report) in loaded.items()}})


@app.post("/api/reset")
def reset():
    """Your data page: go back to the provided Starbucks files."""
    store.reset(session_id())
    return jsonify({"ok": True})


@app.errorhandler(413)
def too_large(_):
    """Uploads over the size limit get a readable message instead of Flask's default page."""
    return error(f"Files must be under {MAX_UPLOAD_MB} MB.", 413)


@app.post("/api/summary")
def summary():
    """AI summary page (Part 2): stream the Groq-written summary as plain text."""
    frames, reports = current_data()
    focus = (request.get_json(silent=True) or {}).get("focus", "overview")
    try:
        chunks = summarization.stream_summary(frames, reports, focus)
    except summarization.LLMError as exc:
        return error(str(exc), 502)
    return Response(stream_with_context(chunks), mimetype="text/plain")


@app.post("/api/ask")
def ask():
    """Ask the menu page (Bonus 1): stream the answer as one JSON event per line.

    The browser sends the chat so far (`history`) and its running notes (`memory`), which is how
    follow-up questions keep their context.
    """
    body = request.get_json(silent=True) or {}
    question = str(body.get("question", "")).strip()
    if not question:
        return error("Type a question first.")
    if len(question) > 500:
        return error("Keep questions under 500 characters.")
    frames, reports = current_data()
    try:
        events = summarization.stream_answer(question, body.get("history") or [], body.get("memory"), frames, reports)
    except summarization.LLMError as exc:
        return error(str(exc), 502)
    lines = (json.dumps(event, ensure_ascii=False, default=str) + "\n" for event in events)
    return Response(stream_with_context(lines), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 5000)))
