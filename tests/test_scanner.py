import unittest
import datetime as dt
from unittest.mock import Mock, patch

import requests

import scanner


def book(key, outcomes, market="h2h"):
    return {
        "last_update": dt.datetime.now(dt.timezone.utc).isoformat(),
        "key": key,
        "title": key.replace("_", " ").title(),
        "markets": [{"key": market, "outcomes": outcomes}],
    }


def outcome(name, price, point=None):
    result = {"name": name, "price": price}
    if point is not None:
        result["point"] = point
    return result


def game(*books):
    return {
        "id": "fixture-event",
        "commence_time": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
        "home_team": "Home",
        "away_team": "Away",
        "sport_key": "basketball_nba",
        "bookmakers": list(books),
    }


class ScannerTests(unittest.TestCase):
    def test_exchange_commission_survives_arbitrage_collection(self):
        event = game(
            book("betfair_ex_eu", [outcome("Home", 2.1), outcome("Away", 1.8)]),
            book("draftkings", [outcome("Home", 1.8), outcome("Away", 2.1)]),
        )
        entries = scanner._collect_market_entries(event, "h2h", 100, 0.05)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertTrue(entry["has_exchange"])
        exchange = next(o for o in entry["best_odds"] if o["is_exchange"])
        self.assertEqual(exchange["bookmaker_key"], "betfair_ex_eu")
        self.assertAlmostEqual(exchange["effective_price"], 2.045)
        self.assertLess(entry["roi_percent"], entry["gross_roi_percent"])

    def test_opposite_handicaps_do_not_overwrite_valid_pairs(self):
        event = game(
            book("a", [outcome("Home", 2.1, -3.5), outcome("Away", 2.1, 3.5)], "spreads"),
            book("b", [outcome("Home", 2.2, 3.5), outcome("Away", 1.7, -3.5)], "spreads"),
        )
        entries = scanner._collect_market_entries(event, "spreads", 100, 0)
        self.assertEqual(len(entries), 1)
        self.assertEqual([o["point"] for o in entries[0]["best_odds"]], [-3.5, 3.5])

    def test_push_and_split_lines_are_excluded_from_two_way_model(self):
        for point in (0, 3, 3.25, 3.75):
            with self.subTest(point=point):
                event = game(
                    book(
                        "a", [outcome("Home", 2.1, -point), outcome("Away", 2.1, point)], "spreads"
                    )
                )
                self.assertEqual(scanner._collect_market_entries(event, "spreads", 100, 0), [])
                self.assertEqual(scanner._two_way_outcomes(event["bookmakers"][0]), {})

    def test_three_way_moneyline_is_excluded(self):
        event = game(book("a", [outcome("Home", 3), outcome("Away", 3), outcome("Draw", 3)]))
        self.assertEqual(scanner._collect_market_entries(event, "h2h", 100, 0), [])

    def test_malformed_prices_are_skipped(self):
        for price in (None, "invalid", float("nan"), float("inf"), 1, -2):
            with self.subTest(price=price):
                event = game(book("a", [outcome("Home", price), outcome("Away", 2.1)]))
                self.assertEqual(scanner._collect_market_entries(event, "h2h", 100, 0), [])
                self.assertEqual(
                    scanner._build_sharp_reference(event["bookmakers"][0], 0, False), {}
                )

    def test_middle_stakes_equalize_miss_outcomes(self):
        a, b = scanner._calculate_middle_stakes(3, 2, 100)
        self.assertEqual((a, b), (40, 60))
        profits = scanner._calculate_middle_outcomes(a, b, 3, 2)
        self.assertEqual(profits["side_a_wins_profit"], profits["side_b_wins_profit"])

    def test_middle_uses_payoffs_and_break_even_instead_of_invented_probability(self):
        event = game(
            book("a", [outcome("Home", 1.9, -2.5), outcome("Away", 1.9, 2.5)], "spreads"),
            book("b", [outcome("Home", 1.9, -3.5), outcome("Away", 1.9, 3.5)], "spreads"),
        )
        entries = scanner._collect_middle_opportunities(event, "spreads", 100, 0)
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["middle_probability"])
        self.assertIsNone(entries[0]["ev_percent"])
        self.assertAlmostEqual(entries[0]["break_even_percent"], 5.26, places=2)
        self.assertEqual(entries[0]["profile"]["worst_case_profit"], -5)

    def test_rounded_stakes_sum_to_total(self):
        offers = [
            {"name": "Home", "bookmaker": "a", "price": 2.1},
            {"name": "Away", "bookmaker": "b", "price": 2.1},
        ]
        for total in (0.01, 0.03, 99.99, 100):
            result = scanner._calculate_stakes(offers, total, "price")
            self.assertAlmostEqual(sum(o["stake"] for o in result["breakdown"]), total)
            self.assertEqual(
                result["guaranteed_profit"],
                round(min(o["payout"] for o in result["breakdown"]) - total, 2),
            )

    def test_kelly_always_returns_three_values(self):
        for args in ((0.5, 2, 0, 0.25), (0.5, 1, 100, 0.25), (0.1, 2, 100, 0.25)):
            self.assertEqual(scanner._kelly_stake(*args), (0, 0, 0))
        self.assertEqual(scanner._kelly_stake(0.6, 2, 1000, 0.25), (20, 5, 50))

    @patch("scanner.requests.get")
    def test_network_error_does_not_disclose_api_key(self, get):
        get.side_effect = requests.ConnectionError("https://example.com/?apiKey=secret")
        with self.assertRaises(scanner.ScannerError) as error:
            scanner._request("https://example.com", {"apiKey": "secret"})
        self.assertNotIn("secret", str(error.exception))

    @patch("scanner.requests.get")
    def test_api_error_redacts_key_and_handles_non_object_json(self, get):
        get.return_value = Mock(status_code=401)
        for payload in ({"message": "Bad key: secret"}, ["error"]):
            get.return_value.json.return_value = payload
            with self.assertRaises(scanner.ScannerError) as error:
                scanner._request("https://example.com", {"apiKey": "secret"})
            self.assertNotIn("secret", str(error.exception))

    @patch("scanner._request")
    def test_invalid_api_payload_is_reported(self, request):
        request.return_value.json.return_value = {"unexpected": True}
        with self.assertRaises(scanner.ScannerError):
            scanner.fetch_sports("key")

    @patch("scanner.fetch_sports", return_value=[])
    def test_no_sports_has_consistent_response_schema(self, fetch):
        result = scanner.run_scan("key")
        self.assertTrue(result["success"])
        for tab in ("arbitrage", "middles", "plus_ev"):
            self.assertEqual(result[tab]["opportunities"], [])

    @patch("scanner.fetch_sports", return_value=[{"key": "basketball_nba", "active": True}])
    @patch("scanner.fetch_odds_for_sport", side_effect=scanner.ScannerError("unavailable"))
    def test_all_sports_failing_is_not_a_success(self, odds, sports):
        result = scanner.run_scan("key", sports=["basketball_nba"])
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], 502)

    @patch("scanner.fetch_sports", return_value=[{"key": "basketball_nba", "active": True}])
    @patch("scanner.fetch_odds_for_sport")
    def test_full_scan_builds_all_tabs_without_network(self, odds, sports):
        odds.return_value = [
            game(
                book("pinnacle", [outcome("Home", 2), outcome("Away", 2)]),
                book("draftkings", [outcome("Home", 2.2), outcome("Away", 1.9)]),
            )
        ]
        result = scanner.run_scan("key", sports=["basketball_nba"])
        self.assertTrue(result["success"])
        self.assertEqual(result["arbitrage"]["opportunities_count"], 1)
        self.assertEqual(result["plus_ev"]["opportunities_count"], 1)
        self.assertEqual(result["plus_ev"]["opportunities"][0]["edge_percent"], 10)


if __name__ == "__main__":
    unittest.main()
