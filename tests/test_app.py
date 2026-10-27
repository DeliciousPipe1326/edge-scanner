import unittest
from unittest.mock import patch

import app as web


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()
        self.key_patch = patch.object(web, "ENV_API_KEY", None)
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def test_home_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Edge Scanner", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_environment_key_is_never_rendered_into_the_page(self):
        with patch.object(web, "ENV_API_KEY", "private-test-key-do-not-render"):
            response = self.client.get("/")
        self.assertNotIn(b"private-test-key-do-not-render", response.data)
        self.assertIn(b'"hasEnvKey": true', response.data)
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])

    @patch("app.run_scan", return_value={"success": True})
    def test_valid_scan_preserves_zero_settings(self, scan):
        response = self.client.post(
            "/scan", json={"apiKey": "key", "minEdgePercent": 0, "bankroll": 0, "kellyFraction": 0}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(scan.call_args.kwargs["bankroll"], 0)
        self.assertEqual(scan.call_args.kwargs["min_edge_percent"], 0)
        self.assertEqual(scan.call_args.kwargs["kelly_fraction"], 0)

    @patch("app.run_scan")
    def test_invalid_values_fail_before_using_api(self, scan):
        bad = [{"stake": value} for value in ("NaN", "Infinity", 10**400, 0, -1, True, 0.001)]
        bad += [
            {"sports": "basketball_nba"},
            {"sports": [{}]},
            {"sports": ["../sports"]},
            {"apiKey": 123},
            {"regions": []},
            {"regions": ["invalid"]},
            {"sharpBook": []},
            {"allSports": "false"},
            {"commission": 21},
            {"kellyFraction": -1},
            {"bankroll": None},
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                response = self.client.post("/scan", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json["success"])
        scan.assert_not_called()

    def test_json_body_must_be_object(self):
        for data in ("null", "[]", '"string"', "{bad"):
            response = self.client.post("/scan", data=data, content_type="application/json")
            self.assertEqual(response.status_code, 400)

    def test_form_and_cross_origin_requests_are_rejected(self):
        self.assertEqual(
            self.client.post("/scan", data="{}", content_type="text/plain").status_code, 415
        )
        self.assertEqual(
            self.client.post(
                "/scan", json={}, headers={"Origin": "https://other.example"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/scan", json={}, headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code,
            403,
        )

    def test_untrusted_host_is_rejected(self):
        self.assertEqual(self.client.get("/", headers={"Host": "other.example"}).status_code, 400)

    def test_missing_key_is_client_error(self):
        response = self.client.post("/scan", json={})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
