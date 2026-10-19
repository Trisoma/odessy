"""Arithmetic over expense entries."""


def sum_amounts(entries: list[dict]) -> float:
    """Total of the entries' amounts."""
    return sum(int(entry["amount"]) for entry in entries)
