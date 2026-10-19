"""Currency conversion with fixed rates."""

#: Euros per unit of each supported currency.
RATES = {"EUR": 1.0, "USD": 0.92}


def to_eur(amount: float, code: str) -> float:
    """Convert an amount in a supported currency to euros."""
    return amount * RATES[code]
