"""Monthly reports."""

from ledger.totals import sum_amounts


def monthly_report(entries: list[dict], month: str) -> dict:
    """Entries for one month ("2026-10") and their total."""
    selected = [entry for entry in entries if entry["date"].startswith(month)]
    return {"month": month, "count": len(selected), "total": sum_amounts(selected)}
