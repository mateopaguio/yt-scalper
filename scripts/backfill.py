#!/usr/bin/env python3
"""One-time historical ingestion: discover the latest N videos per channel.

Discovery only. Videos are queued; normal runs fetch transcripts and
analyze them, so a 1,400-video backfill never becomes one huge job and
never re-downloads anything already stored.

    export YOUTUBE_API_KEY=...
    python scripts/backfill.py --limit 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import config as cfgmod, store, youtube_api


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = cfgmod.load()
    if not youtube_api.api_key():
        print("ERROR: YOUTUBE_API_KEY is not set.")
        print("Get a free key: console.cloud.google.com -> enable YouTube Data API v3")
        print(f"Quota for this backfill: ~{len(cfg.channels) * 2} of 10,000 daily units.")
        return 2

    print(f"Backfilling up to {args.limit} videos per channel...\n")
    discovered, stats = youtube_api.backfill(cfg, store, limit=args.limit)

    print(f"\nChannels queried:  {stats['channels']}")
    print(f"New videos found:  {stats['found']}")
    print(f"Already known:     {stats['already_known']}")
    for failure in stats.get("failed", []):
        print(f"  ! {failure}")

    if args.dry_run:
        print("\n[dry run] nothing queued")
        return 0

    for video in discovered:
        store.queue_pending(video, "queued by backfill")
    print(f"\nQueued {len(discovered)} video(s). Run `python run.py` to process them,")
    print("or let the hourly schedule work through the queue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
