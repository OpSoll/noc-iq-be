from typing import Any, Mapping


_SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Prefix formula-leading text cells so spreadsheet applications treat them as text."""
    return {
        key: f"'{value}"
        if isinstance(value, str) and value.startswith(_SPREADSHEET_FORMULA_PREFIXES)
        else value
        for key, value in row.items()
    }
