"""Provider-boundary and product acceptance tests; never use a live API key."""

import copy
import datetime as dt
import unittest
from unittest.mock import Mock, patch

import app as web
import scanner
from middles import middle_profile
from quality import DEFAULT_MAX_AGE, execution_check, usable_game
from quota import Quota
from test_scanner import book, game, outcome


def response(payload, cost=0, remaining=500, status=200, headers=True):
    result = Mock(status_code=status)
    result.json.return_value = payload
    result.headers = (
        {
            "x-requests-last": str(cost),
            "x-requests-used": str(500 - remaining),
            "x-requests-remaining": str(remaining),
        }
        if headers
        else {}
    )
    return result


SPORTS = [{"key": "basketball_nba", "title": "NBA", "active": True}]


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now(dt.timezone.utc)
        self.event = game(book("a", [outcome("Home", 2), outcome("Away", 2)]))
        self.stats = {
            key: 0
            for key in (
                "invalid_events",
                "started_or_invalid_events",
                "invalid_markets",
                "missing_or_invalid_timestamps",
                "stale_markets",
                "fresh_markets",
            )
        }

    def clean(self, event=None):
        return usable_game(event or self.event, self.now, DEFAULT_MAX_AGE, self.stats)

    def test_stale_missing_and_future_quote_times_are_excluded(self):
        for update in (
            None,
            "bad",
            (self.now - dt.timedelta(seconds=121)).isoformat(),
            (self.now + dt.timedelta(seconds=10)).isoformat(),
            "2026-10-27T12:00:00",
        ):
            with self.subTest(update=update):
                event = copy.deepcopy(self.event)
                event["bookmakers"][0]["last_update"] = update
                self.assertEqual(self.clean(event)["bookmakers"], [])

    def test_market_timestamp_takes_precedence_over_book_timestamp(self):
        event = copy.deepcopy(self.event)
        event["bookmakers"][0]["markets"][0]["last_update"] = (
            self.now - dt.timedelta(seconds=121)
        ).isoformat()
        self.assertEqual(self.clean(event)["bookmakers"], [])

    def test_started_or_undated_events_are_excluded(self):
        for start in (None, "bad", (self.now - dt.timedelta(seconds=1)).isoformat()):
            event = {**self.event, "commence_time": start}
            self.assertIsNone(self.clean(event))

    def test_invalid_outcome_does_not_turn_three_way_market_into_two_way(self):
        self.event["bookmakers"][0]["markets"][0]["outcomes"].append(outcome("Draw", "invalid"))
        self.assertEqual(self.clean()["bookmakers"], [])

    def test_untrusted_nested_shapes_are_rejected_without_crashing(self):
        for bad in (
            None,
            [],
            {"key": {}},
            {"key": "h2h", "outcomes": None},
            {"key": "h2h", "outcomes": [{"name": []}, {}]},
        ):
            event = copy.deepcopy(self.event)
            event["bookmakers"][0]["markets"] = [bad]
            self.assertEqual(self.clean(event)["bookmakers"], [])

    def test_duplicate_outcomes_and_non_complementary_lines_are_rejected(self):
        for market, offers in (
            ("h2h", [outcome("Home", 2), outcome("Home", 2)]),
            ("spreads", [outcome("Home", 2, -3.5), outcome("Away", 2, 2.5)]),
            ("totals", [outcome("Over", 2, 200.5), outcome("Under", 2, 201.5)]),
            ("spreads", [outcome("Home", 2, float("inf")), outcome("Away", 2, 3.5)]),
        ):
            with self.subTest(market=market):
                event = game(book("a", offers, market))
                self.assertEqual(self.clean(event)["bookmakers"], [])

    def test_provider_payload_is_not_mutated(self):
        before = copy.deepcopy(self.event)
        self.assertTrue(self.clean()["bookmakers"])
        self.assertEqual(self.event, before)

    def test_event_window_supports_24_48_7_days_and_infinite(self):
        for hours, visible in ((24, False), (48, False), (168, False), (None, True)):
            event = copy.deepcopy(self.event)
            event["commence_time"] = (self.now + dt.timedelta(days=60)).isoformat()
            self.assertEqual(
                usable_game(event, self.now, 120, self.stats, hours) is not None, visible
            )
        event = copy.deepcopy(self.event)
        event["commence_time"] = (self.now + dt.timedelta(hours=30)).isoformat()
        self.assertIsNone(usable_game(event, self.now, 120, self.stats, 24))
        self.assertIsNotNone(usable_game(event, self.now, 120, self.stats, 48))

    def test_limit_unknown_zero_and_exceeded_remain_distinct(self):
        for limit, expected in ((None, None), (0, False), (49, False), (50, True)):
            check = execution_check([{"bet_limit": limit}], [50])
            self.assertIs(check["legs"][0]["within_limit"], expected)
            self.assertEqual(check["exceeds_limit"], expected is False)

    def test_unreliable_sharp_margins_and_unverified_exchange_liquidity_are_rejected(self):
        # Values observed in live acceptance testing; no API request is made here.
        for prices in ((1.84, 1.64), (1.02, 2.22)):
            reference = book(
                "betfair_ex_eu", [outcome("Home", prices[0]), outcome("Away", prices[1])]
            )
            for item in reference["markets"][0]["outcomes"]:
                item["bet_limit"] = 100
            self.assertEqual(scanner._build_sharp_reference(reference, 0.05, True), {})
        reference = book("betfair_ex_eu", [outcome("Home", 2), outcome("Away", 2)])
        self.assertEqual(scanner._build_sharp_reference(reference, 0.05, True), {})

    def test_arbitrage_uses_a_feasible_lower_price_when_best_quote_has_insufficient_limit(self):
        best = book("limited", [outcome("Home", 2.5), outcome("Away", 1.5)])
        best["markets"][0]["outcomes"][0]["bet_limit"] = 1
        event = game(
            best,
            book("a", [outcome("Home", 2.1), outcome("Away", 1.5)]),
            book("b", [outcome("Home", 1.5), outcome("Away", 2.1)]),
        )
        entries = scanner._collect_market_entries(event, "h2h", 100, 0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["best_odds"][0]["bookmaker_key"], "a")
        self.assertFalse(entries[0]["execution"]["exceeds_limit"])

    def test_no_feasible_arbitrage_is_returned_when_every_price_exceeds_its_limit(self):
        limited = book("limited", [outcome("Home", 2.1), outcome("Away", 2.1)])
        for item in limited["markets"][0]["outcomes"]:
            item["bet_limit"] = 1
        self.assertEqual(scanner._collect_market_entries(game(limited), "h2h", 100, 0), [])


class MiddleTests(unittest.TestCase):
    def test_whole_line_endpoint_pushes_have_exact_payoffs(self):
        profile = middle_profile(
            {"line": -2, "effective_price": 1.9}, {"line": 4, "effective_price": 1.9}, 50, 50, False
        )
        payoffs = {item["score"]: item["profit"] for item in profile["scenarios"]}
        self.assertEqual(payoffs, {1: -5, 2: 45, 3: 90, 4: 45, 5: -5})
        self.assertAlmostEqual(profile["break_even_probability"], 5 / 95)
        self.assertIsNone(profile["probability"])
        self.assertIsNone(profile["ev"])

    def test_middle_can_cross_zero_margin_with_both_positive_handicaps(self):
        event = game(
            book("a", [outcome("Home", 1.9, 1.5), outcome("Away", 1.9, -1.5)], "spreads"),
            book("b", [outcome("Home", 1.9, -1.5), outcome("Away", 1.9, 1.5)], "spreads"),
        )
        results = scanner._collect_middle_opportunities(event, "spreads", 100, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["gap"]["middle_integers"], [-1, 0, 1])

    def test_quarter_lines_cannot_use_binary_settlement(self):
        self.assertIsNone(
            middle_profile(
                {"line": -2.25, "effective_price": 1.9},
                {"line": 4, "effective_price": 1.9},
                50,
                50,
                False,
            )
        )

    def test_total_scenarios_never_include_negative_scores(self):
        profile = middle_profile(
            {"line": 0, "effective_price": 1.9}, {"line": 2, "effective_price": 1.9}, 50, 50, True
        )
        self.assertEqual(min(s["score"] for s in profile["scenarios"]), 0)


class ProviderAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.event = game(
            book("pinnacle", [outcome("Home", 2), outcome("Away", 2)]),
            book("new_au_book", [outcome("Home", 2.2), outcome("Away", 1.9)]),
        )

    @patch("scanner.requests.get")
    def test_plan_is_quota_free_includes_sharp_region_and_deduplicates_sports(self, get):
        get.return_value = response(SPORTS * 2)
        plan = scanner.plan_scan("fake-key", sports=["basketball_nba"], regions=["au", "au"])
        self.assertTrue(plan["success"])
        self.assertEqual(plan["plan"]["regions"], ["au", "eu"])
        self.assertEqual(plan["plan"]["max_credits"], 6)
        self.assertEqual(plan["quota"]["credits_used"], 0)
        self.assertEqual(get.call_count, 1)
        self.assertNotIn("/odds/", get.call_args.args[0])

    @patch("scanner.requests.get")
    def test_budget_rejection_makes_no_paid_requests(self, get):
        get.return_value = response(SPORTS)
        result = scanner.run_scan("fake-key", max_credits=5)
        self.assertEqual(result["error_code"], 409)
        self.assertEqual(get.call_count, 1)

    @patch("scanner.requests.get")
    def test_known_insufficient_quota_makes_no_paid_requests(self, get):
        get.return_value = response(SPORTS, remaining=5)
        result = scanner.run_scan("fake-key", max_credits=6)
        self.assertEqual(result["error_code"], 429)
        self.assertEqual(get.call_count, 1)

    @patch("scanner.requests.get")
    def test_flask_to_provider_scan_with_current_regional_book(self, get):
        get.side_effect = [response(SPORTS), response([self.event], cost=6, remaining=494)]
        with patch.object(web, "ENV_API_KEY", None):
            result = web.app.test_client().post(
                "/scan", json={"apiKey": "fake-key", "maxCredits": 6}
            )
        self.assertEqual(result.status_code, 200)
        payload = result.json
        self.assertEqual(payload["quota"]["credits_used"], 6)
        self.assertEqual(payload["quota"]["remaining"], 494)
        self.assertEqual(payload["arbitrage"]["opportunities_count"], 1)
        self.assertEqual(payload["plus_ev"]["opportunities_count"], 1)
        edge = payload["plus_ev"]["opportunities"][0]
        self.assertEqual(edge["bet"]["soft_key"], "new_au_book")
        self.assertIsNotNone(edge["valid_until"])
        self.assertFalse(edge["execution"]["limits_verified"])
        self.assertEqual(get.call_args.kwargs["params"]["includeBetLimits"], "true")

    @patch("scanner.requests.get")
    def test_stale_sharp_cannot_generate_ev(self, get):
        self.event["bookmakers"][0]["last_update"] = "2020-01-01T00:00:00Z"
        get.side_effect = [response(SPORTS), response([self.event], cost=6, remaining=494)]
        result = scanner.run_scan("fake-key", max_credits=6)
        self.assertEqual(result["plus_ev"]["opportunities_count"], 0)
        self.assertEqual(result["quality"]["stale_markets"], 1)

    @patch("scanner.requests.get")
    def test_rate_limit_stops_remaining_sports(self, get):
        sports = SPORTS + [{"key": "baseball_mlb", "active": True}]
        get.side_effect = [response(sports), response({"message": "Rate limited"}, status=429)]
        result = scanner.run_scan("fake-key", max_credits=12)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], 429)
        self.assertEqual(get.call_count, 2)

    @patch("scanner.requests.get")
    def test_one_failed_sport_preserves_successful_results_as_partial(self, get):
        sports = SPORTS + [{"key": "baseball_mlb", "active": True}]
        get.side_effect = [
            response(sports),
            response([self.event], cost=6, remaining=494),
            response({"message": "Unavailable"}, status=503, remaining=494),
        ]
        result = scanner.run_scan("fake-key", max_credits=12)
        self.assertTrue(result["success"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["arbitrage"]["opportunities_count"], 1)

    @patch("scanner.requests.get")
    def test_missing_headers_are_reported_as_unknown_not_zero(self, get):
        get.side_effect = [response(SPORTS, headers=False), response([], headers=False)]
        result = scanner.run_scan("fake-key", max_credits=6)
        self.assertIsNone(result["quota"]["credits_used"])
        self.assertIsNone(result["quota"]["remaining"])
        self.assertFalse(result["quota"]["complete"])

    @patch("scanner.requests.get")
    def test_redirects_are_not_followed_with_credentials(self, get):
        get.return_value = response({}, status=302)
        with self.assertRaises(scanner.ScannerError):
            scanner.fetch_sports("fake-key")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_quota_distinguishes_zero_from_missing(self):
        quota = Quota()
        quota.record({"x-requests-last": "0", "x-requests-remaining": "0"}, paid=True)
        self.assertEqual(quota.as_dict()["credits_used"], 0)
        self.assertEqual(quota.remaining, 0)
        quota.record({}, paid=True)
        self.assertIsNone(quota.as_dict()["credits_used"])

    def test_sharp_fallback_works_per_market_and_signed_line(self):
        event = game(
            book("pinnacle", [outcome("Home", 2, -3.5), outcome("Away", 2, 3.5)], "spreads"),
            book("betfair_ex_eu", [outcome("Home", 2), outcome("Away", 2)]),
            book("new_au_book", [outcome("Home", 2.2), outcome("Away", 1.9)]),
        )
        for offer in event["bookmakers"][1]["markets"][0]["outcomes"]:
            offer["bet_limit"] = 100
        edges = scanner._collect_plus_ev_opportunities(
            event, ["h2h", "spreads"], scanner._sharp_priority("pinnacle"), 0, 0, 1000, 0.25
        )
        self.assertEqual(edges[0]["sharp"]["key"], "betfair_ex_eu")

    @patch("scanner.requests.get")
    def test_bookmaker_filter_applies_to_all_tabs_but_keeps_reference(self, get):
        get.side_effect = [response(SPORTS), response([self.event], cost=6, remaining=494)]
        result = scanner.run_scan("fake-key", max_credits=6, target_books=["new_au_book"])
        self.assertEqual(result["arbitrage"]["opportunities_count"], 1)
        self.assertEqual(result["plus_ev"]["opportunities_count"], 1)
        self.assertEqual(result["plus_ev"]["opportunities"][0]["sharp"]["key"], "pinnacle")

    def test_concurrent_scans_are_rejected(self):
        with web.SCAN_LOCK:
            with patch.object(web, "ENV_API_KEY", None):
                result = web.app.test_client().post(
                    "/scan", json={"apiKey": "fake-key", "maxCredits": 6}
                )
        self.assertEqual(result.status_code, 409)

    @patch("scanner.requests.get")
    def test_plan_endpoint_preserves_all_four_window_choices(self, get):
        get.return_value = response(SPORTS)
        with patch.object(web, "ENV_API_KEY", None):
            for hours in (24, 48, 168, None):
                result = web.app.test_client().post(
                    "/plan", json={"apiKey": "fake-key", "windowHours": hours}
                )
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json["plan"]["window_hours"], hours)
                self.assertNotIn("/odds/", get.call_args.args[0])

    @patch("scanner.requests.get")
    def test_provider_gets_bounded_horizon_but_no_upper_bound_for_infinite(self, get):
        get.return_value = response([])
        for hours in (24, 48, 168, None):
            scanner.fetch_odds_for_sport(
                "fake-key", "basketball_nba", ["h2h"], ["us"], window_hours=hours
            )
            params = get.call_args.kwargs["params"]
            self.assertEqual("commenceTimeTo" in params, hours is not None)
            if hours is not None:
                start = dt.datetime.fromisoformat(params["commenceTimeFrom"].replace("Z", "+00:00"))
                end = dt.datetime.fromisoformat(params["commenceTimeTo"].replace("Z", "+00:00"))
                self.assertAlmostEqual((end - start).total_seconds(), hours * 3600, delta=1)

    def test_oversized_scan_body_returns_json(self):
        result = web.app.test_client().post(
            "/scan", data='{"x":"' + "a" * 20000 + '"}', content_type="application/json"
        )
        self.assertEqual(result.status_code, 413)
        self.assertFalse(result.json["success"])


if __name__ == "__main__":
    unittest.main()
