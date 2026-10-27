from __future__ import annotations

import argparse
import math
import os
import re
import socket
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from config import (
    DEFAULT_BANKROLL,
    DEFAULT_COMMISSION,
    DEFAULT_KELLY_FRACTION,
    DEFAULT_REGION_KEYS,
    DEFAULT_SHARP_BOOK,
    DEFAULT_SPORT_OPTIONS,
    KELLY_OPTIONS,
    MIN_EDGE_PERCENT,
    REGION_OPTIONS,
    REGION_CONFIG,
    SHARP_BOOKS,
)
from scanner import plan_scan, run_scan
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server
from quality import DEFAULT_MAX_AGE

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1", "[::1]"]
SCAN_LOCK = threading.Lock()
ENV_API_KEY = os.getenv("ODDS_API_KEY") or os.getenv("THEODDSAPI_API_KEY")


@app.route("/")
def index() -> str:
    return render_template(
        "index.html",
        default_sports=DEFAULT_SPORT_OPTIONS,
        region_options=REGION_OPTIONS,
        default_commission_percent=int(DEFAULT_COMMISSION * 100),
        has_env_key=bool(ENV_API_KEY),
        sharp_books=SHARP_BOOKS,
        default_sharp_book=DEFAULT_SHARP_BOOK,
        default_min_edge_percent=MIN_EDGE_PERCENT,
        default_bankroll=DEFAULT_BANKROLL,
        default_kelly_fraction=DEFAULT_KELLY_FRACTION,
        kelly_options=KELLY_OPTIONS,
        scanner_config={
            "hasEnvKey": bool(ENV_API_KEY),
            "defaultSharpBook": DEFAULT_SHARP_BOOK,
            "defaultMinEdgePercent": MIN_EDGE_PERCENT,
            "defaultBankroll": DEFAULT_BANKROLL,
            "defaultKellyFraction": DEFAULT_KELLY_FRACTION,
        },
    )


@app.route("/plan", methods=["POST"])
@app.route("/scan", methods=["POST"])
def scan() -> tuple:
    if not request.is_json:
        return jsonify(success=False, error="Content-Type must be application/json"), 415
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
        request.origin and request.origin != request.host_url.rstrip("/")
    ):
        return jsonify(success=False, error="Cross-origin scans are not allowed"), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(success=False, error="Expected a JSON object"), 400
    try:
        api_key = ENV_API_KEY or payload.get("apiKey", "")
        if not isinstance(api_key, str):
            raise ValueError("apiKey must be a string")
        api_key = api_key.strip()
        sports = payload.get("sports", [])
        if not isinstance(sports, list) or any(
            not isinstance(sport, str) or not re.fullmatch(r"[a-z0-9_]+", sport) for sport in sports
        ):
            raise ValueError("sports must be a list of sport keys")
        all_sports = payload.get("allSports", False)
        if not isinstance(all_sports, bool):
            raise ValueError("allSports must be a boolean")
        if "sports" in payload and not sports and not all_sports:
            raise ValueError("Select at least one sport")
        regions_value = payload.get("regions", DEFAULT_REGION_KEYS)
        if (
            not isinstance(regions_value, list)
            or not regions_value
            or any(
                not isinstance(region, str) or region not in REGION_CONFIG
                for region in regions_value
            )
        ):
            raise ValueError("regions must be a non-empty list of supported regions")
        sharp_book = payload.get("sharpBook", DEFAULT_SHARP_BOOK)
        if not isinstance(sharp_book, str) or sharp_book not in {b["key"] for b in SHARP_BOOKS}:
            raise ValueError("Unsupported sharpBook")
        stake_value = _number(payload, "stake", 100.0, 0.01, 1e9)
        if not math.isclose(stake_value, round(stake_value, 2), abs_tol=1e-8):
            raise ValueError("stake must have at most two decimal places")
        commission_rate = _number(payload, "commission", DEFAULT_COMMISSION * 100, 0, 20) / 100
        min_edge_percent = _number(payload, "minEdgePercent", MIN_EDGE_PERCENT, 0, 100)
        bankroll_value = _number(payload, "bankroll", DEFAULT_BANKROLL, 0, 1e9)
        kelly_fraction = _number(payload, "kellyFraction", DEFAULT_KELLY_FRACTION, 0, 1)
        max_quote_age = _number(payload, "maxQuoteAge", DEFAULT_MAX_AGE, 15, 900)
        window_hours = payload.get("windowHours", 24)
        if window_hours is not None and (
            isinstance(window_hours, bool) or window_hours not in (24, 48, 168)
        ):
            raise ValueError("windowHours must be 24, 48, 168, or null for Infinite")
        max_credits = _number(payload, "maxCredits", 0, 0, 100000)
        if max_credits != int(max_credits):
            raise ValueError("maxCredits must be an integer")
        target_books = payload.get("bookmakers", [])
        if (
            not isinstance(target_books, list)
            or len(target_books) > 100
            or any(
                not isinstance(key, str) or not re.fullmatch(r"[a-z0-9_]+", key)
                for key in target_books
            )
        ):
            raise ValueError("bookmakers must be a list of bookmaker keys")
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    if request.path == "/plan":
        result = plan_scan(api_key, sports, all_sports, regions_value, sharp_book, window_hours)
    else:
        if not SCAN_LOCK.acquire(blocking=False):
            return jsonify(success=False, error="A scan is already running. Please wait."), 409
        try:
            result = run_scan(
                api_key,
                sports,
                all_sports,
                stake_value,
                regions_value,
                commission_rate,
                sharp_book=sharp_book,
                min_edge_percent=min_edge_percent,
                bankroll=bankroll_value,
                kelly_fraction=kelly_fraction,
                max_quote_age=max_quote_age,
                target_books=target_books,
                max_credits=int(max_credits),
                window_hours=window_hours,
            )
        finally:
            SCAN_LOCK.release()
    status = 200 if result.get("success") else result.get("error_code", 500)
    return jsonify(result), status


def _number(payload: dict, key: str, default: float, lower: float, upper: float) -> float:
    value = payload.get(key, default)
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{key} must be a finite number") from None
    if isinstance(value, bool) or not math.isfinite(number) or not lower <= number <= upper:
        raise ValueError(f"{key} must be between {lower} and {upper}")
    return number


@app.errorhandler(HTTPException)
def http_error(error):
    if request.path in {"/plan", "/scan"}:
        return jsonify(success=False, error=error.description), error.code
    return error


@app.after_request
def response_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


def _port_available(port: int) -> bool:
    if not 0 < port <= 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _choose_port(preferred: Optional[int]) -> int:
    if preferred == 0:
        return 0
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend([5000, 5050, 8000])
    seen = set()
    for port in candidates:
        if port in seen:
            continue
        seen.add(port)
        if _port_available(port):
            return port
    return 0  # fall back to OS-chosen port


def open_browser(port: int) -> None:
    webbrowser.open_new(f"http://localhost:{port}/")


def ensure_data_dir() -> None:
    Path("data").mkdir(exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sports Arbitrage Scanner server")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to run the local server on (default 5000, auto-fallback if busy)",
    )
    args = parser.parse_args()
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    ensure_data_dir()
    port = _choose_port(args.port)
    server = make_server("127.0.0.1", port, app, threaded=True)
    timer = threading.Timer(0.3, open_browser, args=(server.server_port,))
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()


if __name__ == "__main__":
    main()
