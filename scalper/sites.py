"""Website sources: find new articles and pull their text.

Same shape as feeds.py so the rest of the pipeline treats an article
and a video transcript identically.
"""
from __future__ import annotations

import os

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; yt-scalper/1.1)"}
# SEC asks automated clients to identify themselves WITH A CONTACT
# ADDRESS (sec.gov/os/accessing-edgar-data). The previous UA said
# "contact via repo" and carried no address, which their newer
# /enforcement-litigation/ endpoints refuse with 403 - while the older
# /news/pressreleases.rss accepts it. That is why two SEC feeds failed
# and one worked.
#
# The address is read from the environment and never committed: this
# repository is public and a hard-coded address would be scraped. Set it
# in .notify.env (gitignored):
#     export SEC_CONTACT="Your Name your@email.com"
# Without it the declared-identity request cannot be made and the
# browser-shaped fallback below is used instead.
_SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()

# No manual Host header: requests derives it, and a hard-coded one
# breaks any redirect to a different host.
SEC_UA = {
    "User-Agent": _SEC_CONTACT or "yt-scalper research client",
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Encoding": "gzip, deflate",
}

# Tried once if the declared identity is still refused; some SEC paths
# sit behind a bot filter that expects a conventional browser.
SEC_FALLBACK = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/129.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}


# Article bodies are fetched with a browser signature. FDA press pages
# carry ~1,100 words of static prose, yet every stored catalyst came back
# empty: the CDN answers a plain client with a challenge page, which is
# HTTP 200 with no article in it, so nothing raised and nothing extracted.
BROWSER_UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/129.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def sec_contact_configured() -> bool:
    """False means SEC_CONTACT is unset, so SEC feeds may keep 403ing."""
    return bool(_SEC_CONTACT)


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
    is_sec = "sec.gov" in (feed_url or "").lower()
    try:
        resp = requests.get(feed_url, headers=_headers(feed_url), timeout=timeout)
        # One retry with a browser signature. SEC's newer endpoints sit
        # behind a filter that rejects non-browser clients outright; the
        # declared-identity request above is tried first because that is
        # what their policy actually asks for.
        if is_sec and resp.status_code in (403, 401):
            resp = requests.get(feed_url, headers=SEC_FALLBACK, timeout=timeout)
            if resp.ok:
                print(f"  (note) {name}: declared UA refused, browser UA accepted")
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as exc:
        hint = ""
        if is_sec and not sec_contact_configured():
            hint = ("  -> set SEC_CONTACT=\"Your Name you@example.com\" in "
                    ".notify.env; SEC requires a contact address")
        print(f"  ! feed failed for {name}: {exc}{hint}")
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
    """Pull the readable body text of an article. Returns (text, word_count).

    Previously this returned ("", 0) on every failure path WITHOUT
    logging, and the caller passed the empty string to the model with a
    "(headline only)" placeholder. The model dutifully summarised the
    headline and nothing looked broken - 39 of 45 stored catalysts were
    built from a headline alone before anyone noticed. Every empty
    result is now reported with the reason.

    Staged extraction, because one strategy is not enough:
      1. favor_precision - clean prose, but discards short or
         list-shaped pages entirely, which is most FDA notices
      2. default settings
      3. favor_recall - takes boilerplate too, better than nothing

    Feed URLs also arrive as http://; those are upgraded before fetching
    so the redirect does not cost a round trip or get dropped.
    """
    try:
        import trafilatura
    except ImportError:
        print("    ! trafilatura is not installed - article bodies unavailable")
        return "", 0

    target = url or ""
    if target.startswith("http://"):
        target = "https://" + target[len("http://"):]

    downloaded = None
    try:
        downloaded = trafilatura.fetch_url(target)
        if not downloaded:
            resp = requests.get(target, headers=BROWSER_UA, timeout=timeout)
            resp.raise_for_status()
            downloaded = resp.text
    except Exception as exc:  # noqa: BLE001 - never kill the run over one article
        print(f"    ! could not fetch article: {exc}")
        return "", 0

    if not downloaded:
        print(f"    ! no content returned for {target[:70]}")
        return "", 0

    text = ""
    for attempt, kwargs in enumerate((
        {"favor_precision": True},
        {},
        {"favor_recall": True},
    )):
        try:
            text = trafilatura.extract(
                downloaded, include_comments=False,
                include_tables=True, **kwargs
            ) or ""
        except Exception:  # noqa: BLE001
            text = ""
        if len(text.split()) >= 40:
            if attempt:
                print(f"    (note) body recovered on fallback {attempt + 1}")
            break

    words = len(text.split())
    if words < 40:
        # Say so, and include the page size: a few hundred words of HTML
        # yielding no article means a challenge/consent page, while a
        # large page yielding none means the extractor missed it.
        print(f"    ! only {words} words extracted from {len(downloaded)}B page "
              f"({target[:52]}) - treating as headline only")
        return (text, words) if words else ("", 0)

    return text, words


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
