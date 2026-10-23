"""One-time historical backfill via the YouTube Data API v3.

Why this exists: a channel's RSS feed returns only its ~15 most recent
uploads, so "import the latest 30 videos" is not possible over RSS. The
Data API's playlistItems endpoint pages through the full uploads
playlist at 1 quota unit per call.

Cost for 49 channels at 30 videos each: ~98 units against a 10,000/day
free quota. This runs once; hourly monitoring stays on RSS at zero quota.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

API = "https://www.googleapis.com/youtube/v3"


class QuotaExceeded(RuntimeError):
    pass


def api_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY")


def _get(path: str, params: dict, key: str, timeout: int = 25) -> dict:
    params = {**params, "key": key}
    resp = requests.get(f"{API}/{path}", params=params, timeout=timeout)
    if resp.status_code == 403:
        body = resp.text[:400]
        if "quotaExceeded" in body or "dailyLimitExceeded" in body:
            raise QuotaExceeded("YouTube Data API daily quota exhausted")
        raise RuntimeError(f"YouTube API 403: {body}")
    resp.raise_for_status()
    return resp.json()


def uploads_playlist(channel_id: str, key: str) -> str | None:
    """Every channel has an 'uploads' playlist holding all its videos."""
    data = _get("channels", {"part": "contentDetails", "id": channel_id}, key)
    items = data.get("items") or []
    if not items:
        return None
    return (
        items[0].get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )


def recent_uploads(channel_id: str, key: str, limit: int = 30) -> list[dict]:
    """The channel's most recent uploads, newest first.

    Returns plain dicts matching the shape the rest of the pipeline
    expects, so backfilled videos and RSS-discovered videos are
    indistinguishable downstream.
    """
    playlist = uploads_playlist(channel_id, key)
    if not playlist:
        return []

    out: list[dict] = []
    token: str | None = None

    while len(out) < limit:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist,
            "maxResults": min(50, limit - len(out)),
        }
        if token:
            params["pageToken"] = token

        data = _get("playlistItems", params, key)
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            details = item.get("contentDetails") or {}
            vid = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
            raw_date = details.get("videoPublishedAt") or snippet.get("publishedAt")
            if not vid or not raw_date:
                continue
            try:
                published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                continue
            out.append({
                "video_id": vid,
                "title": (snippet.get("title") or "").strip(),
                "published": published.isoformat(),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "description": (snippet.get("description") or "")[:500],
            })

        token = data.get("nextPageToken")
        if not token:
            break

    return out[:limit]


def backfill(cfg, store_mod, limit: int = 30) -> tuple[list[dict], dict]:
    """Discover historical videos for every resolved channel.

    Discovery only -- this does not fetch transcripts or call Claude.
    Videos land in the pending queue and are processed by normal runs,
    which keeps a 1,400-video backfill from becoming one enormous job.
    """
    key = api_key()
    if not key:
        return [], {"error": "YOUTUBE_API_KEY is not set"}

    from . import feeds

    channels, _ = feeds.dedupe(cfg.channels)
    seen = store_mod.seen_ids() | {
        i["video"]["video_id"] for i in store_mod.load_pending()
    }

    discovered: list[dict] = []
    stats = {"channels": 0, "found": 0, "already_known": 0, "failed": []}

    for channel in channels:
        if not channel.channel_id:
            continue
        try:
            uploads = recent_uploads(channel.channel_id, key, limit)
        except QuotaExceeded:
            stats["failed"].append(f"{channel.name}: quota exhausted — rerun tomorrow")
            break
        except Exception as exc:  # noqa: BLE001
            stats["failed"].append(f"{channel.name}: {type(exc).__name__}")
            continue

        stats["channels"] += 1
        for video in uploads:
            if video["video_id"] in seen:
                stats["already_known"] += 1
                continue
            discovered.append({
                **video,
                "channel": channel.name,
                "channel_id": channel.channel_id,
                "tier": channel.tier,
                "source_type": "video",
            })
            seen.add(video["video_id"])

        print(f"  - [T{channel.tier}] {channel.name}: {len(uploads)} upload(s)")

    stats["found"] = len(discovered)
    return discovered, stats
