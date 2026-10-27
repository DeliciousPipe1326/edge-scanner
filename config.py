"""Configuration constants for the arbitrage scanner."""

from __future__ import annotations

REGION_CONFIG = {
    "us": {"name": "United States", "default": True},
    "us2": {"name": "United States (Additional)", "default": False},
    "uk": {"name": "United Kingdom", "default": False},
    "eu": {"name": "Europe", "default": True},
    "au": {"name": "Australia", "default": False},
}

DEFAULT_REGION_KEYS = [key for key, meta in REGION_CONFIG.items() if meta.get("default")]

REGION_OPTIONS = [
    {"key": key, "label": meta["name"], "default": meta.get("default", False)}
    for key, meta in REGION_CONFIG.items()
]

EXCHANGE_BOOKMAKERS = {
    "betfair_ex_eu": {"name": "Betfair"},
    "betfair_ex_uk": {"name": "Betfair"},
    "betfair_ex_au": {"name": "Betfair"},
    "sportsbet_ex": {"name": "Sportsbet Exchange"},
    "matchbook": {"name": "Matchbook"},
}

EXCHANGE_KEYS = set(EXCHANGE_BOOKMAKERS.keys())

DEFAULT_COMMISSION = 0.05  # 5%
MAX_REFERENCE_MARGIN_PERCENT = 5.0

# -----------------------------------------------------------------------------
# Middles configuration
# -----------------------------------------------------------------------------

MIN_MIDDLE_GAP = 1.5
DEFAULT_MIDDLE_SORT = "break_even"
SHOW_POSITIVE_EV_ONLY = False

SPORT_DISPLAY_NAMES = {
    "americanfootball_nfl": "NFL",
    "basketball_nba": "NBA",
    "baseball_mlb": "MLB",
    "icehockey_nhl": "NHL",
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_usa_mls": "MLS",
}

AMERICAN_SPORTS = {
    "americanfootball_nfl",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
}

SOCCER_SPORTS = {
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_usa_mls",
}

DEFAULT_SPORT_KEYS = [
    "americanfootball_nfl",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_usa_mls",
]

DEFAULT_SPORT_OPTIONS = [
    {"key": key, "label": SPORT_DISPLAY_NAMES.get(key, key)} for key in DEFAULT_SPORT_KEYS
]

AMERICAN_MARKETS = ["h2h", "spreads", "totals"]
SOCCER_MARKETS = ["spreads", "totals"]

ROI_BANDS = [
    (0.0, 1.0, "0-1%"),
    (1.0, 2.0, "1-2%"),
    (2.0, float("inf"), "2%+"),
]

# -----------------------------------------------------------------------------
# +EV configuration
# -----------------------------------------------------------------------------

SHARP_BOOKS = [
    {"key": "pinnacle", "name": "Pinnacle", "region": "eu", "type": "bookmaker"},
    {"key": "betfair_ex_eu", "name": "Betfair Exchange", "region": "eu", "type": "exchange"},
    {"key": "matchbook", "name": "Matchbook", "region": "eu", "type": "exchange"},
]

DEFAULT_SHARP_BOOK = "pinnacle"
DEFAULT_BANKROLL = 1000.0
MIN_EDGE_PERCENT = 1.0
DEFAULT_KELLY_FRACTION = 0.25
KELLY_OPTIONS = [
    {"label": "Full Kelly", "value": 1.0},
    {"label": "Half Kelly", "value": 0.5},
    {"label": "Quarter Kelly", "value": 0.25},
    {"label": "Tenth Kelly", "value": 0.1},
]

EDGE_BANDS = [
    (1.0, 3.0, "1-3%"),
    (3.0, 5.0, "3-5%"),
    (5.0, 10.0, "5-10%"),
    (10.0, float("inf"), "10%+"),
]


def markets_for_sport(sport_key: str) -> list[str]:
    if sport_key in AMERICAN_SPORTS or sport_key.startswith(
        ("americanfootball_", "basketball_", "baseball_", "icehockey_")
    ):
        return AMERICAN_MARKETS
    if sport_key.startswith("soccer_"):
        return SOCCER_MARKETS
    return ["h2h"]
