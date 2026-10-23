#!/usr/bin/env python3
"""Score stored recommendations against subsequent market performance.

Needs network access to the price provider. Horizons that have not
elapsed are recorded as null; nothing is estimated or back-filled.

    python scripts/score_performance.py --days 365
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import config as cfgmod, prices, store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365,
                    help="how far back to pull recommendations")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    cfgmod.load()
    videos = store.recent_videos(days=args.days)
    recs = prices.collect_recommendations(videos)[: args.limit]

    if not recs:
        print("No bullish/bearish recommendations stored yet.")
        return 0

    print(f"Scoring {len(recs)} recommendation(s) across "
          f"{len({r['ticker'] for r in recs})} ticker(s)...\n")

    cache: dict = {}
    scored, failed = [], 0

    for rec in recs:
        result = prices.score_recommendation(
            rec["ticker"], rec["recommended_at"], cache=cache
        )
        result.update({
            "creator": rec["creator"], "tier": rec["tier"],
            "stance": rec["stance"], "evidence": rec["evidence"],
            "video_url": rec["video_url"],
        })
        if result.get("error"):
            failed += 1
            print(f"  ! {rec['ticker']} ({rec['creator']}): {result['error']}")
        else:
            elapsed = [h for h, v in result["returns"].items() if v]
            print(f"  {rec['ticker']:6} {rec['creator'][:22]:22} "
                  f"entry {result['entry_date']} horizons elapsed: {elapsed or 'none yet'}")
        scored.append(result)

    store.save_performance(scored)
    usable = [s for s in scored if not s.get("error")]
    print(f"\nScored {len(usable)}, failed {failed}. Saved to data/performance.json")

    if usable:
        print("\nPer-creator summary (only elapsed horizons):")
        for row in prices.creator_performance(usable):
            thin = " [thin sample]" if row["statistically_thin"] else ""
            print(f"  {row['creator'][:28]:28} calls={row['calls_scored']}{thin}")
            for horizon, data in sorted(row["horizons"].items(), key=lambda kv: int(kv[0])):
                if data:
                    print(f"      {horizon:>3}d n={data['n']} "
                          f"mean={data['mean_return_pct']:+.2f}% "
                          f"hit={data['hit_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
