"""Fetch video transcripts, with retries and optional proxy support.

YouTube rate-limits transcript requests from datacenter IP ranges,
which is what GitHub Actions runners use. The pipeline handles that
gracefully: blocked videos are reported, not silently dropped, and
setting YT_PROXY_URL routes requests through a residential proxy if
you hit it often. See the README.
"""
from __future__ import annotations

import os

import requests
import time
from dataclasses import dataclass

from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

# "No captions right now" — which for a video uploaded minutes ago usually
# means YouTube has not finished generating them yet, not that none will
# ever exist. run.py decides which it is, based on the video's age.
NOT_YET = (TranscriptsDisabled, NoTranscriptFound)
# Genuinely gone — never retry these.
PERMANENT = (VideoUnavailable, VideoUnplayable)
# Errors that mean "YouTube is throttling this IP" — retry, then report.
THROTTLED = (RequestBlocked, IpBlocked)


@dataclass
class Transcript:
    video_id: str
    text: str
    word_count: int
    blocked: bool = False
    not_ready: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


TRANSCRIPT_TIMEOUT = 30          # seconds, per HTTP request


class _BoundedSession(requests.Session):
    """A session that refuses to wait forever.

    YouTubeTranscriptApi makes its HTTP calls through a requests
    Session and sets no timeout, so a connection that is accepted and
    then never answered blocks the whole pipeline indefinitely.

    That is not hypothetical. The hourly job sat in this call for 20
    hours and 27 minutes using 1.9 seconds of CPU - blocked on a
    socket read, not working - and because launchd will not start a
    second copy of a StartInterval job while one is still running, it
    silently blocked every scheduled run behind it. A job that fails
    is visible; a job that hangs looks exactly like a job that is
    simply not due yet.
    """

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", TRANSCRIPT_TIMEOUT)
        return super().request(*args, **kwargs)


def _client(proxy_url: str | None) -> YouTubeTranscriptApi:
    """Build a client, routing through a residential proxy when configured.

    Three modes, in order of preference:
      1. Webshare credentials (WEBSHARE_USER / WEBSHARE_PASS). The library has
         first-class support for this and retries lookups that come back
         blocked, rotating to a different residential exit each time.
      2. A generic proxy URL (YT_PROXY_URL) for any other provider.
      3. No proxy - direct from this machine's own IP.
    """
    user = os.environ.get("WEBSHARE_USER")
    pw = os.environ.get("WEBSHARE_PASS")
    if user and pw:
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=user,
                proxy_password=pw,
                retries_when_blocked=10,
            ),
            http_client=_BoundedSession(),
        )
    if proxy_url:
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(http_url=proxy_url,
                                            https_url=proxy_url),
            http_client=_BoundedSession(),
        )
    return YouTubeTranscriptApi(http_client=_BoundedSession())


def _stamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fetch(
    video_id: str,
    proxy_url: str | None = None,
    languages: tuple[str, ...] = ("en", "en-US", "en-GB"),
    attempts: int = 3,
) -> Transcript:
    """Fetch one transcript as timestamped text.

    Timestamps are kept inline so Claude can cite where in the video a
    call was made, which is what makes the digest skimmable.
    """
    api = _client(proxy_url)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            fetched = api.fetch(video_id, languages=languages)
            lines = [
                f"[{_stamp(snippet['start'])}] {snippet['text'].strip()}"
                for snippet in fetched.to_raw_data()
                if snippet.get("text", "").strip()
            ]
            text = "\n".join(lines)
            return Transcript(
                video_id=video_id,
                text=text,
                word_count=len(text.split()),
            )
        except NOT_YET as exc:
            return Transcript(
                video_id=video_id,
                text="",
                word_count=0,
                not_ready=True,
                error=f"no captions yet ({type(exc).__name__})",
            )
        except PERMANENT as exc:
            return Transcript(
                video_id=video_id,
                text="",
                word_count=0,
                error=f"video unavailable ({type(exc).__name__})",
            )
        except THROTTLED as exc:
            last_error = exc
            if attempt < attempts:
                wait = 2 ** attempt * 3  # 6s, 12s
                print(f"    throttled on {video_id}, waiting {wait}s")
                time.sleep(wait)
                continue
            return Transcript(
                video_id=video_id,
                text="",
                word_count=0,
                blocked=True,
                error="YouTube blocked the request from this IP",
            )
        except Exception as exc:  # noqa: BLE001 - last resort, keep the run alive
            last_error = exc
            if attempt < attempts:
                time.sleep(3)
                continue

    return Transcript(
        video_id=video_id,
        text="",
        word_count=0,
        error=f"{type(last_error).__name__}: {last_error}",
    )
