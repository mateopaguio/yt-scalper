"""Config loading, with environment variables for anything secret."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"


@dataclass
class Channel:
    name: str
    handle: str | None = None
    url: str | None = None
    channel_id: str | None = None
    tier: int = 2

    def feed_url(self) -> str:
        if not self.channel_id:
            raise ValueError(
                f"Channel {self.name!r} has no channel_id. "
                "Run `python resolve_channels.py` to fill it in."
            )
        return (
            "https://www.youtube.com/feeds/videos.xml"
            f"?channel_id={self.channel_id}"
        )


@dataclass
class Config:
    channels: list[Channel] = field(default_factory=list)
    lookback_hours: int = 26
    max_videos_per_run: int = 12
    min_transcript_words: int = 300
    window_days: int = 3
    pending_max_attempts: int = 12
    pending_give_up_hours: int = 30
    max_catalysts_per_run: int = 25
    request_delay_seconds: float = 4.0
    catalyst_horizon_days: int = 30
    tiers: dict[int, dict[str, Any]] = field(default_factory=dict)
    model: str = "claude-haiku-5-5"
    max_output_tokens: int = 4096
    notify: dict[str, Any] = field(default_factory=dict)
    site_url: str = ""
    watchlist: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    # --- secrets, read from the environment only ---
    @property
    def anthropic_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def twilio(self) -> dict[str, str | None]:
        return {
            "sid": os.environ.get("TWILIO_ACCOUNT_SID"),
            "token": os.environ.get("TWILIO_AUTH_TOKEN"),
            "from": os.environ.get("TWILIO_FROM_NUMBER"),
            "to": os.environ.get("TWILIO_TO_NUMBER"),
        }

    @property
    def ntfy_topic(self) -> str | None:
        return os.environ.get("NTFY_TOPIC")

    @property
    def proxy(self) -> str | None:
        """Optional HTTP proxy for transcript fetching. See README."""
        return os.environ.get("YT_PROXY_URL")

    def sms_enabled(self) -> bool:
        creds = self.twilio
        return bool(self.notify.get("sms")) and all(creds.values())

    def push_enabled(self) -> bool:
        return bool(self.notify.get("push")) and bool(self.ntfy_topic)


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}

    channels = [
        Channel(
            name=c.get("name") or c.get("handle") or "unnamed",
            handle=c.get("handle"),
            url=c.get("url"),
            channel_id=c.get("channel_id"),
            tier=int(c.get("tier", 2)),
        )
        for c in (raw.get("channels") or [])
    ]

    return Config(
        channels=channels,
        lookback_hours=raw.get("lookback_hours", 26),
        max_videos_per_run=raw.get("max_videos_per_run", 12),
        min_transcript_words=raw.get("min_transcript_words", 300),
        window_days=raw.get("window_days", 3),
        pending_max_attempts=raw.get("pending_max_attempts", 12),
        pending_give_up_hours=raw.get("pending_give_up_hours", 30),
        max_catalysts_per_run=raw.get("max_catalysts_per_run", 25),
        request_delay_seconds=float(raw.get("request_delay_seconds", 4.0)),
        catalyst_horizon_days=raw.get("catalyst_horizon_days", 30),
        tiers={int(k): v for k, v in (raw.get("tiers") or {}).items()},
        model=raw.get("model", "claude-haiku-5-5"),
        max_output_tokens=raw.get("max_output_tokens", 4096),
        notify=raw.get("notify") or {},
        site_url=(raw.get("site_url") or "").rstrip("/") + "/"
        if raw.get("site_url")
        else "",
        watchlist=[t.upper() for t in (raw.get("watchlist") or [])],
        raw=raw,
    )


DEFAULT_TIERS: dict[int, dict[str, Any]] = {
    1: {"label": "Market news & earnings", "interval_hours": 1,
        "counts_toward_consensus": True, "priority": 1},
    2: {"label": "Stock research", "interval_hours": 1,
        "counts_toward_consensus": True, "priority": 2},
    3: {"label": "Macro, valuation & education", "interval_hours": 24,
        "counts_toward_consensus": True, "priority": 3},
    4: {"label": "Speculative & opinion-heavy", "interval_hours": 1,
        "counts_toward_consensus": False, "priority": 4},
}


def tier_spec(cfg: "Config", tier: int) -> dict[str, Any]:
    """Settings for a tier, falling back to the built-in defaults."""
    base = dict(DEFAULT_TIERS.get(tier, DEFAULT_TIERS[2]))
    base.update(cfg.tiers.get(tier) or {})
    return base


def consensus_tiers(cfg: "Config") -> set[int]:
    """Tiers whose agreement counts as genuine corroboration."""
    return {
        t for t in (1, 2, 3, 4)
        if tier_spec(cfg, t).get("counts_toward_consensus", True)
    }
