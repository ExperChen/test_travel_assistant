"""Single source of truth for demo-grade, hardcoded currency-to-MYR exchange rates.

These are illustrative, not live rates -- keep them in sync manually if you add a
currency. This table used to be duplicated independently in flight_tool.py, tools.py,
and attraction_tool.py; the CNY rate had drifted (flight_tool.py had 0.94 vs. 0.65
everywhere else, a ~45% overestimate that skewed budget filtering for CNY-denominated
requests).
"""

EXCHANGE_TO_MYR: dict[str, float] = {
    "MYR": 1.0,
    "RM": 1.0,
    "THB": 0.13,
    "฿": 0.13,  # ฿
    "CNY": 0.65,
    "RMB": 0.65,
    "JPY": 0.031,
    "¥": 0.031,  # ¥ (treated as JPY here, matching prior attraction_tool.py behavior)
    "SGD": 3.50,
    "KRW": 0.0034,
    "USD": 4.70,
    "EUR": 5.10,
    "GBP": 6.00,
    "AED": 1.28,
    "AUD": 3.00,
    "HKD": 0.60,
    "TWD": 0.14,
    "CAD": 3.20,
}


def get_rate(currency: str, default: float | None = 1.0) -> float | None:
    """Look up the MYR exchange rate for a currency code or symbol.

    Returns `default` when the currency isn't recognized (1.0 unless overridden).
    """
    return EXCHANGE_TO_MYR.get(str(currency or "").strip().upper(), default)


def convert_to_myr(amount: float, currency: str, default_rate: float | None = 1.0) -> float | None:
    """Convert `amount` in `currency` to MYR.

    Returns None (instead of a converted amount) when `default_rate` is None and the
    currency isn't recognized -- pass `default_rate=None` for callers that need to
    distinguish "unknown currency" from "already priced in MYR".
    """
    rate = get_rate(currency, default_rate)
    if rate is None:
        return None
    return float(amount) * rate
