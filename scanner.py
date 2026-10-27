"""Core arbitrage scanning logic."""

from __future__ import annotations

import datetime as dt
import itertools
import math
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from config import (
    DEFAULT_BANKROLL,
    DEFAULT_COMMISSION,
    DEFAULT_KELLY_FRACTION,
    DEFAULT_MIDDLE_SORT,
    DEFAULT_REGION_KEYS,
    DEFAULT_SHARP_BOOK,
    DEFAULT_SPORT_KEYS,
    EDGE_BANDS,
    EXCHANGE_BOOKMAKERS,
    EXCHANGE_KEYS,
    KEY_NUMBER_SPORTS,
    MAX_MIDDLE_PROBABILITY,
    MIN_EDGE_PERCENT,
    MIN_MIDDLE_GAP,
    NFL_KEY_NUMBER_PROBABILITY,
    PROBABILITY_PER_INTEGER,
    REGION_CONFIG,
    ROI_BANDS,
    SHARP_BOOKS,
    SHOW_POSITIVE_EV_ONLY,
    SOFT_BOOK_KEYS,
    SPORT_DISPLAY_NAMES,
    markets_for_sport,
)

BASE_URL = "https://api.the-odds-api.com/v4"
MIDDLE_MARKETS = {"spreads", "totals"}
ALLOWED_PLUS_EV_MARKETS = {"h2h", "spreads", "totals"}
SOFT_BOOK_KEY_SET = set(SOFT_BOOK_KEYS)
SHARP_BOOK_MAP = {book["key"]: book for book in SHARP_BOOKS}


class ScannerError(Exception):
    """Raised for recoverable scanner issues."""


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _valid_price(value) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and price > 1 else None


def _clamp_commission(rate: float) -> float:
    if rate is None:
        return DEFAULT_COMMISSION
    return max(0.0, min(rate, 0.2))


def _normalize_regions(regions: Optional[Sequence[str]]) -> List[str]:
    if not regions:
        return list(DEFAULT_REGION_KEYS)
    valid = [region for region in regions if region in REGION_CONFIG]
    return valid or list(DEFAULT_REGION_KEYS)


def _ensure_sharp_region(regions: List[str], sharp_key: str) -> List[str]:
    """Always include the region required for the sharp reference (usually EU)."""
    required_region = SHARP_BOOK_MAP.get(sharp_key, {}).get("region", "eu")
    normalized = []
    seen = set()
    for region in regions:
        if region not in seen:
            normalized.append(region)
            seen.add(region)
    if required_region and required_region not in seen and required_region in REGION_CONFIG:
        normalized.append(required_region)
    return normalized


def _apply_commission(price: float, commission_rate: float, is_exchange: bool) -> float:
    if not is_exchange:
        return price
    edge = price - 1.0
    if edge <= 0:
        return price
    return 1.0 + edge * (1.0 - commission_rate)


def _sharp_priority(selected_key: str) -> List[dict]:
    priority = []
    seen = set()
    if selected_key in SHARP_BOOK_MAP:
        priority.append(SHARP_BOOK_MAP[selected_key])
        seen.add(selected_key)
    for book in SHARP_BOOKS:
        key = book.get("key")
        if key and key not in seen:
            priority.append(book)
            seen.add(key)
    return priority


def _points_match(point_a, point_b, tolerance: float = 1e-6) -> bool:
    if point_a is None and point_b is None:
        return True
    if point_a is None or point_b is None:
        return False
    try:
        return abs(float(point_a) - float(point_b)) <= tolerance
    except (TypeError, ValueError, OverflowError):
        return False


def _spread_gap_info(favorite_line: float, underdog_line: float) -> Optional[dict]:
    if favorite_line is None or underdog_line is None:
        return None
    if favorite_line >= 0 or underdog_line <= 0:
        return None
    fav_abs = abs(favorite_line)
    if fav_abs >= underdog_line:
        return None
    gap = round(underdog_line - fav_abs, 2)
    start = math.floor(fav_abs) + 1
    end = math.ceil(underdog_line) - 1
    if end < start:
        return None
    middle_integers = list(range(start, end + 1))
    if not middle_integers:
        return None
    return {
        "gap_points": round(gap, 2),
        "middle_integers": middle_integers,
        "integer_count": len(middle_integers),
    }


def _total_gap_info(over_line: float, under_line: float) -> Optional[dict]:
    if over_line is None or under_line is None:
        return None
    if over_line >= under_line:
        return None
    gap = under_line - over_line
    lower = math.floor(over_line) + 1
    upper = math.ceil(under_line) - 1
    if upper < lower:
        return None
    middle_integers = list(range(lower, upper + 1))
    if not middle_integers:
        return None
    return {
        "gap_points": round(gap, 2),
        "middle_integers": middle_integers,
        "integer_count": len(middle_integers),
    }


def _build_sharp_reference(
    bookmaker: dict, commission_rate: float, is_exchange: bool
) -> Dict[Tuple[str, str], dict]:
    """Return mapping of (market_key, line_key) to vig-free odds."""
    line_map: Dict[Tuple[str, str], List[dict]] = {}
    for market in bookmaker.get("markets", []):
        market_key = market.get("key")
        if market_key not in ALLOWED_PLUS_EV_MARKETS:
            continue
        for outcome in market.get("outcomes", []):
            price = _valid_price(outcome.get("price"))
            if price is None:
                continue
            line_key = _line_key(market_key, outcome)
            if not line_key:
                continue
            try:
                price_val = float(price)
            except (TypeError, ValueError, OverflowError):
                continue
            adjusted_price = _apply_commission(price_val, commission_rate, is_exchange)
            line_map.setdefault((market_key, line_key), []).append(
                {
                    "name": (outcome.get("name") or "").strip().lower(),
                    "display_name": outcome.get("name") or "",
                    "price": adjusted_price,
                    "raw_price": price_val,
                    "point": outcome.get("point"),
                }
            )
    references: Dict[Tuple[str, str], dict] = {}
    for key, entries in line_map.items():
        if len(entries) != 2:
            continue
        first, second = entries
        fair_a, fair_b, vig_percent = _remove_vig(first["price"], second["price"])
        prob_a = 1 / fair_a if fair_a else 0.0
        prob_b = 1 / fair_b if fair_b else 0.0
        references[key] = {
            "vig_percent": round(vig_percent, 2),
            "outcomes": {
                first["name"]: {
                    "fair_odds": fair_a,
                    "true_probability": prob_a,
                    "sharp_odds": first["raw_price"],
                    "opponent_odds": second["raw_price"],
                    "opponent_name": second["display_name"],
                    "display_name": first["display_name"],
                    "point": first.get("point"),
                },
                second["name"]: {
                    "fair_odds": fair_b,
                    "true_probability": prob_b,
                    "sharp_odds": second["raw_price"],
                    "opponent_odds": first["raw_price"],
                    "opponent_name": first["display_name"],
                    "display_name": second["display_name"],
                    "point": second.get("point"),
                },
            },
        }
    return references


def _two_way_outcomes(bookmaker: dict) -> Dict[Tuple[str, str], List[dict]]:
    line_map: Dict[Tuple[str, str], List[dict]] = {}
    for market in bookmaker.get("markets", []):
        market_key = market.get("key")
        if market_key not in ALLOWED_PLUS_EV_MARKETS:
            continue
        for outcome in market.get("outcomes", []):
            price = _valid_price(outcome.get("price"))
            if price is None:
                continue
            line_key = _line_key(market_key, outcome)
            if not line_key:
                continue
            try:
                display_price = float(price)
            except (TypeError, ValueError, OverflowError):
                continue
            line_map.setdefault((market_key, line_key), []).append(
                {
                    "name": (outcome.get("name") or "").strip().lower(),
                    "display_name": outcome.get("name") or "",
                    "price": display_price,
                    "point": outcome.get("point"),
                }
            )
    return {key: entries for key, entries in line_map.items() if len(entries) == 2}


def _estimate_middle_probability(
    middle_integers: List[int], sport_key: str, market_key: str
) -> float:
    if not middle_integers:
        return 0.0
    lookup_key = f"{sport_key}_{market_key}"
    base_prob = PROBABILITY_PER_INTEGER.get(lookup_key, PROBABILITY_PER_INTEGER["default"])
    total = 0.0
    is_key_sport = sport_key in KEY_NUMBER_SPORTS and market_key == "spreads"
    for integer in middle_integers:
        abs_int = abs(integer)
        if is_key_sport and abs_int in NFL_KEY_NUMBER_PROBABILITY:
            total += NFL_KEY_NUMBER_PROBABILITY[abs_int]
        else:
            total += base_prob
    return min(total, MAX_MIDDLE_PROBABILITY)


def _calculate_middle_stakes(odds_a: float, odds_b: float, total_stake: float) -> Tuple[float, float]:
    if total_stake <= 0 or odds_a <= 1 or odds_b <= 1:
        return 0.0, 0.0
    # Equalize the payouts when only one side wins, including the lost stake.
    stake_a = total_stake * odds_b / (odds_a + odds_b)
    stake_a = round(stake_a, 2)
    stake_b = round(total_stake - stake_a, 2)
    return stake_a, stake_b


def _calculate_middle_outcomes(
    stake_a: float, stake_b: float, odds_a: float, odds_b: float
) -> dict:
    total = stake_a + stake_b
    payout_a = round(stake_a * odds_a, 2)
    payout_b = round(stake_b * odds_b, 2)
    win_both = round((payout_a + payout_b) - total, 2)
    side_a_only = round(payout_a - total, 2)
    side_b_only = round(payout_b - total, 2)
    typical_miss = round((side_a_only + side_b_only) / 2, 2)
    return {
        "win_both_profit": win_both,
        "side_a_wins_profit": side_a_only,
        "side_b_wins_profit": side_b_only,
        "typical_miss_profit": typical_miss,
    }


def _calculate_middle_ev(
    win_both_profit: float,
    side_a_profit: float,
    side_b_profit: float,
    probability: float,
) -> float:
    probability = max(0.0, min(probability, 1.0))
    miss_probability = 1.0 - probability
    miss_ev = 0.5 * side_a_profit + 0.5 * side_b_profit
    value = (probability * win_both_profit) + (miss_probability * miss_ev)
    return round(value, 2)


def _format_middle_zone(
    description_source: str, middle_integers: List[int], is_total: bool
) -> str:
    if not middle_integers:
        return description_source
    middle_integers = sorted(middle_integers)
    if len(middle_integers) == 1:
        range_text = str(middle_integers[0])
    else:
        range_text = f"{middle_integers[0]}-{middle_integers[-1]}"
    if is_total:
        return f"Total {range_text}"
    return f"{description_source} by {range_text}"


def _request(url: str, params: Dict[str, str]) -> requests.Response:
    try:
        resp = requests.get(url, params=params, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network error
        # Request exceptions can contain the full URL, including the API key.
        raise ScannerError("Could not reach The Odds API. Please try again.") from exc
    if resp.status_code >= 400:
        try:
            payload = resp.json()
            message = (payload.get("message") or payload.get("error")) if isinstance(payload, dict) else None
        except ValueError:
            message = None
        message = str(message or f"API request failed ({resp.status_code})")
        if params.get("apiKey"):
            message = message.replace(params["apiKey"], "[redacted]")
        raise ScannerError(message)
    return resp


def fetch_sports(api_key: str) -> List[dict]:
    url = f"{BASE_URL}/sports/"
    resp = _request(url, {"apiKey": api_key})
    try:
        return _parse_list_response(resp, "sports list")
    except ValueError as exc:  # pragma: no cover - malformed payload
        raise ScannerError("Failed to parse sports list") from exc


def filter_sports(
    sports: Sequence[dict], requested: Sequence[str], all_sports: bool
) -> List[dict]:
    if all_sports:
        return [s for s in sports if s.get("active") and not s.get("has_outrights")]
    requested_set = set(requested) if requested else set(DEFAULT_SPORT_KEYS)
    return [s for s in sports if s.get("key") in requested_set and s.get("active")]


def fetch_odds_for_sport(
    api_key: str, sport_key: str, markets: Sequence[str], regions: Sequence[str]
) -> List[dict]:
    url = f"{BASE_URL}/sports/{sport_key}/odds/"
    params = {
        "apiKey": api_key,
        "regions": ",".join(regions),
        "markets": ",".join(markets),
        "oddsFormat": "decimal",
        "commenceTimeFrom": _iso_now(),
    }
    resp = _request(url, params)
    try:
        return _parse_list_response(resp, f"odds for {sport_key}")
    except ValueError as exc:  # pragma: no cover
        raise ScannerError(f"Failed to parse odds for {sport_key}") from exc


OutcomeInfo = Dict[str, object]
LineMap = Dict[str, Dict[str, OutcomeInfo]]


def _parse_list_response(response: requests.Response, description: str) -> List[dict]:
    payload = response.json()
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ScannerError(f"Unexpected response for {description}")
    return payload


def _line_key(market: str, outcome: dict) -> Optional[str]:
    if market == "h2h":
        return "moneyline"
    point = outcome.get("point")
    if point is None:
        return None
    try:
        point_val = float(point)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(point_val):
        return None
    # Whole lines can push; quarter lines require split-stake settlement.
    if not math.isclose(abs(point_val) % 1, 0.5):
        return None
    if market == "spreads":
        return f"spread_{abs(point_val):.2f}"
    return f"total_{point_val:.2f}"


def _record_best_prices(
    markets: List[dict], market_key: str, commission_rate: float, home_team: Optional[str] = None
) -> LineMap:
    lines: LineMap = {}
    for book in markets:
        bookmaker = book.get("title") or book.get("key")
        bookmaker_key = book.get("key") or bookmaker
        for market in book.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                price = _valid_price(outcome.get("price"))
                if price is None:
                    continue
                key = _line_key(market_key, outcome)
                if key is None:
                    continue
                normalized_point = outcome.get("point")
                if market_key == "spreads" and home_team:
                    point = float(normalized_point)
                    home_point = point if outcome.get("name") == home_team else -point
                    key = f"spread_{home_point:.6f}"
                entry = lines.setdefault(key, {})
                name = outcome.get("name", "")
                existing = entry.get(name)
                display_price = float(price)
                is_exchange = bookmaker_key in EXCHANGE_KEYS
                effective_price = _apply_commission(display_price, commission_rate, is_exchange)
                if not existing or effective_price > existing["effective_price"]:
                    entry[name] = {
                        "effective_price": effective_price,
                        "display_price": display_price,
                        "bookmaker": bookmaker,
                        "bookmaker_key": bookmaker_key,
                        "name": name,
                        "point": normalized_point,
                        "is_exchange": is_exchange,
                    }
    return lines


def _collect_market_entries(
    game: dict, market_key: str, stake_total: float, commission_rate: float
) -> List[dict]:
    bookmakers = game.get("bookmakers", [])
    markets = [
        {
            "key": book.get("key"),
            "title": book.get("title") or book.get("key"),
            "markets": book.get("markets", []),
        }
        for book in bookmakers
    ]
    best_lines = _record_best_prices(markets, market_key, commission_rate, game.get("home_team"))
    entries = []
    for line_key, offers in best_lines.items():
        # Skip lines that aren't genuine two-way markets (e.g., moneyline with a draw option).
        if len(offers) != 2:
            continue
        outcomes = list(offers.values())
        names = {o["name"] for o in outcomes}
        expected = ({"Over", "Under"} if market_key == "totals"
                    else {game.get("home_team"), game.get("away_team")})
        if names != expected:
            continue
        if market_key == "spreads":
            try:
                point_values = [float(o.get("point")) for o in outcomes]
            except (TypeError, ValueError, OverflowError):
                continue
            if any(p is None for p in point_values):
                continue
            # Require opposite sides of the spread (one positive, one negative) and different teams.
            if point_values[0] * point_values[1] >= 0:
                continue
            outcome_names = {o.get("name", "").strip().lower() for o in outcomes}
            if len(outcome_names) < 2:
                continue
        has_exchange = any(o.get("is_exchange") for o in outcomes)
        outcome_payload = [
            {
                "outcome": o["name"],
                "bookmaker": o["bookmaker"],
                "bookmaker_key": o.get("bookmaker_key"),
                "price": o["display_price"],
                "effective_price": o["effective_price"],
                "point": o.get("point"),
                "is_exchange": o.get("is_exchange", False),
            }
            for o in outcomes
        ]
        net_stake_info = _calculate_stakes(outcomes, stake_total, price_field="effective_price")
        gross_stake_info = (
            _calculate_stakes(outcomes, stake_total, price_field="display_price") if has_exchange else None
        )
        net_roi = net_stake_info["roi_percent"]
        gross_roi = gross_stake_info["roi_percent"] if gross_stake_info else net_roi
        if not has_exchange and net_roi <= 0:
            continue
        if has_exchange and net_roi <= 0 and gross_roi <= 0:
            continue
        exchange_names = {
            o.get("bookmaker")
            or EXCHANGE_BOOKMAKERS.get(o.get("bookmaker_key"), {}).get("name")
            for o in outcomes
            if o.get("is_exchange")
        }
        exchange_books = sorted(name for name in exchange_names if name)
        entry = {
            "sport": game.get("sport_key"),
            "sport_display": game.get("sport_display")
            or SPORT_DISPLAY_NAMES.get(game.get("sport_key", ""), game.get("sport_key")),
            "sport_title": game.get("sport_title"),
            "event": f"{game.get('away_team')} vs {game.get('home_team')}",
            "commence_time": game.get("commence_time"),
            "market": market_key,
            "roi_percent": round(net_roi, 2),
            "gross_roi_percent": round(gross_roi, 2) if has_exchange else round(net_roi, 2),
            "best_odds": outcome_payload,
            "stakes": net_stake_info,
            "gross_stakes": gross_stake_info,
            "has_exchange": has_exchange,
            "exchange_books": exchange_books,
        }
        entries.append(entry)
    return entries


def _collect_middle_opportunities(
    game: dict, market_key: str, stake_total: float, commission_rate: float
) -> List[dict]:
    if market_key not in MIDDLE_MARKETS or stake_total <= 0:
        return []
    bookmakers = game.get("bookmakers", [])
    offers = []
    for book in bookmakers:
        bookmaker_title = book.get("title") or book.get("key")
        bookmaker_key = book.get("key") or bookmaker_title
        markets = book.get("markets", [])
        for market in markets:
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                point = outcome.get("point")
                price = _valid_price(outcome.get("price"))
                if point is None or price is None:
                    continue
                name = outcome.get("name") or ""
                try:
                    point_value = float(point)
                except (TypeError, ValueError, OverflowError):
                    continue
                if not math.isfinite(point_value):
                    continue
                display_price = float(price)
                is_exchange = bookmaker_key in EXCHANGE_KEYS
                effective_price = _apply_commission(display_price, commission_rate, is_exchange)
                offers.append(
                    {
                        "pair_key": f"{bookmaker_key}:{name}:{point_value}",
                        "bookmaker": bookmaker_title,
                        "bookmaker_key": bookmaker_key,
                        "team": name,
                        "line": point_value,
                        "display_price": display_price,
                        "effective_price": effective_price,
                        "is_exchange": is_exchange,
                    }
                )
    entries: List[dict] = []
    seen_pairs = set()
    for offer_a, offer_b in itertools.combinations(offers, 2):
        if offer_a["bookmaker_key"] == offer_b["bookmaker_key"]:
            continue
        pair_signature = tuple(sorted([offer_a["pair_key"], offer_b["pair_key"]]))
        if pair_signature in seen_pairs:
            continue
        seen_pairs.add(pair_signature)
        middle_entry = _build_middle_entry(game, market_key, offer_a, offer_b, stake_total)
        if middle_entry:
            entries.append(middle_entry)
    return entries


def _collect_plus_ev_opportunities(
    game: dict,
    markets: Sequence[str],
    sharp_priority: List[dict],
    commission_rate: float,
    min_edge_percent: float,
    bankroll: float,
    kelly_fraction: float,
) -> List[dict]:
    bookmakers = game.get("bookmakers", [])
    sharp_meta = None
    sharp_bookmaker = None
    for candidate in sharp_priority:
        for book in bookmakers:
            if book.get("key") == candidate.get("key"):
                sharp_meta = candidate
                sharp_bookmaker = book
                break
        if sharp_bookmaker:
            break
    if not sharp_bookmaker or not sharp_meta:
        return []
    is_sharp_exchange = sharp_meta.get("type") == "exchange"
    sharp_reference = _build_sharp_reference(sharp_bookmaker, commission_rate, is_sharp_exchange)
    if not sharp_reference:
        return []
    opportunities: List[dict] = []
    for book in bookmakers:
        key = book.get("key")
        if not key:
            continue
        if key == sharp_meta.get("key"):
            continue
        if SOFT_BOOK_KEY_SET and key not in SOFT_BOOK_KEY_SET:
            continue
        bookmaker_title = book.get("title") or key
        is_exchange = key in EXCHANGE_KEYS
        soft_lines = _two_way_outcomes(book)
        if not soft_lines:
            continue
        for (market_key, line_key), entries in soft_lines.items():
            if market_key not in markets:
                continue
            reference = sharp_reference.get((market_key, line_key))
            if not reference:
                continue
            for entry in entries:
                name_norm = entry["name"]
                sharp_outcome = reference["outcomes"].get(name_norm)
                if not sharp_outcome:
                    continue
                soft_point = entry.get("point")
                sharp_point = sharp_outcome.get("point")
                has_point = sharp_point is not None or soft_point is not None
                if has_point and not _points_match(soft_point, sharp_point):
                    continue
                display_price = entry["price"]
                effective_price = _apply_commission(display_price, commission_rate, is_exchange)
                fair_odds = sharp_outcome["fair_odds"]
                gross_edge = _calculate_edge_percent(display_price, fair_odds)
                net_edge = _calculate_edge_percent(effective_price, fair_odds)
                if net_edge < min_edge_percent:
                    continue
                true_probability = sharp_outcome["true_probability"]
                ev_per_100 = _calculate_ev(true_probability, effective_price, 100.0)
                full_pct, fraction_pct, recommended = _kelly_stake(
                    true_probability, effective_price, bankroll, kelly_fraction
                )
                opportunity = {
                    "id": str(uuid.uuid4()),
                    "sport": game.get("sport_key"),
                    "sport_display": game.get("sport_display")
                    or SPORT_DISPLAY_NAMES.get(game.get("sport_key", ""), game.get("sport_key")),
                    "event": f"{game.get('away_team')} vs {game.get('home_team')}",
                    "commence_time": game.get("commence_time"),
                    "market": market_key,
                    "market_point": sharp_outcome.get("point"),
                    "bet": {
                        "outcome": entry.get("display_name") or "",
                        "soft_book": bookmaker_title,
                        "soft_key": key,
                        "soft_odds": display_price,
                        "effective_odds": effective_price,
                        "is_exchange": is_exchange,
                        "point": soft_point,
                    },
                    "sharp": {
                        "book": sharp_meta.get("name") or sharp_bookmaker.get("title") or sharp_meta.get("key"),
                        "key": sharp_meta.get("key"),
                        "odds": sharp_outcome["sharp_odds"],
                        "opponent_odds": sharp_outcome["opponent_odds"],
                        "opponent": sharp_outcome["opponent_name"],
                        "fair_odds": sharp_outcome["fair_odds"],
                        "true_probability": sharp_outcome["true_probability"],
                        "true_probability_percent": round(sharp_outcome["true_probability"] * 100, 2),
                        "vig_percent": reference.get("vig_percent"),
                    },
                    "edge_percent": round(net_edge, 2),
                    "net_edge_percent": round(net_edge, 2),
                    "gross_edge_percent": round(gross_edge, 2),
                    "ev_per_100": round(ev_per_100, 2),
                    "kelly": {
                        "full_percent": full_pct,
                        "fraction_percent": fraction_pct,
                        "recommended_stake": recommended,
                    },
                    "has_exchange": is_exchange,
                }
                opportunities.append(opportunity)
    return opportunities


def _build_middle_entry(
    game: dict,
    market_key: str,
    offer_a: dict,
    offer_b: dict,
    stake_total: float,
) -> Optional[dict]:
    sport_key = game.get("sport_key") or ""
    home_team = game.get("home_team") or "Home"
    away_team = game.get("away_team") or "Away"
    favorite_offer: Optional[dict] = None
    underdog_offer: Optional[dict] = None
    over_offer: Optional[dict] = None
    under_offer: Optional[dict] = None
    gap_info: Optional[dict] = None
    is_total = market_key == "totals"

    if market_key == "spreads":
        if offer_a["line"] < 0 and offer_b["line"] > 0:
            favorite_offer, underdog_offer = offer_a, offer_b
        elif offer_b["line"] < 0 and offer_a["line"] > 0:
            favorite_offer, underdog_offer = offer_b, offer_a
        else:
            return None
        # Require opposite teams so we're not pairing two prices on the same side
        team_a = (favorite_offer.get("team") or "").strip().lower()
        team_b = (underdog_offer.get("team") or "").strip().lower()
        if not team_a or not team_b or team_a == team_b:
            return None
        gap_info = _spread_gap_info(favorite_offer["line"], underdog_offer["line"])
        if not gap_info:
            return None
        side_a = favorite_offer
        side_b = underdog_offer
        descriptor_name = side_a["team"]
    else:
        names = {
            offer_a["team"].strip().lower(): offer_a,
            offer_b["team"].strip().lower(): offer_b,
        }
        for name, offer in names.items():
            if "over" in name:
                over_offer = offer
            elif "under" in name:
                under_offer = offer
        if not over_offer or not under_offer:
            return None
        if over_offer["line"] >= under_offer["line"]:
            return None
        gap_info = _total_gap_info(over_offer["line"], under_offer["line"])
        side_a = over_offer
        side_b = under_offer
        descriptor_name = "Total"

    if not gap_info:
        return None
    middle_integers = gap_info["middle_integers"]
    if not middle_integers:
        return None
    middle_probability = _estimate_middle_probability(middle_integers, sport_key, market_key)
    if middle_probability <= 0:
        return None

    stake_a, stake_b = _calculate_middle_stakes(
        side_a["effective_price"], side_b["effective_price"], stake_total
    )
    if stake_a <= 0 or stake_b <= 0:
        return None
    outcomes = _calculate_middle_outcomes(stake_a, stake_b, side_a["effective_price"], side_b["effective_price"])
    ev_dollars = _calculate_middle_ev(
        outcomes["win_both_profit"],
        outcomes["side_a_wins_profit"],
        outcomes["side_b_wins_profit"],
        middle_probability,
    )
    ev_percent = round((ev_dollars / stake_total) * 100, 2) if stake_total else 0.0
    has_exchange = side_a["is_exchange"] or side_b["is_exchange"]
    gross_ev_percent = None
    if has_exchange:
        gross_stake_a, gross_stake_b = _calculate_middle_stakes(
            side_a["display_price"], side_b["display_price"], stake_total
        )
        if gross_stake_a > 0 and gross_stake_b > 0:
            gross_outcomes = _calculate_middle_outcomes(
                gross_stake_a, gross_stake_b, side_a["display_price"], side_b["display_price"]
            )
            gross_ev = _calculate_middle_ev(
                gross_outcomes["win_both_profit"],
                gross_outcomes["side_a_wins_profit"],
                gross_outcomes["side_b_wins_profit"],
                middle_probability,
            )
            gross_ev_percent = round((gross_ev / stake_total) * 100, 2) if stake_total else 0.0

    key_numbers_crossed: List[int] = []
    includes_key_number = False
    if sport_key in KEY_NUMBER_SPORTS and market_key == "spreads":
        key_numbers_crossed = [
            integer for integer in middle_integers if integer in NFL_KEY_NUMBER_PROBABILITY
        ]
        includes_key_number = bool(key_numbers_crossed)

    middle_zone = _format_middle_zone(descriptor_name, middle_integers, is_total)
    best_odds = (
        {
            "team": side_a["team"],
            "line": side_a["line"],
            "price": side_a["display_price"],
            "effective_price": side_a["effective_price"],
            "bookmaker": side_a["bookmaker"],
            "is_exchange": side_a["is_exchange"],
        },
        {
            "team": side_b["team"],
            "line": side_b["line"],
            "price": side_b["display_price"],
            "effective_price": side_b["effective_price"],
            "bookmaker": side_b["bookmaker"],
            "is_exchange": side_b["is_exchange"],
        },
    )
    stakes_payload = {
        "total": stake_total,
        "side_a": {
            "stake": stake_a,
            "payout": round(stake_a * side_a["effective_price"], 2),
        },
        "side_b": {
            "stake": stake_b,
            "payout": round(stake_b * side_b["effective_price"], 2),
        },
    }

    opportunity = {
        "id": str(uuid.uuid4()),
        "sport": sport_key,
        "sport_display": game.get("sport_display")
        or SPORT_DISPLAY_NAMES.get(sport_key, sport_key),
        "event": f"{game.get('away_team', away_team)} vs {game.get('home_team', home_team)}",
        "commence_time": game.get("commence_time"),
        "market": market_key,
        "side_a": best_odds[0],
        "side_b": best_odds[1],
        "gap": {
            "points": gap_info["gap_points"],
            "middle_integers": middle_integers,
            "integer_count": gap_info["integer_count"],
            "includes_key_number": includes_key_number,
            "key_numbers_crossed": key_numbers_crossed,
        },
        "middle_zone": middle_zone,
        "middle_probability": middle_probability,
        "probability_percent": round(middle_probability * 100, 2),
        "stakes": stakes_payload,
        "outcomes": outcomes,
        "ev_dollars": ev_dollars,
        "ev_percent": ev_percent,
        "has_exchange": has_exchange,
        "gross_ev_percent": gross_ev_percent,
        "sport_title": game.get("sport_title"),
    }
    return opportunity


def _calculate_stakes(outcomes: List[dict], stake_total: float, price_field: str) -> dict:
    if stake_total <= 0 or len(outcomes) < 2:
        return {
            "total": 0.0,
            "breakdown": [],
            "guaranteed_profit": 0.0,
            "roi_percent": 0.0,
        }
    inverses = []
    for outcome in outcomes:
        price = outcome.get(price_field)
        if not price or price <= 0:
            return {
                "total": 0.0,
                "breakdown": [],
                "guaranteed_profit": 0.0,
                "roi_percent": 0.0,
            }
        inverses.append(1 / price)
    inverse_sum = sum(inverses)
    if inverse_sum <= 0:
        return {
            "total": 0.0,
            "breakdown": [],
            "guaranteed_profit": 0.0,
            "roi_percent": 0.0,
        }
    breakdown = []
    allocated = 0.0
    for index, (outcome, inv) in enumerate(zip(outcomes, inverses)):
        fraction = inv / inverse_sum
        stake_value = (round(stake_total - allocated, 2) if index == len(outcomes) - 1
                       else round(stake_total * fraction, 2))
        allocated += stake_value
        price_used = outcome.get(price_field)
        display_price = outcome.get("display_price", price_used)
        payout = round(stake_value * price_used, 2)
        breakdown.append(
            {
                "outcome": outcome["name"],
                "bookmaker": outcome["bookmaker"],
                "price": display_price,
                "effective_price": outcome.get("effective_price", price_used),
                "point": outcome.get("point"),
                "stake": stake_value,
                "payout": payout,
                "fraction": fraction,
                "is_exchange": outcome.get("is_exchange", False),
            }
        )
    min_payout = min(item["payout"] for item in breakdown) if breakdown else 0.0
    guaranteed = round(min_payout - stake_total, 2)
    roi = round((guaranteed / stake_total) * 100, 4) if stake_total else 0.0
    return {
        "total": stake_total,
        "breakdown": breakdown,
        "guaranteed_profit": guaranteed,
        "roi_percent": roi,
    }


def _summaries(
    opportunities: List[dict],
    sports_scanned: int,
    events_scanned: int,
    total_profit: float,
    api_calls_used: int,
) -> dict:
    by_sport: Dict[str, int] = {}
    band_counts: Dict[str, int] = {label: 0 for *_, label in ROI_BANDS}
    for opp in opportunities:
        key = opp.get("sport_display") or opp.get("sport") or "unknown"
        by_sport[key] = by_sport.get(key, 0) + 1
        roi = opp.get("roi_percent", 0)
        for lower, upper, label in ROI_BANDS:
            if lower <= roi < upper:
                band_counts[label] += 1
                break
    return {
        "by_sport": by_sport,
        "by_roi_band": band_counts,
        "sports_scanned": sports_scanned,
        "events_scanned": events_scanned,
        "api_calls_used": api_calls_used,
        "total_guaranteed_profit": round(total_profit, 2),
    }


def _middle_summary(opportunities: List[dict]) -> dict:
    count = len(opportunities)
    positive = [opp for opp in opportunities if opp.get("ev_percent", 0) > 0]
    avg_ev = (
        round(sum(opp.get("ev_percent", 0) for opp in opportunities) / count, 2)
        if count
        else 0.0
    )
    best = max(opportunities, key=lambda o: o.get("ev_percent", 0), default=None)
    by_sport: Dict[str, int] = {}
    key_numbers: Dict[str, int] = {}
    for opp in opportunities:
        sport = opp.get("sport_display") or opp.get("sport") or "unknown"
        by_sport[sport] = by_sport.get(sport, 0) + 1
        for key_number in opp.get("gap", {}).get("key_numbers_crossed", []):
            key_numbers[str(key_number)] = key_numbers.get(str(key_number), 0) + 1
    return {
        "count": count,
        "positive_count": len(positive),
        "average_ev_percent": avg_ev,
        "best_ev": {
            "ev_percent": best.get("ev_percent") if best else None,
            "event": best.get("event") if best else None,
            "sport": best.get("sport_display") if best else None,
        }
        if best
        else None,
        "by_sport": by_sport,
        "key_numbers": key_numbers,
    }


def _deduplicate_middles(opportunities: List[dict]) -> List[dict]:
    best_by_key: Dict[tuple, dict] = {}
    for opp in opportunities:
        key = (
            opp.get("event"),
            opp.get("sport"),
            opp.get("commence_time"),
            opp.get("market"),
            opp.get("side_a", {}).get("team"),
            tuple(opp.get("gap", {}).get("middle_integers", [])),
        )
        if key not in best_by_key or opp.get("ev_percent", 0) > best_by_key[key].get("ev_percent", 0):
            best_by_key[key] = opp
    return list(best_by_key.values())


def _plus_ev_summary(opportunities: List[dict]) -> dict:
    count = len(opportunities)
    avg_edge = (
        round(sum(opp.get("edge_percent", 0) for opp in opportunities) / count, 2)
        if count
        else 0.0
    )
    best = max(opportunities, key=lambda o: o.get("edge_percent", 0), default=None)
    total_ev = round(sum(opp.get("ev_per_100", 0) for opp in opportunities), 2)
    by_sport: Dict[str, int] = {}
    by_edge_band: Dict[str, int] = {label: 0 for *_, label in EDGE_BANDS}
    for opp in opportunities:
        sport = opp.get("sport_display") or opp.get("sport") or "Other"
        by_sport[sport] = by_sport.get(sport, 0) + 1
        edge = opp.get("edge_percent", 0)
        for lower, upper, label in EDGE_BANDS:
            if lower <= edge < upper:
                by_edge_band[label] += 1
                break
    return {
        "count": count,
        "average_edge_percent": avg_edge,
        "total_ev_per_100": total_ev,
        "best_edge": {
            "edge_percent": best.get("edge_percent") if best else None,
            "event": best.get("event") if best else None,
            "sport": best.get("sport_display") if best else None,
        }
        if best
        else None,
        "by_sport": by_sport,
        "by_edge_band": by_edge_band,
    }


def _deduplicate_plus_ev(opportunities: List[dict]) -> List[dict]:
    best_by_key: Dict[tuple, dict] = {}
    for opp in opportunities:
        bet = opp.get("bet", {})
        point = bet.get("point")
        if point is None:
            point = opp.get("market_point")
        try:
            normalized_point = float(point) if point is not None else None
        except (TypeError, ValueError, OverflowError):
            normalized_point = None
        key = (
            opp.get("event"),
            opp.get("sport"),
            opp.get("commence_time"),
            opp.get("market"),
            (bet.get("outcome") or "").strip().lower(),
            normalized_point,
        )
        existing = best_by_key.get(key)
        if not existing or (opp.get("edge_percent", 0) > existing.get("edge_percent", 0)):
            best_by_key[key] = opp
    return list(best_by_key.values())


def run_scan(
    api_key: str,
    sports: Optional[List[str]] = None,
    all_sports: bool = False,
    stake_amount: float = 100.0,
    regions: Optional[Sequence[str]] = None,
    commission_rate: float = DEFAULT_COMMISSION,
    sharp_book: str = DEFAULT_SHARP_BOOK,
    min_edge_percent: float = MIN_EDGE_PERCENT,
    bankroll: float = DEFAULT_BANKROLL,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
) -> dict:
    if not api_key:
        return {"success": False, "error": "API key is required", "error_code": 400}
    if stake_amount is None or stake_amount <= 0:
        stake_amount = 100.0
    normalized_regions = _normalize_regions(regions)
    normalized_regions = _ensure_sharp_region(normalized_regions, sharp_book or DEFAULT_SHARP_BOOK)
    if not normalized_regions:
        return {
            "success": False,
            "error": "At least one region must be selected",
            "error_code": 400,
        }
    commission_rate = _clamp_commission(commission_rate)
    try:
        sports_list = fetch_sports(api_key)
    except ScannerError as exc:
        return {"success": False, "error": str(exc), "error_code": 500}

    filtered = filter_sports(sports_list, sports or DEFAULT_SPORT_KEYS, all_sports)

    arb_opportunities: List[dict] = []
    middle_opportunities: List[dict] = []
    plus_ev_opportunities: List[dict] = []
    events_scanned = 0
    total_profit = 0.0
    sport_errors: List[dict] = []
    successful_sports = 0
    api_calls_used = 0
    sharp_priority = _sharp_priority(sharp_book or DEFAULT_SHARP_BOOK)

    for sport in filtered:
        sport_key = sport.get("key")
        if not sport_key:
            continue
        markets = markets_for_sport(sport_key)
        api_calls_used += 1
        try:
            events = fetch_odds_for_sport(api_key, sport_key, markets, normalized_regions)
        except ScannerError as exc:
            sport_errors.append(
                {
                    "sport_key": sport_key,
                    "sport": sport.get("title")
                    or SPORT_DISPLAY_NAMES.get(sport_key, sport_key),
                    "error": str(exc),
                }
            )
            continue
        successful_sports += 1
        events_scanned += len(events)
        for game in events:
            game["sport_key"] = sport_key
            game["sport_title"] = sport.get("title")
            game["sport_display"] = SPORT_DISPLAY_NAMES.get(sport_key, sport_key)
            for market_key in markets:
                new_entries = _collect_market_entries(
                    game, market_key, stake_amount, commission_rate
                )
                for entry in new_entries:
                    total_profit += entry["stakes"].get("guaranteed_profit", 0.0)
                arb_opportunities.extend(new_entries)
                if market_key in MIDDLE_MARKETS:
                    middle_entries = _collect_middle_opportunities(
                        game, market_key, stake_amount, commission_rate
                    )
                    middle_opportunities.extend(middle_entries)
            plus_entries = _collect_plus_ev_opportunities(
                game,
                markets,
                sharp_priority,
                commission_rate,
                min_edge_percent,
                bankroll,
                kelly_fraction,
            )
            plus_ev_opportunities.extend(plus_entries)

    arb_opportunities.sort(key=lambda x: x["roi_percent"], reverse=True)
    middle_opportunities.sort(key=lambda x: x["ev_percent"], reverse=True)
    middle_opportunities = _deduplicate_middles(middle_opportunities)
    plus_ev_opportunities = _deduplicate_plus_ev(plus_ev_opportunities)
    plus_ev_opportunities.sort(key=lambda x: x.get("edge_percent", 0), reverse=True)
    arb_summary = _summaries(
        arb_opportunities, successful_sports, events_scanned, total_profit, api_calls_used
    )
    middle_summary = _middle_summary(middle_opportunities)
    plus_ev_summary = _plus_ev_summary(plus_ev_opportunities)
    if filtered and not successful_sports:
        return {"success": False, "error": "All selected sports failed to load.",
                "error_code": 502, "sport_errors": sport_errors}
    return {
        "success": True,
        "scan_time": _iso_now(),
        "arbitrage": {
            "opportunities": arb_opportunities,
            "opportunities_count": len(arb_opportunities),
            "summary": arb_summary,
            "stake_amount": stake_amount,
        },
        "middles": {
            "opportunities": middle_opportunities,
            "opportunities_count": len(middle_opportunities),
            "summary": middle_summary,
            "stake_amount": stake_amount,
            "defaults": {
                "min_gap": MIN_MIDDLE_GAP,
                "sort": DEFAULT_MIDDLE_SORT,
                "positive_only": SHOW_POSITIVE_EV_ONLY,
            },
        },
        "plus_ev": {
            "opportunities": plus_ev_opportunities,
            "opportunities_count": len(plus_ev_opportunities),
            "summary": plus_ev_summary,
            "defaults": {
                "sharp_book": sharp_book or DEFAULT_SHARP_BOOK,
                "min_edge_percent": min_edge_percent,
                "bankroll": bankroll,
                "kelly_fraction": kelly_fraction,
            },
        },
        "sport_errors": sport_errors,
        "partial": bool(sport_errors),
        "regions": normalized_regions,
        "commission_rate": commission_rate,
    }
def _remove_vig(odds_a: float, odds_b: float) -> Tuple[float, float, float]:
    """Return fair odds for both sides plus vig percent."""
    if odds_a <= 1 or odds_b <= 1:
        return odds_a, odds_b, 0.0
    implied_a = 1 / odds_a
    implied_b = 1 / odds_b
    total_implied = implied_a + implied_b
    if total_implied <= 0:
        return odds_a, odds_b, 0.0
    true_prob_a = implied_a / total_implied
    true_prob_b = implied_b / total_implied
    fair_a = 1 / true_prob_a if true_prob_a else odds_a
    fair_b = 1 / true_prob_b if true_prob_b else odds_b
    vig_percent = max(0.0, (total_implied - 1.0) * 100)
    return fair_a, fair_b, vig_percent


def _calculate_edge_percent(soft_odds: float, fair_odds: float) -> float:
    if fair_odds <= 0:
        return 0.0
    return (soft_odds / fair_odds - 1.0) * 100


def _calculate_ev(true_probability: float, odds: float, stake: float) -> float:
    true_probability = max(0.0, min(true_probability, 1.0))
    win_amount = stake * (odds - 1.0)
    lose_amount = stake
    value = (true_probability * win_amount) - ((1.0 - true_probability) * lose_amount)
    return round(value, 2)


def _kelly_stake(
    true_probability: float, odds: float, bankroll: float, fraction: float
) -> Tuple[float, float, float]:
    if bankroll <= 0 or odds <= 1:
        return 0.0, 0.0, 0.0
    p = max(0.0, min(true_probability, 1.0))
    q = 1.0 - p
    b = odds - 1.0
    if b <= 0:
        return 0.0, 0.0, 0.0
    kelly_fraction = (b * p - q) / b
    if kelly_fraction <= 0:
        return 0.0, 0.0, 0.0
    fraction = max(0.0, min(fraction, 1.0))
    recommended_fraction = kelly_fraction * fraction
    stake = round(bankroll * recommended_fraction, 2)
    return round(kelly_fraction * 100, 2), round(recommended_fraction * 100, 2), stake
