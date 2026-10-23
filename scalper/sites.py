"""Website sources: find new articles and pull their text.

Same shape as feeds.py so the rest of the pipeline treats an article
and a video transcript identically.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; yt-scalper/1.1)"}
# SEC blocks generic agents: it requires a declared identity on automated
# requests, and answers 403 without one.
SEC_UA = {
    "User-Agent": "yt-scalper personal research (contact via repo)",
    "Accept-Encoding": "gzip, deflate",
    "Host": "www.sec.gov",
}


def _headers(url: str) -> dict:
    return SEC_UA if "sec.gov" in url else UA

FEED_HINTS = (
    "/feed", "/feed/", "/rss", "/rss.xml", "/index.xml",
    "/atom.xml", "/feed.xml", "/feeds/posts/default",
)


@dataclass
class Article:
    article_id: str
    title: str
    source: str
    published: datetime
    url: str

    def as_dict(self) -> dict:
        return {
            "video_id": self.article_id,   # shared key across source types
            "title": self.title,
            "channel": self.source,
            "published": self.published.isoformat(),
            "url": self.url,
            "source_type": "article",
        }


def discover_feed(site_url: str, timeout: int = 20) -> str | None:
    """Find a site's RSS/Atom feed.

    Looks for a declared <link rel="alternate"> first, then tries the
    handful of conventional paths.
    """
    try:
        resp = requests.get(site_url, headers=UA, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! could not load {site_url}: {exc}")
        return None

    match = re.search(
        r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>',
        resp.text,
        re.I,
    )
    if match:
        href = re.search(r'href=["\']([^"\']+)["\']', match.group(0), re.I)
        if href:
            return urljoin(site_url, href.group(1))

    for path in FEED_HINTS:
        candidate = urljoin(site_url, path)
        try:
            probe = requests.get(candidate, headers=UA, timeout=10)
            if probe.ok and probe.content.lstrip()[:200].startswith(b"<"):
                body = probe.content[:400].lower()
                if b"<rss" in body or b"<feed" in body:
                    return candidate
        except requests.RequestException:
            continue
    return None


def _parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def fetch_feed(name: str, feed_url: str, timeout: int = 20) -> list[Article]:
    """Read a feed and return its entries. Handles both RSS and Atom."""
    try:
        resp = requests.get(feed_url, headers=_headers(feed_url), timeout=timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"  ! feed failed for {name}: {exc}")
        return []

    articles: list[Article] = []
    atom = "{http://www.w3.org/2005/Atom}"

    # RSS 2.0
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        published = _parse_date(
            item.findtext("pubDate")
            or item.findtext("{http://purl.org/dc/elements/1.1/}date")
            or ""
        )
        if link and published:
            articles.append(
                Article(
                    article_id=_slug(link), title=title, source=name,
                    published=published, url=link,
                )
            )

    # Atom
    for entry in root.iter(f"{atom}entry"):
        link_el = entry.find(f"{atom}link[@rel='alternate']") or entry.find(f"{atom}link")
        link = (link_el.get("href") if link_el is not None else "") or ""
        title = (entry.findtext(f"{atom}title") or "").strip()
        published = _parse_date(
            entry.findtext(f"{atom}published") or entry.findtext(f"{atom}updated") or ""
        )
        if link and published:
            articles.append(
                Article(
                    article_id=_slug(link), title=title, source=name,
                    published=published, url=link,
                )
            )

    return articles


def _slug(url: str) -> str:
    """Stable ID for an article URL, namespaced so it can't collide
    with an 11-character YouTube video ID."""
    import hashlib

    return "art_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:14]


def fetch_article(url: str, timeout: int = 25) -> tuple[str, int]:
    """Pull the readable body text of an article. Returns (text, word_count)."""
    try:
        import trafilatura
    except ImportError:
        return "", 0

    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            resp = requests.get(url, headers=UA, timeout=timeout)
            resp.raise_for_status()
            downloaded = resp.text

        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        ) or ""
    except Exception as exc:  # noqa: BLE001 - never kill the run over one article
        print(f"    ! article extraction failed: {exc}")
        return "", 0

    return text, len(text.split())


def find_new_articles(cfg, already_seen: set[str]) -> list[Article]:
    """Recent articles across all configured sites that we haven't processed."""
    sites = cfg.raw.get("sites") or []
    if not sites:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.lookback_hours)
    found: list[Article] = []

    for site in sites:
        name = site.get("name") or urlparse(site.get("url", "")).netloc or "site"
        feed_url = site.get("rss")
        if not feed_url:
            print(f"  - {name}: no rss configured, skipping "
                  "(run resolve_sources.py to find it)")
            continue

        entries = fetch_feed(name, feed_url)
        fresh = [
            a for a in entries
            if a.published >= cutoff and a.article_id not in already_seen
        ]
        print(f"  - {name}: {len(fresh)} new of {len(entries)} recent")
        found.extend(fresh)

    found.sort(key=lambda a: a.published, reverse=True)
    return found
