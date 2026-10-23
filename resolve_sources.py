#!/usr/bin/env python3
"""One-time setup: resolve YouTube channel IDs and website RSS feeds,
then write them back into config.yml.

    python resolve_sources.py
"""
from __future__ import annotations

import sys
from urllib.parse import urlparse

import yaml

from scalper import config as cfgmod
from scalper.feeds import resolve_channel_id
from scalper.sites import discover_feed, fetch_feed


def main() -> int:
    cfg = cfgmod.load()
    with open(cfgmod.CONFIG_PATH) as fh:
        raw = yaml.safe_load(fh) or {}

    changed = False
    failures: list[str] = []

    # --- YouTube channels ---
    channels = raw.get("channels") or []
    if channels:
        print("YouTube channels")
    for idx, channel in enumerate(cfg.channels):
        if channel.channel_id:
            print(f"  ok   {channel.name} -> {channel.channel_id}")
            continue
        print(f"  ...  {channel.name}")
        found = resolve_channel_id(channel)
        if found:
            raw["channels"][idx]["channel_id"] = found
            changed = True
            print(f"  ok   {channel.name} -> {found}")
        else:
            failures.append(f"channel {channel.name}")
            print(f"  FAIL {channel.name}")

    # --- Websites ---
    sites = raw.get("sites") or []
    if sites:
        print("\nWebsites")
    for idx, site in enumerate(sites):
        name = site.get("name") or urlparse(site.get("url", "")).netloc or f"site {idx}"
        if site.get("rss"):
            print(f"  ok   {name} -> {site['rss']}")
            continue
        url = site.get("url")
        if not url:
            failures.append(f"site {name} (no url)")
            print(f"  FAIL {name}: no url given")
            continue

        print(f"  ...  {name}")
        feed = discover_feed(url)
        if not feed:
            failures.append(f"site {name}")
            print(f"  FAIL {name}: no feed found at {url}")
            continue

        entries = fetch_feed(name, feed)
        if not entries:
            failures.append(f"site {name}")
            print(f"  FAIL {name}: feed {feed} returned no entries")
            continue

        raw["sites"][idx]["rss"] = feed
        changed = True
        print(f"  ok   {name} -> {feed} ({len(entries)} entries)")

    if changed:
        with open(cfgmod.CONFIG_PATH, "w") as fh:
            yaml.safe_dump(raw, fh, sort_keys=False, allow_unicode=True)
        print(f"\nSaved to {cfgmod.CONFIG_PATH}")

    if failures:
        print("\nCould not resolve: " + ", ".join(failures))
        print(
            "For a channel: open it in a browser and copy the UC... string into\n"
            "channel_id. For a site: find its feed (often linked in the footer as\n"
            "RSS) and paste the URL into the site's `rss` field."
        )
        return 1

    print("\nAll sources resolved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
