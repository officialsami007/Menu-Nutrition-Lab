"""Part 1.1 of the brief: turn a raw (possibly messy) CSV into a clean, typed DataFrame.

Loading only: no categorising or statistics here, those belong to nutrition.processing.

Handles the issues found in the provided files and other common ones:
- unknown encodings (the food file is UTF-16; others may be UTF-8 with BOM or Latin-1)
- unknown delimiters (comma, semicolon, tab, pipe)
- padded or inconsistent headers (" Calories", "Protein" vs "Protein (g)")
- placeholder values ("-", "n/a", blanks) and numbers with units or thousands separators
- rows with no nutrition data at all, and duplicate rows
"""
import csv
import io
import re
from pathlib import Path

import pandas as pd

from .config import METRICS, MISSING_TOKENS, NAME_PATTERN


class DataLoadError(ValueError):
    """Raised when a file can't be turned into a usable nutrition table."""


def _normalise_header(header: str) -> str:
    """Lower-case and keep only letters and digits, so ' Fat (g)' becomes 'fatg'."""
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Return (text, encoding). Uses the byte-order mark when present, then tries common encodings."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    # UTF-16 text without its byte-order mark shows up as lots of zero bytes.
    if raw[:200].count(b"\x00") > 20:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                continue
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise DataLoadError("The file's text encoding couldn't be read.")


def _sniff_delimiter(text: str) -> str:
    """Guess the separator from the first lines; fall back to a comma."""
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def to_number(value):
    """Parse '1,200', '45 mg', '5g' or ' 12 ' into a float; placeholders and negatives become NaN."""
    if pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else float("nan")
    text = str(value).strip().lower()
    if text in MISSING_TOKENS:
        return float("nan")
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # thousands separator
    text = text.replace(",", ".")  # decimal comma
    match = re.search(r"\d+(\.\d+)?", text)
    if not match or text.startswith("-"):
        return float("nan")
    return float(match.group())


def _map_columns(columns) -> tuple[dict, list]:
    """Map raw headers to canonical names. Returns ({raw: canonical}, ignored_headers)."""
    mapping, ignored = {}, []
    for col in columns:
        key = _normalise_header(col)
        if "name" not in mapping.values() and re.match(NAME_PATTERN, key):
            mapping[col] = "name"
            continue
        for metric, spec in METRICS.items():
            if metric not in mapping.values() and re.match(spec["pattern"], key):
                mapping[col] = metric
                break
        else:
            ignored.append(str(col).strip())
    return mapping, ignored


def load_csv(source, label: str = "dataset") -> tuple[pd.DataFrame, dict]:
    """Load a CSV from a path, bytes or file-like object.

    Returns the clean DataFrame (columns: name + known metrics) and a report describing
    what was fixed, so the UI can show users exactly how their data was treated.
    """
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()
    if not raw or not raw.strip():
        raise DataLoadError(f"The {label} file is empty.")

    text, encoding = decode_bytes(raw)
    delimiter = _sniff_delimiter(text)
    # Read every cell as text and convert numbers ourselves (to_number), so values like
    # "1,200" or "45 mg" are understood instead of turning a whole column into strings.
    try:
        df = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, skipinitialspace=True,
                         keep_default_na=False, on_bad_lines="skip")
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataLoadError(f"The {label} file isn't a readable CSV ({exc}).") from exc

    df = df.dropna(axis=1, how="all")
    mapping, ignored = _map_columns(df.columns)

    # No recognisable name header: fall back to the first column that is mostly text.
    if "name" not in mapping.values():
        for col in df.columns:
            if col not in mapping and df[col].map(to_number).isna().mean() > 0.5:
                mapping[col] = "name"
                ignored = [c for c in ignored if c != str(col).strip()]
                break
    if "name" not in mapping.values():
        raise DataLoadError(f"The {label} file needs a column with item names.")

    metrics_found = [m for m in METRICS if m in mapping.values()]
    if not metrics_found:
        raise DataLoadError(
            f"The {label} file has no nutrition columns. Expected headers such as Calories, Fat (g), Protein."
        )

    df = df[list(mapping)].rename(columns=mapping)
    rows_read = len(df)

    df["name"] = df["name"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    df = df[~df["name"].str.lower().isin(MISSING_TOKENS)]
    for metric in metrics_found:
        df[metric] = df[metric].map(to_number).astype(float)

    no_data = df[metrics_found].isna().all(axis=1)
    dropped_no_data = int(no_data.sum())
    df = df[~no_data]

    # Exact duplicate rows are safe to drop. When the same name appears with different values
    # we can't tell which is right, so we keep the first and report how many were affected.
    before = len(df)
    df = df.drop_duplicates()
    exact_dupes = before - len(df)
    before = len(df)
    df = df.drop_duplicates(subset="name", keep="first")
    name_dupes = before - len(df)

    df = df.reset_index(drop=True)
    report = {
        "rows_read": rows_read,
        "rows_kept": len(df),
        "dropped_no_data": dropped_no_data,
        "duplicates_removed": exact_dupes,
        "conflicting_duplicates": name_dupes,
        "partial_rows": int(df[metrics_found].isna().any(axis=1).sum()),
        "encoding": encoding,
        "delimiter": {"\t": "tab"}.get(delimiter, delimiter),
        "metrics": metrics_found,
        "ignored_columns": ignored,
    }
    if df.empty:
        raise DataLoadError(f"The {label} file has no rows with nutrition values.")
    return df, report
