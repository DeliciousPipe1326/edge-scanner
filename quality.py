"""Validate provider quotes before using them in calculations."""

from __future__ import annotations

import datetime as dt
import math

UTC = dt.timezone.utc
DEFAULT_MAX_AGE = 120


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def usable_game(game, now, max_age, stats, window_hours=24):
    """Return a normalized copy; never turn incomplete markets into two-way bets."""
    if not isinstance(game, dict):
        stats["invalid_events"] += 1
        return None
    commence = timestamp(game.get("commence_time"))
    if not commence or commence <= now:
        stats["started_or_invalid_events"] += 1
        return None
    if window_hours is not None and commence > now + dt.timedelta(hours=window_hours):
        stats["outside_window_events"] = stats.get("outside_window_events", 0) + 1
        return None
    home, away = game.get("home_team"), game.get("away_team")
    if not isinstance(home, str) or not isinstance(away, str) or not home or home == away:
        stats["invalid_events"] += 1
        return None
    books = game.get("bookmakers")
    if not isinstance(books, list):
        stats["invalid_events"] += 1
        return None
    clean_books = []
    seen_books = set()
    for book in books:
        if not isinstance(book, dict) or not isinstance(book.get("key"), str):
            stats["invalid_markets"] += 1
            continue
        if book["key"] in seen_books:
            continue
        seen_books.add(book["key"])
        clean_markets = []
        markets = book.get("markets")
        if not isinstance(markets, list):
            stats["invalid_markets"] += 1
            continue
        seen_markets = set()
        for market in markets:
            if (
                not isinstance(market, dict)
                or not isinstance(market.get("key"), str)
                or market.get("key")
                not in {
                    "h2h",
                    "spreads",
                    "totals",
                }
            ):
                continue
            key = market["key"]
            if key in seen_markets:
                continue
            seen_markets.add(key)
            updated = timestamp(market.get("last_update", book.get("last_update")))
            if not updated or (now - updated).total_seconds() < -5:
                stats["missing_or_invalid_timestamps"] += 1
                continue
            if (now - updated).total_seconds() > max_age:
                stats["stale_markets"] += 1
                continue
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, list) or len(outcomes) not in (2, 3):
                stats["invalid_markets"] += 1
                continue
            names, clean_outcomes = set(), []
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    break
                name = outcome.get("name")
                if not isinstance(name, str) or name in names:
                    break
                price = finite(outcome.get("price"))
                if price is None or price <= 1:
                    break
                names.add(name)
                clean = {**outcome, "price": price}
                if key != "h2h":
                    point = finite(outcome.get("point"))
                    if point is None or abs(point) > 10000:
                        break
                    clean["point"] = point
                limit = finite(outcome.get("bet_limit"))
                clean["bet_limit"] = limit if limit is not None and limit >= 0 else None
                clean_outcomes.append(clean)
            expected = {"Over", "Under"} if key == "totals" else {home, away}
            if key == "h2h" and len(outcomes) == 3:
                expected.add("Draw")
            valid = len(clean_outcomes) == len(outcomes) and names == expected
            if valid and key == "spreads":
                valid = math.isclose(sum(o["point"] for o in clean_outcomes), 0, abs_tol=1e-8)
            if valid and key == "totals":
                valid = (
                    clean_outcomes[0]["point"] == clean_outcomes[1]["point"]
                    and clean_outcomes[0]["point"] >= 0
                )
            if not valid:
                stats["invalid_markets"] += 1
                continue
            clean_markets.append(
                {**market, "outcomes": clean_outcomes, "last_update": updated.isoformat()}
            )
            stats["fresh_markets"] += 1
        if clean_markets:
            clean_books.append({**book, "markets": clean_markets})
    return {**game, "bookmakers": clean_books}


def quote_metadata(book, market, outcome):
    return {
        "last_update": market.get("last_update", book.get("last_update")),
        "bet_limit": outcome.get("bet_limit"),
    }


def execution_check(legs, stakes):
    """Provider limits are snapshots, never a promise of executable liquidity."""
    checks = []
    for leg, stake in zip(legs, stakes):
        limit = finite(leg.get("bet_limit"))
        checks.append(
            {
                "bookmaker": leg.get("bookmaker"),
                "stake": stake,
                "bet_limit": limit,
                "last_update": leg.get("last_update"),
                "within_limit": None if limit is None else stake <= limit,
            }
        )
    return {
        "legs": checks,
        "limits_verified": all(c["within_limit"] is True for c in checks),
        "exceeds_limit": any(c["within_limit"] is False for c in checks),
    }


def current_opportunities(opportunities, now, max_age):
    result = []
    for opportunity in opportunities:
        if "best_odds" in opportunity:
            legs = opportunity["best_odds"]
        elif "side_a" in opportunity:
            legs = [opportunity["side_a"], opportunity["side_b"]]
        else:
            legs = [opportunity["bet"], opportunity["sharp"]]
        updates = [timestamp(leg.get("last_update")) for leg in legs]
        commence = timestamp(opportunity.get("commence_time"))
        if not commence or any(update is None for update in updates):
            continue
        expiry = min([commence, *(update + dt.timedelta(seconds=max_age) for update in updates)])
        if expiry <= now:
            continue
        opportunity["valid_until"] = expiry.isoformat()
        result.append(opportunity)
    return result
