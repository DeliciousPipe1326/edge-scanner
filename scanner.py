"""Core arbitrage scanning logic."""

from __future__ import annotations

import datetime as dt
import itertools
import math
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from quality import (
    DEFAULT_MAX_AGE,
    UTC,
    execution_check,
    quote_metadata,
    usable_game,
    current_opportunities,
)
from quota import Quota
from middles import middle_profile

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
    MIN_EDGE_PERCENT,
    MIN_MIDDLE_GAP,
    MAX_REFERENCE_MARGIN_PERCENT,
    REGION_CONFIG,
    ROI_BANDS,
    SHARP_BOOKS,
    SHOW_POSITIVE_EV_ONLY,
    SPORT_DISPLAY_NAMES,
    markets_for_sport,
)

BASE_URL = "https://api.the-odds-api.com/v4"
MIDDLE_MARKETS = {"spreads", "totals"}
ALLOWED_PLUS_EV_MARKETS = {"h2h", "spreads", "totals"}
SHARP_BOOK_MAP = {book["key"]: book for book in SHARP_BOOKS}


class ScannerError(Exception):
    """Raised for recoverable scanner issues."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code
        self.fatal = status_code in {401, 403, 429}


def _iso_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


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
    fav_abs = -favorite_line
    if fav_abs >= underdog_line:
        return None
    gap = round(underdog_line - fav_abs, 2)
    if gap > 1000:
        return None
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
    if gap > 1000:
        return None
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
    bookmaker: dict,
    commission_rate: float,
    is_exchange: bool,
    diagnostics: Optional[dict] = None,
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
                    **quote_metadata(bookmaker, market, outcome),
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
        margin = (1 / first["price"] + 1 / second["price"] - 1) * 100
        limits = [entry.get("bet_limit") for entry in entries]
        if abs(margin) > MAX_REFERENCE_MARGIN_PERCENT or (
            is_exchange
            and any(
                not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit <= 0
                for limit in limits
            )
        ):
            if diagnostics is not None:
                diagnostics["rejected_reference_lines"] = (
                    diagnostics.get("rejected_reference_lines", 0) + 1
                )
            continue
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
                    "last_update": first.get("last_update"),
                },
                second["name"]: {
                    "fair_odds": fair_b,
                    "true_probability": prob_b,
                    "sharp_odds": second["raw_price"],
                    "opponent_odds": first["raw_price"],
                    "opponent_name": first["display_name"],
                    "display_name": second["display_name"],
                    "point": second.get("point"),
                    "last_update": second.get("last_update"),
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
                    **quote_metadata(bookmaker, market, outcome),
                    "name": (outcome.get("name") or "").strip().lower(),
                    "display_name": outcome.get("name") or "",
                    "price": display_price,
                    "point": outcome.get("point"),
                }
            )
    return {key: entries for key, entries in line_map.items() if len(entries) == 2}


def _calculate_middle_stakes(
    odds_a: float, odds_b: float, total_stake: float
) -> Tuple[float, float]:
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


def _format_middle_zone(description_source: str, middle_integers: List[int], is_total: bool) -> str:
    if not middle_integers:
        return description_source
    middle_integers = sorted(middle_integers)
    if len(middle_integers) == 1:
        range_text = str(middle_integers[0])
    else:
        range_text = f"{middle_integers[0]} to {middle_integers[-1]}"
    if is_total:
        return f"Total {range_text}"
    return f"{description_source}: {range_text}"


def _request(url: str, params: Dict[str, str], quota: Optional[Quota] = None) -> requests.Response:
    try:
        resp = requests.get(url, params=params, timeout=(5, 30), allow_redirects=False)
    except requests.RequestException as exc:
        if quota is not None:
            quota.complete = False
        # Request exceptions can contain the full URL, including the API key.
        raise ScannerError("Could not reach The Odds API. Please try again.") from exc
    if quota is not None:
        quota.record(resp.headers, paid="/odds/" in url)
    if 300 <= resp.status_code < 400:
        raise ScannerError("Unexpected redirect from The Odds API")
    if resp.status_code >= 400:
        try:
            payload = resp.json()
            message = (
                (payload.get("message") or payload.get("error"))
                if isinstance(payload, dict)
                else None
            )
        except ValueError:
            message = None
        message = str(message or f"API request failed ({resp.status_code})")
        if params.get("apiKey"):
            message = message.replace(params["apiKey"], "[redacted]")
        raise ScannerError(message, status_code=resp.status_code)
    return resp


def fetch_sports(api_key: str, quota: Optional[Quota] = None) -> List[dict]:
    url = f"{BASE_URL}/sports/"
    resp = _request(url, {"apiKey": api_key}, quota)
    try:
        return _parse_list_response(resp, "sports list")
    except ValueError as exc:  # pragma: no cover - malformed payload
        raise ScannerError("Failed to parse sports list") from exc


def filter_sports(sports: Sequence[dict], requested: Sequence[str], all_sports: bool) -> List[dict]:
    requested_set = set(requested) if requested else set(DEFAULT_SPORT_KEYS)
    selected = {}
    for sport in sports:
        key = sport.get("key")
        if (
            isinstance(key, str)
            and key
            and sport.get("active")
            and not sport.get("has_outrights")
            and (all_sports or key in requested_set)
        ):
            selected[key] = sport
    return list(selected.values())


def fetch_odds_for_sport(
    api_key: str,
    sport_key: str,
    markets: Sequence[str],
    regions: Sequence[str],
    quota: Optional[Quota] = None,
    window_hours: Optional[int] = 24,
) -> List[dict]:
    url = f"{BASE_URL}/sports/{sport_key}/odds/"
    params = {
        "apiKey": api_key,
        "regions": ",".join(regions),
        "markets": ",".join(markets),
        "oddsFormat": "decimal",
        "includeBetLimits": "true",
        "commenceTimeFrom": _iso_now(),
    }
    if window_hours is not None:
        end = dt.datetime.now(UTC) + dt.timedelta(hours=window_hours)
        params["commenceTimeTo"] = end.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    resp = _request(url, params, quota)
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
    markets: List[dict],
    market_key: str,
    commission_rate: float,
    home_team: Optional[str] = None,
    stake_total: Optional[float] = None,
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
                display_price = float(price)
                is_exchange = bookmaker_key in EXCHANGE_KEYS
                effective_price = _apply_commission(display_price, commission_rate, is_exchange)
                entry.setdefault(name, []).append(
                    {
                        **quote_metadata(book, market, outcome),
                        "effective_price": effective_price,
                        "display_price": display_price,
                        "bookmaker": bookmaker,
                        "bookmaker_key": bookmaker_key,
                        "name": name,
                        "point": normalized_point,
                        "is_exchange": is_exchange,
                    }
                )
    selected = {}
    for key, offers in lines.items():
        if len(offers) != 2 or stake_total is None:
            selected[key] = {
                name: max(items, key=lambda o: o["effective_price"])
                for name, items in offers.items()
            }
            continue
        names = list(offers)
        best, best_roi = None, float("-inf")
        for pair in itertools.product(*(offers[name] for name in names)):
            stakes = _calculate_stakes(list(pair), stake_total, "effective_price")
            check = execution_check(pair, [o["stake"] for o in stakes["breakdown"]])
            if not check["exceeds_limit"] and stakes["roi_percent"] > best_roi:
                best, best_roi = pair, stakes["roi_percent"]
        if best:
            selected[key] = dict(zip(names, best))
    return selected


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
    best_lines = _record_best_prices(
        markets, market_key, commission_rate, game.get("home_team"), stake_total
    )
    entries = []
    for line_key, offers in best_lines.items():
        # Skip lines that aren't genuine two-way markets (e.g., moneyline with a draw option).
        if len(offers) != 2:
            continue
        outcomes = list(offers.values())
        names = {o["name"] for o in outcomes}
        expected = (
            {"Over", "Under"}
            if market_key == "totals"
            else {game.get("home_team"), game.get("away_team")}
        )
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
                "last_update": o.get("last_update"),
                "bet_limit": o.get("bet_limit"),
            }
            for o in outcomes
        ]
        net_stake_info = _calculate_stakes(outcomes, stake_total, price_field="effective_price")
        gross_stake_info = (
            _calculate_stakes(outcomes, stake_total, price_field="display_price")
            if has_exchange
            else None
        )
        net_roi = net_stake_info["roi_percent"]
        gross_roi = gross_stake_info["roi_percent"] if gross_stake_info else net_roi
        if net_roi <= 0:
            continue
        exchange_names = {
            o.get("bookmaker") or EXCHANGE_BOOKMAKERS.get(o.get("bookmaker_key"), {}).get("name")
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
            "execution": execution_check(
                outcomes, [o["stake"] for o in net_stake_info["breakdown"]]
            ),
            "gross_stakes": gross_stake_info,
            "has_exchange": has_exchange,
            "exchange_books": exchange_books,
        }
        entries.append(entry)
    return entries


def _collect_middle_opportunities(
    game: dict, market_key: str, stake_total: float, commission_rate: float
) -> List[dict]:
    if (
        market_key not in MIDDLE_MARKETS
        or stake_total <= 0
        or not (game.get("sport_key") or "").startswith(
            (
                "americanfootball_",
                "basketball_",
                "baseball_",
                "icehockey_",
                "soccer_",
                "rugbyleague_",
                "rugbyunion_",
                "aussierules_",
            )
        )
    ):
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
                        **quote_metadata(book, market, outcome),
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
    target_books: Optional[Sequence[str]] = None,
    diagnostics: Optional[dict] = None,
) -> List[dict]:
    bookmakers = game.get("bookmakers", [])
    sharp_reference = {}
    for candidate in sharp_priority:
        for sharp in bookmakers:
            if sharp.get("key") != candidate.get("key"):
                continue
            references = _build_sharp_reference(
                sharp,
                commission_rate,
                candidate.get("type") == "exchange",
                diagnostics,
            )
            for line, reference in references.items():
                # A spread reference also identifies which team holds each signed line.
                signature = (
                    *line,
                    tuple(
                        sorted(
                            (name, data.get("point"))
                            for name, data in reference["outcomes"].items()
                        )
                    ),
                )
                sharp_reference.setdefault(signature, (reference, candidate))
    if not sharp_reference:
        if diagnostics is not None:
            diagnostics["events_without_reference"] = (
                diagnostics.get("events_without_reference", 0) + 1
            )
        return []
    opportunities: List[dict] = []
    for book in bookmakers:
        key = book.get("key")
        if not key:
            continue
        if key in SHARP_BOOK_MAP or key in EXCHANGE_KEYS:
            continue
        if target_books and key not in target_books:
            continue
        bookmaker_title = book.get("title") or key
        is_exchange = key in EXCHANGE_KEYS
        soft_lines = _two_way_outcomes(book)
        if not soft_lines:
            continue
        for (market_key, line_key), entries in soft_lines.items():
            if market_key not in markets:
                continue
            signature = (
                market_key,
                line_key,
                tuple(sorted((entry["name"], entry.get("point")) for entry in entries)),
            )
            match = sharp_reference.get(signature)
            if not match:
                continue
            reference, sharp_meta = match
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
                        "last_update": entry.get("last_update"),
                        "bet_limit": entry.get("bet_limit"),
                    },
                    "sharp": {
                        "book": sharp_meta.get("name") or sharp_meta.get("key"),
                        "key": sharp_meta.get("key"),
                        "last_update": sharp_outcome.get("last_update"),
                        "odds": sharp_outcome["sharp_odds"],
                        "opponent_odds": sharp_outcome["opponent_odds"],
                        "opponent": sharp_outcome["opponent_name"],
                        "fair_odds": sharp_outcome["fair_odds"],
                        "true_probability": sharp_outcome["true_probability"],
                        "true_probability_percent": round(
                            sharp_outcome["true_probability"] * 100, 2
                        ),
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
                opportunity["execution"] = execution_check(
                    [{**entry, "bookmaker": bookmaker_title}], [recommended]
                )
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
        if {offer_a["team"], offer_b["team"]} != {home_team, away_team}:
            return None
        favorite_offer, underdog_offer = (
            (offer_a, offer_b) if offer_a["team"] == home_team else (offer_b, offer_a)
        )
        gap_info = _spread_gap_info(favorite_offer["line"], underdog_offer["line"])
        if not gap_info:
            return None
        side_a, side_b = favorite_offer, underdog_offer
        descriptor_name = side_a["team"] + " margin"
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

    stake_a, stake_b = _calculate_middle_stakes(
        side_a["effective_price"], side_b["effective_price"], stake_total
    )
    if stake_a <= 0 or stake_b <= 0:
        return None
    outcomes = _calculate_middle_outcomes(
        stake_a, stake_b, side_a["effective_price"], side_b["effective_price"]
    )
    profile = middle_profile(side_a, side_b, stake_a, stake_b, is_total)
    if profile is None:
        return None
    has_exchange = side_a["is_exchange"] or side_b["is_exchange"]
    middle_zone = _format_middle_zone(descriptor_name, middle_integers, is_total)
    best_odds = (
        {
            "team": side_a["team"],
            "line": side_a["line"],
            "price": side_a["display_price"],
            "effective_price": side_a["effective_price"],
            "bookmaker": side_a["bookmaker"],
            "is_exchange": side_a["is_exchange"],
            "last_update": side_a.get("last_update"),
            "bet_limit": side_a.get("bet_limit"),
        },
        {
            "team": side_b["team"],
            "line": side_b["line"],
            "price": side_b["display_price"],
            "effective_price": side_b["effective_price"],
            "bookmaker": side_b["bookmaker"],
            "is_exchange": side_b["is_exchange"],
            "last_update": side_b.get("last_update"),
            "bet_limit": side_b.get("bet_limit"),
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
        "sport_display": game.get("sport_display") or SPORT_DISPLAY_NAMES.get(sport_key, sport_key),
        "event": f"{game.get('away_team', away_team)} vs {game.get('home_team', home_team)}",
        "commence_time": game.get("commence_time"),
        "market": market_key,
        "side_a": best_odds[0],
        "side_b": best_odds[1],
        "gap": {
            "points": gap_info["gap_points"],
            "middle_integers": middle_integers,
            "integer_count": gap_info["integer_count"],
        },
        "middle_zone": middle_zone,
        "profile": profile,
        "break_even_percent": profile["break_even_percent"],
        "middle_probability": None,
        "probability_percent": None,
        "stakes": stakes_payload,
        "outcomes": outcomes,
        "ev_dollars": None,
        "ev_percent": None,
        "execution": execution_check([side_a, side_b], [stake_a, stake_b]),
        "has_exchange": has_exchange,
        "gross_ev_percent": None,
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
        stake_value = (
            round(stake_total - allocated, 2)
            if index == len(outcomes) - 1
            else round(stake_total * fraction, 2)
        )
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
    thresholds = [
        o["break_even_percent"] for o in opportunities if o.get("break_even_percent") is not None
    ]
    by_sport = {}
    for opp in opportunities:
        sport = opp.get("sport_display") or opp.get("sport") or "unknown"
        by_sport[sport] = by_sport.get(sport, 0) + 1
    return {
        "count": len(opportunities),
        "by_sport": by_sport,
        "average_break_even_percent": round(sum(thresholds) / len(thresholds), 2)
        if thresholds
        else None,
        "lowest_break_even_percent": min(thresholds, default=None),
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
            opp.get("side_a", {}).get("bookmaker"),
            opp.get("side_b", {}).get("bookmaker"),
            opp.get("side_a", {}).get("line"),
            opp.get("side_b", {}).get("line"),
            tuple(opp.get("gap", {}).get("middle_integers", [])),
        )
        if (
            key not in best_by_key
            or opp["profile"]["worst_case_profit"]
            > best_by_key[key]["profile"]["worst_case_profit"]
        ):
            best_by_key[key] = opp
    return list(best_by_key.values())


def _plus_ev_summary(opportunities: List[dict]) -> dict:
    count = len(opportunities)
    avg_edge = (
        round(sum(opp.get("edge_percent", 0) for opp in opportunities) / count, 2) if count else 0.0
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
            bet.get("soft_key"),
            (bet.get("outcome") or "").strip().lower(),
            normalized_point,
        )
        existing = best_by_key.get(key)
        if not existing or (opp.get("edge_percent", 0) > existing.get("edge_percent", 0)):
            best_by_key[key] = opp
    return list(best_by_key.values())


def estimate_plan(sports, regions):
    items = [
        {
            "key": s["key"],
            "name": s.get("title") or s["key"],
            "markets": markets_for_sport(s["key"]),
            "max_credits": len(markets_for_sport(s["key"])) * len(regions),
        }
        for s in sports
    ]
    return {
        "sports": items,
        "regions": regions,
        "odds_requests": len(items),
        "max_credits": sum(s["max_credits"] for s in items),
    }


def plan_scan(
    api_key,
    sports=None,
    all_sports=False,
    regions=None,
    sharp_book=DEFAULT_SHARP_BOOK,
    window_hours=24,
):
    if not api_key:
        return {"success": False, "error": "API key is required", "error_code": 400}
    quota = Quota()
    try:
        available = fetch_sports(api_key, quota)
    except ScannerError as exc:
        return {"success": False, "error": str(exc), "error_code": exc.status_code}
    selected = filter_sports(available, sports or DEFAULT_SPORT_KEYS, all_sports)
    normalized = _ensure_sharp_region(_normalize_regions(regions), sharp_book)
    return {
        "success": True,
        "plan": {**estimate_plan(selected, normalized), "window_hours": window_hours},
        "quota": quota.as_dict(),
    }


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
    max_quote_age: float = DEFAULT_MAX_AGE,
    target_books: Optional[Sequence[str]] = None,
    max_credits: Optional[int] = None,
    window_hours: Optional[int] = 24,
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
    quota = Quota()
    try:
        sports_list = fetch_sports(api_key, quota)
    except ScannerError as exc:
        return {
            "success": False,
            "error": str(exc),
            "error_code": exc.status_code,
            "quota": quota.as_dict(),
        }

    filtered = filter_sports(sports_list, sports or DEFAULT_SPORT_KEYS, all_sports)
    estimate = {**estimate_plan(filtered, normalized_regions), "window_hours": window_hours}
    if max_credits is not None and estimate["max_credits"] > max_credits:
        return {
            "success": False,
            "error": "Scan cost exceeds the approved credit budget. Review the scan again.",
            "error_code": 409,
            "plan": estimate,
        }
    if quota.remaining is not None and estimate["max_credits"] > quota.remaining:
        return {
            "success": False,
            "error": "Insufficient API credits for this scan. Select fewer sports or regions.",
            "error_code": 429,
            "quota": quota.as_dict(),
        }
    quality = {
        key: 0
        for key in (
            "invalid_events",
            "started_or_invalid_events",
            "invalid_markets",
            "missing_or_invalid_timestamps",
            "stale_markets",
            "fresh_markets",
            "outside_window_events",
        )
    }
    coverage = {}

    arb_opportunities: List[dict] = []
    middle_opportunities: List[dict] = []
    plus_ev_opportunities: List[dict] = []
    events_scanned = 0
    total_profit = 0.0
    sport_errors: List[dict] = []
    successful_sports = 0
    api_calls_used = 0
    terminal_status = 502
    sharp_priority = _sharp_priority(sharp_book or DEFAULT_SHARP_BOOK)

    for sport in filtered:
        sport_key = sport.get("key")
        if not sport_key:
            continue
        markets = markets_for_sport(sport_key)
        if quota.remaining is not None and len(markets) * len(normalized_regions) > quota.remaining:
            sport_errors.append(
                {"sport_key": sport_key, "error": "Insufficient remaining API credits"}
            )
            break
        api_calls_used += 1
        try:
            events = fetch_odds_for_sport(
                api_key, sport_key, markets, normalized_regions, quota, window_hours
            )
        except ScannerError as exc:
            sport_errors.append(
                {
                    "sport_key": sport_key,
                    "sport": sport.get("title") or SPORT_DISPLAY_NAMES.get(sport_key, sport_key),
                    "error": str(exc),
                }
            )
            if exc.fatal:
                terminal_status = exc.status_code
                break
            continue
        successful_sports += 1
        events_scanned += len(events)
        for raw_game in events:
            game = usable_game(raw_game, dt.datetime.now(UTC), max_quote_age, quality, window_hours)
            if game is None:
                continue
            for book in game["bookmakers"]:
                coverage[book["key"]] = book.get("title") or book["key"]
            game["sport_key"] = sport_key
            game["sport_title"] = sport.get("title")
            game["sport_display"] = SPORT_DISPLAY_NAMES.get(sport_key, sport_key)
            trading_game = {
                **game,
                "bookmakers": [
                    b for b in game["bookmakers"] if not target_books or b["key"] in target_books
                ],
            }
            for market_key in markets:
                new_entries = _collect_market_entries(
                    trading_game, market_key, stake_amount, commission_rate
                )
                for entry in new_entries:
                    total_profit += entry["stakes"].get("guaranteed_profit", 0.0)
                arb_opportunities.extend(new_entries)
                if market_key in MIDDLE_MARKETS:
                    middle_entries = _collect_middle_opportunities(
                        trading_game, market_key, stake_amount, commission_rate
                    )
                    middle_opportunities.extend(middle_entries)
            plus_entries = _collect_plus_ev_opportunities(
                game,
                markets,
                sharp_priority,
                commission_rate,
                0.0,
                bankroll,
                kelly_fraction,
                target_books,
                quality,
            )
            plus_ev_opportunities.extend(plus_entries)

    final_time = dt.datetime.now(UTC)
    arb_opportunities = current_opportunities(arb_opportunities, final_time, max_quote_age)
    middle_opportunities = current_opportunities(middle_opportunities, final_time, max_quote_age)
    plus_ev_opportunities = current_opportunities(plus_ev_opportunities, final_time, max_quote_age)
    total_profit = sum(o["stakes"]["guaranteed_profit"] for o in arb_opportunities)
    arb_opportunities.sort(key=lambda x: x["roi_percent"], reverse=True)
    middle_opportunities.sort(
        key=lambda x: x["break_even_percent"] if x["break_even_percent"] is not None else 100
    )
    middle_opportunities = _deduplicate_middles(middle_opportunities)
    plus_ev_opportunities = _deduplicate_plus_ev(plus_ev_opportunities)
    plus_ev_opportunities.sort(key=lambda x: x.get("edge_percent", 0), reverse=True)
    arb_summary = _summaries(
        arb_opportunities, successful_sports, events_scanned, total_profit, api_calls_used
    )
    middle_summary = _middle_summary(middle_opportunities)
    plus_ev_summary = _plus_ev_summary(plus_ev_opportunities)
    if filtered and not successful_sports:
        return {
            "success": False,
            "error": "All selected sports failed to load.",
            "error_code": terminal_status,
            "sport_errors": sport_errors,
            "quota": quota.as_dict(),
        }
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
        "quota": quota.as_dict(),
        "plan": estimate,
        "quality": quality,
        "max_quote_age": max_quote_age,
        "bookmakers": [{"key": key, "name": name} for key, name in sorted(coverage.items())],
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
