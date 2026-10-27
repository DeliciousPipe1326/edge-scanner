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
from scanner import run_scan

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1", "[::1]"]
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
    )


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
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    result = run_scan(
        api_key,
        sports,
        all_sports,
        stake_value,
        regions_value or DEFAULT_REGION_KEYS,
        commission_rate,
        sharp_book=sharp_book,
        min_edge_percent=min_edge_percent,
        bankroll=bankroll_value,
        kelly_fraction=kelly_fraction,
    )
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


@app.after_request
def response_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _port_available(port: int) -> bool:
    if port <= 0:
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

    ensure_data_dir()
    port = _choose_port(args.port)
    if port > 0:
        timer = threading.Timer(1.0, open_browser, args=(port,))
        timer.start()
    else:
        timer = None
    try:
        app.run(port=port or 0, debug=False)
    finally:
        if timer:
            timer.cancel()


if __name__ == "__main__":
    main()
