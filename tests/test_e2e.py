"""End-to-end pipeline test.

Only three boundaries are stubbed, and only because this sandbox cannot
reach them: the YouTube RSS feed, the transcript endpoint, and the Claude
API. Everything else is the real code path — real config, real tiering,
real aggregation, real storage, real rendering.

Run with real credentials and network and the same path executes for real.
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-stub-for-test")

import run as runner
from scalper import feeds, sites, transcripts, catalysts as cat, store

NOW = datetime.now(timezone.utc)

# A real Tier 2 channel from the verified config.
DISCOVERED = feeds.Video(
    video_id="e2eTEST0001",
    title="Why I added to my semiconductor position this week",
    channel="Chip Stock Investor",
    published=NOW - timedelta(hours=2),
    url="https://www.youtube.com/watch?v=e2eTEST0001",
    tier=2,
    channel_id="UC3aD-gfmHV_MhMmcwyIu1wA",
)

TRANSCRIPT = "\n".join(
    [f"[{m}:00] Discussion of capital expenditure and gross margin trends."
     for m in range(1, 90)]
)

CLAUDE_RESPONSE = {
    "summary": "Walks through a semiconductor supplier's margin trend and "
               "capex guidance, and explains adding to an existing position.",
    "overall_stance": "bullish", "hype_level": 2,
    "discloses_position": True, "promotional": False,
    "tickers": [{
        "ticker": "AMAT", "company": "Applied Materials", "stance": "bullish",
        "thesis": "Argues order backlog supports margins even if wafer starts soften.",
        "catalyst": "Q4 earnings", "price_target": "$240",
        "timeframe": "2 quarters", "conviction": "high",
        "evidence": "data", "timestamp": "12:30",
    }],
    "macro_points": [], "red_flags": [],
}


class FakeMessages:
    def __init__(self): self.calls = 0
    def create(self, **kw):
        self.calls += 1
        assert kw["tool_choice"]["name"] in ("record_findings", "record_catalyst")
        payload = (CLAUDE_RESPONSE if kw["tool_choice"]["name"] == "record_findings"
                   else {"is_market_relevant": False, "event_type": "other",
                         "summary": "Routine.", "companies": [],
                         "direction": "unclear", "materiality": 1})
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", input=dict(payload))],
            usage=SimpleNamespace(input_tokens=11800, output_tokens=380))


class FakeAnthropic:
    def __init__(self, **kw): self.messages = FakeMessages()


def main():
    sys.argv = ["run.py", "--dry-run"]
    with patch.object(runner, "Anthropic", FakeAnthropic), \
         patch.object(feeds, "find_new_videos",
                      lambda cfg, seen: [] if DISCOVERED.video_id in seen else [DISCOVERED]), \
         patch.object(sites, "find_new_articles", lambda cfg, seen: []), \
         patch.object(cat, "find_new_catalysts", lambda cfg, seen: []), \
         patch.object(transcripts, "fetch",
                      lambda vid, proxy_url=None, **k: transcripts.Transcript(
                          vid, TRANSCRIPT, len(TRANSCRIPT.split()))):
        return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
