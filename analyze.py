"""Command-line version of the tool: load the CSVs, print statistics, filter, and ask the LLM.

Examples:
    python analyze.py                                   # cleaning report, statistics, comparison
    python analyze.py --show drinks --caffeine yes      # only drinks with caffeine
    python analyze.py --show food --under-calories 500  # food items under 500 calories
    python analyze.py --summary sugar                   # Groq summary (overview|sugar|calories|protein)
    python analyze.py --ask "What's the average caffeine content for drinks?"
    python analyze.py --drinks my_drinks.csv            # analyse only your own drinks file
    python analyze.py --drinks my_drinks.csv --food my_food.csv
"""
import argparse
import sys

import pandas as pd

from nutrition import load_menu, processing, summarization
from nutrition.config import DEFAULT_FILES, METRICS
from nutrition.loading import DataLoadError

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 20)
pd.set_option("display.max_colwidth", 50)


def heading(text: str) -> None:
    """Print an underlined section title."""
    print(f"\n{text}\n{'=' * len(text)}")


def print_report(kind: str, report: dict) -> None:
    """One line per file describing what cleaning changed."""
    print(f"{kind.title()}: {report['rows_read']} rows read, {report['rows_kept']} kept "
          f"({report['dropped_no_data']} with no values, {report['duplicates_removed']} duplicates, "
          f"{report['conflicting_duplicates']} conflicting duplicates). "
          f"Encoding {report['encoding']}, delimiter '{report['delimiter']}'.")


def print_statistics(kind: str, df: pd.DataFrame) -> None:
    """Part 1.2: the statistics table for one dataset, plus ratio, sugar and caffeine notes."""
    stats = processing.describe(df)
    table = pd.DataFrame(stats["per_metric"]).T[["total", "mean", "median", "min", "max"]]
    table.index = [f"{METRICS[m]['label']} ({METRICS[m]['unit']})" for m in table.index]
    heading(f"{kind.title()}: {stats['items']} items")
    print(table.to_string())
    print(f"Fat-to-protein ratio (total fat / total protein): {stats.get('fat_protein_ratio')}")
    if stats["sweetness_metric"] == "carbs":
        print(f"Average sugar: not in this file; average carbs instead: {stats['per_metric']['carbs']['mean']} g")
    if kind == "drinks":
        source = "from the caffeine column" if stats["caffeine_source"] == "column" else "estimated from item names"
        print(f"Drinks with caffeine: {stats['caffeinated_items']} of {stats['items']} ({source})")


def print_comparison(frames: dict) -> None:
    """Part 1.2: average of each shared nutrient, drinks vs food."""
    heading("Drinks vs food (average per item)")
    table = pd.DataFrame(processing.compare(frames["drinks"], frames["food"]))
    print(table[["label", "unit", "drinks", "food", "difference", "higher"]].to_string(index=False))


def print_filtered(df: pd.DataFrame, args: argparse.Namespace) -> None:
    """Part 1.3: the rows of one dataset that pass the --caffeine / --under-calories / --min-protein filters."""
    filters = {"caffeine": args.caffeine, "calories_below": args.under_calories, "protein_min": args.min_protein}
    rows = processing.apply_filters(df, filters).sort_values("calories")
    heading(f"{args.show.title()} matching filters: {len(rows)} of {len(df)}")
    columns = ["name", "category"] + processing.metrics_in(df) + (["caffeinated"] if args.show == "drinks" else [])
    print(rows[columns].to_string(index=False))


def main() -> int:
    """Parse the arguments, load both files, then run the one action asked for. Returns the exit code."""
    parser = argparse.ArgumentParser(description="Menu nutrition analysis")
    parser.add_argument("--drinks", help="drinks CSV (with neither --drinks nor --food, the provided files are used)")
    parser.add_argument("--food", help="food CSV; give just one of --drinks / --food to analyse only that file")
    parser.add_argument("--show", choices=["drinks", "food"], help="list items from one dataset after filtering")
    parser.add_argument("--caffeine", choices=["yes", "no"], help="with --show: keep drinks with/without caffeine")
    parser.add_argument("--under-calories", type=float, help="with --show: keep items below this many kcal")
    parser.add_argument("--min-protein", type=float, help="with --show: keep items with at least this much protein (g)")
    parser.add_argument("--summary", nargs="?", const="overview", choices=list(summarization.FOCUS_PROMPTS),
                        help="print a Groq-written summary")
    parser.add_argument("--ask", metavar="QUESTION", help="ask the LLM a question about the menu")
    args = parser.parse_args()

    # Like the web app: a file you leave out is left out, not filled in from the provided files.
    paths = {k: p for k, p in (("drinks", args.drinks), ("food", args.food)) if p} or dict(DEFAULT_FILES)
    if args.show and args.show not in paths:
        print(f"No {args.show} file given, so there is nothing to show. Add --{args.show} FILE.", file=sys.stderr)
        return 1
    try:
        loaded = {kind: load_menu(path, kind) for kind, path in paths.items()}
    except (DataLoadError, OSError) as exc:
        print(f"Could not load data: {exc}", file=sys.stderr)
        return 1
    frames = {k: df for k, (df, _) in loaded.items()}
    reports = {k: report for k, (_, report) in loaded.items()}

    try:
        if args.show:
            print_filtered(frames[args.show], args)
        elif args.summary:
            for chunk in summarization.stream_summary(frames, reports, args.summary):
                print(chunk, end="", flush=True)
            print()
        elif args.ask:
            result = summarization.answer_question(args.ask, [], frames, reports)
            print(result["answer"])
            for step in result["steps"]:
                print(f"\n[calculated with {step['tool']} {step['args']}]")
        else:
            heading("Data cleaning")
            for kind in frames:
                print_report(kind, reports[kind])
            for kind, df in frames.items():
                print_statistics(kind, df)
            if len(frames) == 2:
                print_comparison(frames)
            else:
                print(f"\nOnly a {next(iter(frames))} file was given, so there is no drinks vs food comparison.")
    except summarization.LLMError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
