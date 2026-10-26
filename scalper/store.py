"""Simple JSON store. No database needed at this scale, and a JSON
file commits cleanly back into the repo so the history is browsable."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR

VIDEOS_PATH = DATA_DIR / "videos.json"
DIGESTS_PATH = DATA_DIR / "digests.json"
PENDING_PATH = DATA_DIR / "pending.json"
NOTIFIED_PATH = DATA_DIR / "notified.json"
CATALYSTS_PATH = DATA_DIR / "catalysts.json"
CHANNEL_STATE_PATH = DATA_DIR / "channel_state.json"
PERFORMANCE_PATH = DATA_DIR / "performance.json"


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path) as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_videos() -> list[dict]:
    """Every video processed so far, newest first."""
    return _read(VIDEOS_PATH, [])


def seen_ids() -> set[str]:
    return {v["video_id"] for v in load_videos() if v.get("video_id")}


def save_videos(videos: list[dict]) -> None:
    videos.sort(key=lambda v: v.get("published", ""), reverse=True)
    _write(VIDEOS_PATH, videos[:600])  # keep the file a sane size


def add_videos(new: list[dict]) -> list[dict]:
    existing = load_videos()
    known = {v["video_id"] for v in existing}
    merged = existing + [v for v in new if v["video_id"] not in known]
    save_videos(merged)
    return merged


def load_digests() -> list[dict]:
    return _read(DIGESTS_PATH, [])


def add_digest(digest: dict) -> list[dict]:
    digests = load_digests()
    digests.insert(0, digest)
    _write(DIGESTS_PATH, digests[:120])
    return digests


def recent_videos(days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [v for v in load_videos() if v.get("published", "") >= cutoff]


# ---------------------------------------------------------------
# Pending queue.
#
# Hourly polling finds videos within minutes of upload, often before
# YouTube has finished generating captions. Those are not failures --
# they are "not yet". They go here and get retried, instead of being
# marked processed and lost.
# ---------------------------------------------------------------

def load_pending() -> list[dict]:
    return _read(PENDING_PATH, [])


def save_pending(items: list[dict]) -> None:
    _write(PENDING_PATH, items)


def queue_pending(record: dict, reason: str, count_attempt: bool = True) -> None:
    """Add or re-queue a video whose transcript was not ready."""
    items = load_pending()
    now = datetime.now(timezone.utc).isoformat()

    for item in items:
        if item["video"]["video_id"] == record["video_id"]:
            if count_attempt:
                item["attempts"] += 1
            else:
                # Throttled: we never got to try. Push the age clock forward
                # so a long block can't silently expire a healthy video.
                item["throttled"] = item.get("throttled", 0) + 1
                item["eligible_since"] = now
            item["last_attempt"] = now
            item["reason"] = reason
            save_pending(items)
            return

    items.append({
        "video": record,
        "attempts": 1 if count_attempt else 0,
        "throttled": 0 if count_attempt else 1,
        "first_seen": now,
        "eligible_since": now,
        "last_attempt": now,
        "reason": reason,
    })
    save_pending(items)


def drop_pending(video_id: str) -> None:
    save_pending([i for i in load_pending() if i["video"]["video_id"] != video_id])


def due_pending(max_attempts: int = 12, give_up_hours: int = 30) -> tuple[list[dict], list[dict]]:
    """Split the queue into (retry now, give up).

    Giving up is itself a result worth recording -- a video with no
    captions after 30 hours almost certainly has captions disabled.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=give_up_hours)
    retry, expired = [], []

    for item in load_pending():
        # Age is measured from when we could last actually attempt a fetch,
        # not from first sight - otherwise an outage expires good videos.
        clock = item.get("eligible_since") or item.get("first_seen", "")
        too_old = False
        try:
            too_old = datetime.fromisoformat(clock) < cutoff
        except ValueError:
            too_old = True

        if item.get("attempts", 0) >= max_attempts or too_old:
            expired.append(item)
        else:
            retry.append(item)

    return retry, expired


# ---------------------------------------------------------------
# Notification dedup.
#
# At 24 runs a day, the digest page can show a rolling window while
# notifications stay strictly about what is new since the last alert.
# ---------------------------------------------------------------

def notified_ids() -> set[str]:
    return set(_read(NOTIFIED_PATH, {}).get("video_ids", []))


def mark_notified(video_ids: list[str]) -> None:
    payload = _read(NOTIFIED_PATH, {})
    known = payload.get("video_ids", [])
    known.extend(v for v in video_ids if v not in known)
    payload["video_ids"] = known[-800:]
    payload["last_notified_at"] = datetime.now(timezone.utc).isoformat()
    _write(NOTIFIED_PATH, payload)


def last_notified_at() -> str | None:
    return _read(NOTIFIED_PATH, {}).get("last_notified_at")


# ---------------------------------------------------------------
# Catalyst events (regulatory filings, agency releases, macro).
# Kept separate from opinion content: different schema, different
# meaning, and they should never be mixed into creator consensus.
# ---------------------------------------------------------------

def load_catalysts() -> list[dict]:
    return _read(CATALYSTS_PATH, [])


def catalyst_ids() -> set[str]:
    return {c["event_id"] for c in load_catalysts() if c.get("event_id")}


def add_catalysts(new: list[dict]) -> list[dict]:
    existing = load_catalysts()
    known = {c["event_id"] for c in existing}
    merged = existing + [c for c in new if c["event_id"] not in known]
    merged.sort(key=lambda c: c.get("published", ""), reverse=True)
    _write(CATALYSTS_PATH, merged[:800])
    return merged


def recent_catalysts(days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [c for c in load_catalysts() if c.get("published", "") >= cutoff]


# ---------------------------------------------------------------
# Per-channel polling state.
#
# Tier 3 is checked daily, tiers 1/2/4 hourly. Tracking the last
# successful check per channel (rather than keying off the clock
# hour) means a missed or delayed run self-heals: the channel is
# simply due the next time anything runs.
# ---------------------------------------------------------------

def channel_state() -> dict:
    return _read(CHANNEL_STATE_PATH, {})


def mark_checked(channel_ids: list[str]) -> None:
    state = channel_state()
    now = datetime.now(timezone.utc).isoformat()
    for cid in channel_ids:
        entry = state.setdefault(cid, {})
        entry["last_checked"] = now
        entry["checks"] = entry.get("checks", 0) + 1
    _write(CHANNEL_STATE_PATH, state)


def is_due(channel_id: str, interval_hours: int) -> bool:
    entry = channel_state().get(channel_id) or {}
    last = entry.get("last_checked")
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - when >= timedelta(
        hours=max(interval_hours, 0) * 0.9  # 10% slack so hourly cron never skips
    )


# --- price-scored recommendation records ---

def load_performance() -> list[dict]:
    return _read(PERFORMANCE_PATH, [])


def save_performance(records: list[dict]) -> None:
    _write(PERFORMANCE_PATH, records[:2000])
