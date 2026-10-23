"""Find new videos by reading each channel's public RSS feed.

RSS is the cheap path: no API key, no quota, no OAuth. Each feed
returns a channel's ~15 most recent uploads.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from .config import Channel, Config

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
UA = {"User-Agent": "Mozilla/5.0 (compatible; yt-scalper/1.0)"}


@dataclass
class Video:
    video_id: str
    title: str
    channel: str
    published: datetime
    url: str
    description: str = ""
    tier: int = 2
    channel_id: str = ""

    def as_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "channel": self.channel,
            "published": self.published.isoformat(),
            "url": self.url,
            "tier": self.tier,
            "channel_id": self.channel_id,
            "source_type": "video",
        }


def resolve_channel_id(channel: Channel, timeout: int = 20) -> str | None:
    """Turn an @handle or channel URL into a stable UC... channel ID.

    Scrapes the channel page for the embedded channelId. Only needed
    once per channel; the result gets written back into config.yml.
    """
    if channel.channel_id:
        return channel.channel_id

    target = channel.url
    if not target and channel.handle:
        handle = channel.handle if channel.handle.startswith("@") else f"@{channel.handle}"
        target = f"https://www.youtube.com/{handle}"
    if not target:
        return None

    try:
        resp = requests.get(target, headers=UA, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! could not load {target}: {exc}")
        return None

    for pattern in (
        r'"channelId":"(UC[\w-]{22})"',
        r'<meta itemprop="identifier" content="(UC[\w-]{22})"',
        r'channel/(UC[\w-]{22})',
    ):
        match = re.search(pattern, resp.text)
        if match:
            return match.group(1)
    return None


def dedupe(channels: list[Channel]) -> tuple[list[Channel], list[str]]:
    """Collapse duplicates by official channel ID.

    The same channel can be listed twice under different names or handles
    (say "@Meet Kevin" and a full URL). The channel ID is the only
    identifier YouTube guarantees is stable and unique, so it wins. When
    duplicates disagree on tier, the more informative tier (lower number)
    is kept.
    """
    by_id: dict[str, Channel] = {}
    no_id: list[Channel] = []
    notes: list[str] = []

    for channel in channels:
        if not channel.channel_id:
            no_id.append(channel)
            continue
        existing = by_id.get(channel.channel_id)
        if existing is None:
            by_id[channel.channel_id] = channel
            continue
        notes.append(
            f"{channel.name!r} duplicates {existing.name!r} "
            f"({channel.channel_id}) — keeping one"
        )
        if channel.tier < existing.tier:
            notes.append(
                f"  promoted {existing.name!r} to tier {channel.tier}"
            )
            existing.tier = channel.tier

    # Catch duplicate handles among unresolved entries too.
    seen_handles: dict[str, Channel] = {}
    unique_no_id: list[Channel] = []
    for channel in no_id:
        key = (channel.handle or channel.url or channel.name).lower().lstrip("@")
        if key in seen_handles:
            notes.append(f"{channel.name!r} duplicates {seen_handles[key].name!r} (handle)")
            continue
        seen_handles[key] = channel
        unique_no_id.append(channel)

    return list(by_id.values()) + unique_no_id, notes


def fetch_recent(channel: Channel, timeout: int = 20) -> list[Video]:
    """Pull a channel's recent uploads from its RSS feed."""
    try:
        resp = requests.get(channel.feed_url(), headers=UA, timeout=timeout)
        resp.raise_for_status()
    except (requests.RequestException, ValueError) as exc:
        print(f"  ! feed failed for {channel.name}: {exc}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"  ! bad feed XML for {channel.name}: {exc}")
        return []

    videos: list[Video] = []
    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=NS)
        title = entry.findtext("atom:title", default="", namespaces=NS)
        published_raw = entry.findtext("atom:published", default="", namespaces=NS)
        if not vid or not published_raw:
            continue
        try:
            published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            continue

        group = entry.find("media:group", NS)
        description = ""
        if group is not None:
            description = group.findtext("media:description", default="", namespaces=NS) or ""

        videos.append(
            Video(
                video_id=vid,
                title=title.strip(),
                channel=channel.name,
                published=published,
                url=f"https://www.youtube.com/watch?v={vid}",
                description=description[:500],
                tier=channel.tier,
                channel_id=channel.channel_id or "",
            )
        )
    return videos


def find_new_videos(
    cfg: Config, already_seen: set[str], force: bool = False
) -> list[Video]:
    """Recent videos from every channel that is due a check.

    Tier decides how often a channel is polled and who wins when the
    per-run cap binds: a Tier 1 earnings video outranks a Tier 4 hype
    video for the same slot.
    """
    from . import config as cfgmod
    from . import store

    channels, notes = dedupe(cfg.channels)
    for note in notes:
        print(f"  ! {note}")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.lookback_hours)
    found: list[Video] = []
    checked: list[str] = []
    skipped_not_due = 0

    for channel in channels:
        if not channel.channel_id:
            print(f"  - skipping {channel.name}: no channel_id yet")
            continue

        spec = cfgmod.tier_spec(cfg, channel.tier)
        interval = int(spec.get("interval_hours", 1))
        if not force and not store.is_due(channel.channel_id, interval):
            skipped_not_due += 1
            continue

        recent = fetch_recent(channel)
        checked.append(channel.channel_id)
        fresh = [
            v for v in recent
            if v.published >= cutoff and v.video_id not in already_seen
        ]
        if fresh or recent:
            print(f"  - [T{channel.tier}] {channel.name}: "
                  f"{len(fresh)} new of {len(recent)}")
        found.extend(fresh)

    if skipped_not_due:
        print(f"  - {skipped_not_due} channel(s) not due yet this run")

    if checked:
        store.mark_checked(checked)

    # Newest first within tier, tiers in priority order.
    found.sort(
        key=lambda v: (
            cfgmod.tier_spec(cfg, v.tier).get("priority", v.tier),
            -v.published.timestamp(),
        )
    )
    return found[: cfg.max_videos_per_run]
