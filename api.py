#!/usr/bin/env python3
"""Reference HTTP API over the service layer.

Zero dependencies — standard library only — so it runs anywhere without
installing a framework. This is a working reference, not the recommended
production server: in The Byrde, import `scalper.services` directly and
wrap each function in your existing framework's routing.

    python api.py --port 8000

Endpoints (all GET, all JSON):

    GET /health
    GET /channels
    GET /channels/<name>
    GET /videos?limit=&channel=&ticker=&tier=
    GET /videos/<video_id>
    GET /mentions?ticker=&days=
    GET /creators?days=
    GET /catalysts?days=
    GET /rankings?days=&limit=
    GET /report
    GET /performance?ticker=

Binds to 127.0.0.1 by default. It performs no authentication, so do not
expose it to a network without putting auth in front of it.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from scalper import services


def _int(params: dict, key: str, default):
    try:
        return int(params.get(key, [default])[0])
    except (ValueError, TypeError):
        return default


def _str(params: dict, key: str):
    value = params.get(key, [None])[0]
    return value or None


def route(path: str, params: dict) -> tuple[int, dict]:
    parts = [p for p in path.strip("/").split("/") if p]

    if not parts or parts[0] == "health":
        return 200, services.data_health()

    head = parts[0]
    rest = unquote(parts[1]) if len(parts) > 1 else None

    if head == "channels":
        return (200, services.channel_detail(rest)) if rest \
            else (200, services.list_channels())

    if head == "videos":
        if rest:
            result = services.video_detail(rest)
            return (200 if result["ok"] else 404), result
        return 200, services.list_videos(
            limit=_int(params, "limit", 50),
            channel=_str(params, "channel"),
            ticker=_str(params, "ticker"),
            tier=_int(params, "tier", None) if "tier" in params else None,
        )

    if head == "mentions":
        result = services.stock_mentions(
            ticker=_str(params, "ticker"), days=_int(params, "days", 7)
        )
        return (200 if result["ok"] else 404), result

    if head == "creators":
        return 200, services.creator_reliability(days=_int(params, "days", 90))

    if head == "catalysts":
        return 200, services.catalyst_intelligence(days=_int(params, "days", 7))

    if head == "rankings":
        return 200, services.opportunity_rankings(
            days=_int(params, "days", 7), limit=_int(params, "limit", 10)
        )

    if head == "report":
        return 200, services.hourly_report()

    if head == "performance":
        return 200, services.historical_performance(ticker=_str(params, "ticker"))

    return 404, {"ok": False, "error": f"unknown endpoint {path!r}"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        try:
            status, payload = route(parsed.path, parse_qs(parsed.query))
        except Exception as exc:  # noqa: BLE001
            status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        body = json.dumps(payload, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    print(f"Serving on http://{args.host}:{args.port}  (no auth — keep it local)")
    HTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
