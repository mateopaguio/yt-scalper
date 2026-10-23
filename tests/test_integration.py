"""End-to-end run with the network and Claude mocked out."""
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-not-real"

import run as runner
from scalper import feeds, sites, transcripts, store

NOW = datetime.now(timezone.utc)

FAKE_VIDEOS = [
    feeds.Video("vidAAA11111", "Three AI names into earnings", "Value Lens",
                NOW, "https://www.youtube.com/watch?v=vidAAA11111"),
    feeds.Video("vidBBB22222", "CAPTIONS NOT READY YET", "Momentum Daily",
                NOW, "https://www.youtube.com/watch?v=vidBBB22222"),
]
FAKE_ARTICLES = [
    sites.Article(sites._slug("https://news.example.com/semis"),
                  "Semis lead the tape", "Market Wire", NOW,
                  "https://news.example.com/semis"),
]

TRANSCRIPT = " ".join(
    ["[0:05] Talking about Nvidia and its data center backlog today."] * 60
)
ARTICLE = " ".join(["Analysts at a major bank raised their AMD target."] * 60)


def fake_transcript_fetch(video_id, proxy_url=None, **kw):
    if video_id == "vidBBB22222":
        return transcripts.Transcript(video_id, "", 0, not_ready=True,
                                      error="no captions yet (NoTranscriptFound)")
    return transcripts.Transcript(video_id, TRANSCRIPT, len(TRANSCRIPT.split()))


def fake_article_fetch(url, timeout=25):
    return ARTICLE, len(ARTICLE.split())


TOOL_RESULT = {
    "summary": "Covers semiconductor names ahead of earnings.",
    "overall_stance": "bullish", "hype_level": 2,
    "discloses_position": True, "promotional": False,
    "tickers": [{
        "ticker": "NVDA", "company": "Nvidia", "stance": "bullish",
        "thesis": "Argues the data-center backlog is already contracted.",
        "catalyst": "Q3 earnings", "price_target": "$210",
        "timeframe": "2 quarters", "conviction": "high",
        "evidence": "data", "timestamp": "4:12",
    }],
    "macro_points": [], "red_flags": [],
}


class FakeMessages:
    def create(self, **kwargs):
        assert kwargs["tool_choice"] == {"type": "tool", "name": "record_findings"}
        assert kwargs["model"] == "claude-haiku-5-5"
        block = SimpleNamespace(type="tool_use", input=dict(TOOL_RESULT))
        return SimpleNamespace(
            content=[block],
            usage=SimpleNamespace(input_tokens=11000, output_tokens=400),
        )


class FakeAnthropic:
    def __init__(self, **kw):
        self.messages = FakeMessages()


def run_once(label, videos, articles, argv):
    print(f"\n{'='*58}\n{label}\n{'='*58}")
    with patch.object(runner, "Anthropic", FakeAnthropic), \
         patch.object(feeds, "find_new_videos", lambda cfg, seen:
                      [v for v in videos if v.video_id not in seen]), \
         patch.object(sites, "find_new_articles", lambda cfg, seen:
                      [a for a in articles if a.article_id not in seen]), \
         patch.object(transcripts, "fetch", fake_transcript_fetch), \
         patch.object(sites, "fetch_article", fake_article_fetch):
        sys.argv = ["run.py"] + argv
        return runner.main()


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load(open("config.yml"))
    cfg["sites"] = [{"name": "Market Wire", "url": "https://news.example.com",
                     "rss": "https://news.example.com/feed"}]
    yaml.safe_dump(cfg, open("config.yml", "w"), sort_keys=False)

    rc1 = run_once("RUN 1 — first hour: 2 videos + 1 article",
                   FAKE_VIDEOS, FAKE_ARTICLES, ["--dry-run"])
    print(f"\nexit={rc1}")
    print("pending queue:", [i["video"]["video_id"] for i in store.load_pending()])
    print("stored:", [v["video_id"] for v in store.load_videos()])

    rc2 = run_once("RUN 2 — next hour: nothing new, captions still missing",
                   FAKE_VIDEOS, FAKE_ARTICLES, ["--dry-run"])
    print(f"\nexit={rc2}")
    items = store.load_pending()
    print("pending attempts:", [(i["video"]["video_id"], i["attempts"]) for i in items])
    sys.exit(rc1 or rc2)
