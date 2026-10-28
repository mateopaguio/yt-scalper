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


def dispatch(cfg: Config, digest: dict, message: str | None = None) -> dict:
    """Send the legacy digest. Returns what actually happened.

    This returned None, so the caller could not tell a delivered push
    from a failed one - and marked every item notified either way. A
    failed send therefore consumed the alert permanently: the items
    were recorded as seen and never considered again.

    `attempted` separates "we tried and it failed" from "there was
    nothing to send". Only the first should hold back the state.
    """
    result = {"attempted": False, "delivered": False, "channels": {},
              "reason": ""}

    has_findings = bool(digest.get("aggregate", {}).get("tickers"))
    if cfg.notify.get("only_if_findings") and not has_findings:
        print("  - nothing worth sending, staying quiet")
        result["reason"] = "no findings worth sending"
        return result

    # Honour a caller-supplied message. This unconditionally overwrote
    # the parameter, so the signal-specific alert built in
    # run.notify_if_new -- and the catalyst tag prefixed to it -- were
    # discarded on every send and replaced with the generic digest.
    message = message or build_sms(digest, cfg.site_url)

    if cfg.sms_enabled():
        result["attempted"] = True
        result["channels"]["sms"] = bool(send_sms(cfg, message))
    elif cfg.notify.get("sms"):
        print("  - SMS enabled but TWILIO_* secrets are missing")
        result["reason"] = "TWILIO_* missing"

    if cfg.push_enabled():
        result["attempted"] = True
        result["channels"]["push"] = bool(send_push(cfg, digest, message))
    elif cfg.notify.get("push"):
        print("  - push enabled but NTFY_TOPIC is missing")
        result["reason"] = "NTFY_TOPIC missing"

    result["delivered"] = any(result["channels"].values())
    if result["attempted"] and not result["delivered"]:
        result["reason"] = "every enabled channel failed"
    return result


def build_signal_alert(sig: dict, site_url: str = "",
                       min_lift: float = 1.0,
                       min_materiality: int = 3) -> tuple[str, list[str]]:
    """Alert text built from what is actually moving, not a fixed watchlist.

    Returns (message, alert_keys). The keys let the caller suppress a
    repeat of the same signal on the next run.
    """
    lines, keys = [], []

    hot = [d for d in (sig.get("momentum") or [])
           if d["lift"] >= min_lift and d["quality"] > 0][:4]
    if hot:
        parts = []
        for d in hot:
            parts.append("%s %dx%s" % (d["ticker"], d["recent_mentions"],
                                       " NEW" if d["is_new"] else ""))
            keys.append("mom:%s:%d" % (d["ticker"], d["recent_mentions"]))
        lines.append("Heating up: " + ", ".join(parts))

    nl = [d for d in (sig.get("news_linked") or [])
          if d["max_materiality"] >= min_materiality][:3]
    if nl:
        parts = []
        for d in nl:
            ev = d["events"][0] if d["events"] else {}
            parts.append("%s (%s)" % (d["ticker"],
                                      str(ev.get("event_type", "")).replace("_", " ")))
            keys.append("news:%s:%s" % (d["ticker"], ev.get("url", "")))
        lines.append("Filed news + chatter: " + ", ".join(parts))

    emg = [d for d in (sig.get("emerging") or []) if d["quality"] >= 2][:3]
    if emg:
        parts = []
        for d in emg:
            parts.append(d["ticker"])
            keys.append("new:%s" % d["ticker"])
        lines.append("New with support: " + ", ".join(parts))

    sec = [d for d in (sig.get("sectors") or []) if d["quality"] >= 3][:2]
    if sec:
        lines.append("Sectors: " + ", ".join(
            "%s (%s)" % (d["sector"], d["tilt"]) for d in sec))

    if not lines:
        return "", []
    if site_url:
        lines.append(site_url)
    return "\n".join(lines), keys


def send_tiered(cfg, tier_messages: dict, dry_run: bool = False) -> dict:
    """Send one push per tier, each with its own ntfy priority.

    Separate messages on purpose: a critical alert buried inside a digest
    is a critical alert nobody reads. Priority 5 breaks through a silent
    phone; priority 1 arrives without a sound.
    """
    topic = getattr(cfg, "ntfy_topic", None)
    results = {}
    for tier, m in (tier_messages or {}).items():
        if dry_run or not topic:
            results[tier] = {"sent": False,
                             "reason": "dry run" if dry_run else "no NTFY_TOPIC",
                             "count": m.get("count", 0)}
            continue
        headers = {
            "Title": m["title"],
            "Priority": str(m["priority"]),
            "Tags": m["tags"],
        }
        if getattr(cfg, "site_url", ""):
            headers["Click"] = cfg.site_url
        try:
            resp = requests.post(f"https://ntfy.sh/{topic}",
                                 data=m["body"].encode("utf-8"),
                                 headers=headers, timeout=15)
            resp.raise_for_status()
            results[tier] = {"sent": True, "count": m.get("count", 0)}
        except requests.RequestException as exc:
            # Never let a notification failure kill the run.
            print(f"  ! {tier} push failed: {str(exc)[:70]}")
            results[tier] = {"sent": False, "reason": str(exc)[:70],
                             "count": m.get("count", 0)}
    return results
