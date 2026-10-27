"""Deterministic middle payoffs for integer-scored sports, including pushes."""

import math


def settlement(stake, odds, margin):
    """Return a single leg's payout including returned stake on a push."""
    return round(stake * (odds if margin > 0 else 1 if margin == 0 else 0), 2)


def middle_profile(side_a, side_b, stake_a, stake_b, is_total):
    low = side_a["line"] if is_total else -side_a["line"]
    high = side_b["line"]
    # Whole and half lines are supported; Asian quarter settlements are excluded.
    if any(not math.isclose(line * 2, round(line * 2)) for line in (low, high)):
        return None
    if high - low > 1000:
        return None
    total = round(stake_a + stake_b, 2)
    scenarios = []
    start = max(0, math.floor(low) - 1) if is_total else math.floor(low) - 1
    for score in range(start, math.ceil(high) + 2):
        margin_a, margin_b = score - low, high - score
        payout = settlement(stake_a, side_a["effective_price"], margin_a)
        payout += settlement(stake_b, side_b["effective_price"], margin_b)
        scenarios.append(
            {
                "score": score,
                "profit": round(payout - total, 2),
                "side_a": "win" if margin_a > 0 else "push" if margin_a == 0 else "lose",
                "side_b": "win" if margin_b > 0 else "push" if margin_b == 0 else "lose",
            }
        )
    both = [s["profit"] for s in scenarios if s["side_a"] == s["side_b"] == "win"]
    if not both:
        return None
    worst_miss = min(s["profit"] for s in scenarios if not s["side_a"] == s["side_b"] == "win")
    win_both = min(both)
    # Conservative threshold: assign every non-middle outcome its worst payoff.
    threshold = max(0.0, -worst_miss / (win_both - worst_miss)) if win_both > worst_miss else None
    return {
        "scenarios": scenarios,
        "worst_case_profit": min(s["profit"] for s in scenarios),
        "win_both_profit": win_both,
        "worst_miss_profit": worst_miss,
        "break_even_probability": threshold,
        "break_even_percent": round(threshold * 100, 2) if threshold is not None else None,
        "probability": None,
        "ev": None,
    }
