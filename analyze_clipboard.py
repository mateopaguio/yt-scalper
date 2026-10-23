#!/usr/bin/env python3
"""Analyze a transcript you paste yourself - no YouTube fetching, no throttling.

Copy a transcript (YouTube's own "Show transcript" panel works fine), then:

    bash ~/Downloads/yt-scalper/ANALYZE.sh

Reads the clipboard, runs the same extraction the automated pipeline uses,
prints the breakdown, and files it so it shows up in your digest page and
ticker history like anything else.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from anthropic import Anthropic

from scalper import analyze, config as cfgmod, render, store

URL_RE = re.compile(r"^https?://\S+$", re.I)


def clipboard() -> str:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True,
                              timeout=15).stdout
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read the clipboard: {exc}")
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="", help="video title (optional)")
    ap.add_argument("--channel", default="Pasted", help="channel name (optional)")
    ap.add_argument("--file", help="read from a file instead of the clipboard")
    args = ap.parse_args()

    cfg = cfgmod.load()
    if not cfg.anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY not set (run via ANALYZE.sh).")
        return 2

    text = open(args.file).read() if args.file else clipboard()
    text = (text or "").strip()

    if not text:
        print("Clipboard is empty. Copy the transcript text first.")
        return 1
    if URL_RE.match(text) or (len(text) < 200 and "youtube.com" in text):
        print("That's a link, not a transcript.")
        print()
        print("On the video page: click '...' under the title -> 'Show transcript',")
        print("then select the transcript panel, copy it, and run this again.")
        return 1

    words = len(text.split())
    if words < cfg.min_transcript_words:
        print(f"Only {words} words - too short to be worth analyzing "
              f"(minimum {cfg.min_transcript_words}).")
        return 1

    vid = "paste_" + hashlib.sha1(text[:2000].encode()).hexdigest()[:12]
    record = {
        "video_id": vid,
        "title": args.title or "(pasted transcript)",
        "channel": args.channel,
        "published": datetime.now(timezone.utc).isoformat(),
        "url": "",
        "tier": 2,
        "source_type": "video",
    }

    print(f"Analyzing {words:,} words...\n")
    client = Anthropic(api_key=cfg.anthropic_key)
    findings = analyze.analyze(client, record, text,
                               model=cfg.model, max_tokens=cfg.max_output_tokens)
    if not findings:
        print("Analysis failed - see the error above.")
        return 1

    usage = findings.pop("_usage", {})
    record["findings"] = findings

    # ---- readable output ----
    line = "=" * 64
    print(line)
    print(f"  {findings.get('overall_stance','?').upper()}   "
          f"hype {findings.get('hype_level','?')}/5   "
          f"{'PROMOTIONAL  ' if findings.get('promotional') else ''}"
          f"{'discloses position' if findings.get('discloses_position') else 'no disclosure'}")
    print(line)
    print()
    print(findings.get("summary", ""))
    print()

    tickers = findings.get("tickers") or []
    if not tickers:
        print("No specific tickers discussed.")
    for t in tickers:
        print("-" * 64)
        head = f"  {t.get('ticker','?')}  {t.get('company','')}".rstrip()
        print(head)
        print(f"  {t.get('stance','?')} | conviction {t.get('conviction','?')} "
              f"| evidence: {t.get('evidence','?')} | at {t.get('timestamp','?')}")
        print()
        print(f"  {t.get('thesis','')}")
        extras = [(k, t.get(k)) for k in ("catalyst","price_target","timeframe") if t.get(k)]
        if extras:
            print()
            for k, v in extras:
                print(f"    {k.replace('_',' ')}: {v}")
        print()

    flags = findings.get("red_flags") or []
    if flags:
        print("-" * 64)
        print("  RED FLAGS")
        for f in flags:
            print(f"    ! {f}")
        print()

    macro = findings.get("macro_points") or []
    if macro:
        print("-" * 64)
        print("  MACRO")
        for m in macro:
            print(f"    - {m}")
        print()

    cost = (usage.get("input_tokens",0)/1e6*0.10 + usage.get("output_tokens",0)/1e6*0.50)
    print(line)
    print(f"  {usage.get('input_tokens',0):,} in / {usage.get('output_tokens',0):,} out  (~${cost:.4f})")
    print(line)

    # ---- file it so it joins the digest + ticker history ----
    store.add_videos([record])
    window = store.recent_videos(days=cfg.window_days)
    agg = analyze.aggregate(window, cfg.watchlist, cfgmod.consensus_tiers(cfg))
    render.render({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "video_count": len(window), "videos": window, "aggregate": agg,
        "catalysts": store.recent_catalysts(days=cfg.window_days),
        "calendar": [], "orphan_catalysts": [], "ledger": {},
        "creator_stats": [], "under_the_radar": [], "tier4_candidates": [],
        "blocked_count": 0, "skipped_count": 0, "pending_count": 0,
    }, store.load_digests())
    print("\nAdded to your digest: open ~/Downloads/yt-scalper/docs/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
