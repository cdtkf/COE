"""
export.py — Helpers for turning dashboard DataFrames into downloadable files.

Kept deliberately small and Streamlit-free so it can be unit-tested or
reused outside the dashboard (e.g. from a CLI report). Streamlit-side
wiring lives in app.py.
"""
from io import BytesIO

import pandas as pd

# Excel caps sheet names at 31 chars and disallows certain characters.
_INVALID_SHEET_CHARS = set(r"[]:*?/\\")
_MAX_SHEET_NAME_LEN = 31


def _safe_sheet_name(name: str) -> str:
    """Strip characters Excel rejects and clamp length to 31."""
    cleaned = "".join("_" if ch in _INVALID_SHEET_CHARS else ch for ch in name)
    cleaned = cleaned.strip() or "Sheet1"
    return cleaned[:_MAX_SHEET_NAME_LEN]


def _strip_timezones(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a copy of `df` with timezone info stripped from any
    tz-aware datetime columns.

    Why: Excel's file format has no concept of "this timestamp is in
    UTC" — pandas raises rather than silently lying. Our timestamps
    are stored as TIMESTAMP WITH TIME ZONE in Postgres (UTC), so the
    DataFrame columns come back tz-aware. Stripping the tz preserves
    the wall-clock UTC value; downstream consumers reading the
    spreadsheet should treat the column as UTC.
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            if getattr(df[col].dt, "tz", None) is not None:
                df[col] = df[col].dt.tz_localize(None)
    return df


def df_to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Data") -> bytes:
    """
    Serialize a DataFrame to a bare-bones .xlsx as raw bytes.

    No formatting, no styling — just a single sheet of header + rows.
    Suitable for handing straight to st.download_button.

    Args:
        df: The DataFrame to export. Index is dropped (rows are usually
            already meaningful by their columns). Timezone-aware datetime
            columns are converted to tz-naive (UTC wall-clock) so Excel
            will accept them.
        sheet_name: Sheet label inside the workbook. Sanitized to satisfy
            Excel's 31-character / no-special-chars rule.

    Returns:
        The full .xlsx file as bytes.
    """
    df = _strip_timezones(df)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=_safe_sheet_name(sheet_name), index=False)
    return buffer.getvalue()
