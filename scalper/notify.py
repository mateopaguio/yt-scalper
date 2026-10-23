"""Send the digest out: SMS via Twilio, push via ntfy.sh."""
from __future__ import annotations

import requests

from .config import Config

SMS_LIMIT = 300  # two segments, keeps per-message cost predictable


def build_sms(digest: dict, site_url: str, new_only: bool = False) -> str:
    """Short, scannable, and honest about what it is."""
    agg = digest.get("aggregate", {})
    tickers = agg.get("tickers", [])
    count = digest.get("video_count", 0)

    lead = "New" if new_only else "Scalper"

    if not tickers:
        body = f"{lead}: {count} item(s), no clear picks."
    else:
        top = tickers[:4]
        parts = []
        for entry in top:
            arrow = {"bullish": "+", "bearish": "-", "split": "~"}.get(
                entry.get("consensus", "split"), "~"
            )
            nchan = len(entry.get("channels", []))
            suffix = f"x{nchan}ch" if nchan > 1 else ""
            parts.append(f"{arrow}{entry['ticker']}{suffix}")
        body = f"{lead} ({count}): " + " ".join(parts)

    if site_url:
        body += f"\n{site_url}"
    if len(body) > SMS_LIMIT:
        body = body[: SMS_LIMIT - 3] + "..."
    return body


def send_sms(cfg: Config, message: str) -> bool:
    creds = cfg.twilio
    try:
        from twilio.rest import Client
    except ImportError:
        print("  ! twilio package not installed")
        return False

    try:
        client = Client(creds["sid"], creds["token"])
        sent = client.messages.create(
            body=message, from_=creds["from"], to=creds["to"]
        )
        print(f"  - SMS queued ({sent.sid})")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ! SMS failed: {exc}")
        return False


def send_push(cfg: Config, digest: dict, message: str) -> bool:
    topic = cfg.ntfy_topic
    if not topic:
        return False

    agg = digest.get("aggregate", {})
    tickers = agg.get("tickers", [])
    title = (
        f"{len(tickers)} tickers across {digest.get('video_count', 0)} videos"
        if tickers
        else "No new picks today"
    )

    headers = {"Title": title, "Priority": "default", "Tags": "chart_with_upwards_trend"}
    if cfg.site_url:
        headers["Click"] = cfg.site_url

    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        print("  - push sent")
        return True
    except requests.RequestException as exc:
        print(f"  ! push failed: {exc}")
        return False


def dispatch(cfg: Config, digest: dict, message: str | None = None) -> None:
    has_findings = bool(digest.get("aggregate", {}).get("tickers"))
    if cfg.notify.get("only_if_findings") and not has_findings:
        print("  - nothing worth sending, staying quiet")
        return

    message = build_sms(digest, cfg.site_url)

    if cfg.sms_enabled():
        send_sms(cfg, message)
    elif cfg.notify.get("sms"):
        print("  - SMS enabled but TWILIO_* secrets are missing")

    if cfg.push_enabled():
        send_push(cfg, digest, message)
    elif cfg.notify.get("push"):
        print("  - push enabled but NTFY_TOPIC is missing")
