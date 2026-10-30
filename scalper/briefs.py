"""Per-ticker briefs: everything every channel said about one name.

The digest answers "what came out?". A brief answers "what does my
watchlist look like right now, and why does anyone hold that view?" -
bull case and bear case side by side, each line attributed and
evidence-graded, so disagreement is visible instead of averaged away.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import clock

EV_RANK = {"data": 3, "reasoned": 2, "speculation": 1, "none": 0}
EV_LABEL = {"data": "cites data", "reasoned": "reasoned",
            "speculation": "speculation", "none": "unsupported"}


def build(entry: dict, events: list[dict] | None = None) -> dict[str, Any]:
    """Split one aggregated ticker into bull / bear / neutral cases."""
    bull, bear, neutral = [], [], []
    for item in entry.get("items", []):
        row = {
            "channel": item.get("channel", ""),
            "tier": item.get("tier", 2),
            "thesis": item.get("thesis", ""),
            "evidence": item.get("evidence", "none"),
            "conviction": item.get("conviction", ""),
            "catalyst": item.get("catalyst", ""),
            "price_target": item.get("price_target", ""),
            "timeframe": item.get("timeframe", ""),
            "url": item.get("url", ""),
            "timestamp": item.get("timestamp", ""),
            "video_title": item.get("video_title", ""),
        }
        {"bullish": bull, "bearish": bear}.get(item.get("stance"), neutral).append(row)

    for group in (bull, bear, neutral):
        group.sort(key=lambda r: EV_RANK.get(r["evidence"], 0), reverse=True)

    counts = entry.get("evidence_counts") or {}
    supported = counts.get("data", 0) + counts.get("reasoned", 0)
    total = sum(counts.values()) or 1

    return {
        "ticker": entry["ticker"],
        "company": entry.get("company", ""),
        "consensus": entry.get("consensus", "split"),
        "bull": bull, "bear": bear, "neutral": neutral,
        "channels": entry.get("corroborating_channels", []),
        "speculative_channels": entry.get("speculative_channels", []),
        "evidence_counts": counts,
        "support_ratio": round(supported / total, 2),
        "catalyst_events": entry.get("catalyst_events") or [],
        "strongest": (bull + bear + neutral)[0] if (bull or bear or neutral) else None,
    }


def as_text(brief: dict, max_lines: int = 3) -> str:
    """Compact plain-text version, for a push notification."""
    t = brief["ticker"]
    nb, nr = len(brief["bull"]), len(brief["bear"])
    head = f"{t} — {nb} bull / {nr} bear across {len(brief['channels'])} channel(s)"
    if brief["support_ratio"] < 0.5:
        head += "  [thin evidence]"
    lines = [head]
    for row in (brief["bull"] + brief["bear"])[:max_lines]:
        side = "+" if row in brief["bull"] else "-"
        lines.append(f"{side} {row['channel']} ({EV_LABEL.get(row['evidence'])}): "
                     f"{row['thesis'][:150]}")
    for ev in brief["catalyst_events"][:1]:
        lines.append(f"! filed: {ev.get('event_type','').replace('_',' ')} "
                     f"({ev.get('source','')})")
    return "\n".join(lines)


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _case_html(rows: list, label: str, cls: str) -> str:
    if not rows:
        return ""
    out = ['<h3 class="%s">%s <span class="n">%d</span></h3>' % (cls, label, len(rows))]
    for r in rows:
        meta = " \u00b7 ".join(
            "%s: %s" % (k.replace("_", " "), _esc(r[k]))
            for k in ("catalyst", "price_target", "timeframe") if r.get(k)
        )
        link = r.get("url") or ""
        if str(link).startswith(("http://", "https://")):
            src = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (
                _esc(link), _esc(r["channel"]))
        else:
            src = _esc(r["channel"])

        ts = ""
        if r.get("timestamp"):
            ts = '<span class="ts">@%s</span>' % _esc(r["timestamp"])
        cv = ""
        if r.get("conviction"):
            cv = '<span class="cv">conviction %s</span>' % _esc(r["conviction"])

        block = (
            '<div class="case"><div class="who">' + src
            + '<span class="ev %s">%s</span>' % (r["evidence"], EV_LABEL.get(r["evidence"], ""))
            + cv + ts + '</div><p>' + _esc(r["thesis"]) + '</p>'
        )
        if meta:
            block += '<div class="meta">%s</div>' % meta
        block += "</div>"
        out.append(block)
    return "".join(out)


PAGE_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{--bg:#0d1117;--panel:#161b22;--line:#2a313c;--ink:#e6edf3;
 --muted:#8b949e;--dim:#6e7681;--up:#3fb950;--down:#f85149;--flat:#d29922;
 --accent:#58a6ff;--good:#3fb950;--ok:#58a6ff;--weak:#d29922;--bad:#f85149}
body{margin:0;background:var(--bg);color:var(--ink);padding:0 0 56px;
 font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:0 16px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{border-bottom:1px solid var(--line);padding:20px 0 14px;margin-bottom:18px}
h1{margin:0;font-size:30px;letter-spacing:.01em}
.co{color:var(--muted);font-size:14px;margin-top:2px}
.tags{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.tag{font-size:11px;padding:3px 9px;border-radius:999px;border:1px solid currentColor;font-weight:600}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--flat)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:26px 0 10px}
h3{font-size:14px;margin:22px 0 10px;display:flex;align-items:center;gap:8px}
h3 .n{font-size:11px;color:var(--dim);font-weight:400}
h3.bull{color:var(--up)}h3.bear{color:var(--down)}h3.neutral{color:var(--flat)}
.case{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 padding:12px 14px;margin-bottom:9px}
.case .who{display:flex;gap:9px;align-items:baseline;flex-wrap:wrap;
 font-size:12px;color:var(--muted);margin-bottom:6px}
.ev{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.ev.data{color:var(--good)}.ev.reasoned{color:var(--ok)}
.ev.speculation{color:var(--weak)}.ev.none{color:var(--bad)}
.cv,.ts{color:var(--dim);font-size:11px}
.case p{margin:0}
.meta{font-size:12px;color:var(--dim);margin-top:7px}
.warn{background:#1c1908;border:1px solid #4d3d0e;color:#d8c272;
 padding:11px 13px;border-radius:8px;font-size:13px;margin-bottom:16px}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);
 color:var(--dim);font-size:12px;line-height:1.6}
.back{display:inline-block;margin:14px 0;font-size:13px}
"""


def render_page(brief: dict, out_dir: Path, site_url: str = "") -> Path:
    """One page per ticker: bull case, bear case, filed catalysts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    t = brief["ticker"]

    cls = {"bullish": "up", "bearish": "down"}.get(brief["consensus"], "flat")
    tags = ['<span class="tag %s">%s</span>' % (cls, brief["consensus"])]
    tags.append('<span class="tag flat">%d channel(s)</span>' % len(brief["channels"]))
    if brief["speculative_channels"]:
        tags.append('<span class="tag flat">%d speculative</span>'
                    % len(brief["speculative_channels"]))

    warn = ""
    if brief["support_ratio"] < 0.5:
        warn = ('<div class="warn">Most of what was said about %s rests on '
                'speculation or nothing stated. Treat this as chatter, not research.</div>' % t)

    cat = ""
    if brief["catalyst_events"]:
        rows = []
        for ev in brief["catalyst_events"]:
            rows.append('<div class="case"><div class="who">%s<span class="ev data">%s</span>'
                        '</div><p>%s</p></div>' % (
                            _esc(ev.get("source", "")),
                            _esc(str(ev.get("event_type", "")).replace("_", " ")),
                            _esc(ev.get("summary", ""))))
        cat = "<h2>Filed events</h2>" + "".join(rows)

    body = (_case_html(brief["bull"], "Bull case", "bull")
            + _case_html(brief["bear"], "Bear case", "bear")
            + _case_html(brief["neutral"], "Neutral / unclear", "neutral"))
    if not body:
        body = "<p>No theses recorded yet.</p>"

    stamp = clock.stamp(datetime.now(timezone.utc), "%b %d, %Y %-I:%M %p")
    home = (site_url or "../") + ("" if site_url else "index.html")
    page = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0d1117"><title>%s</title>
<style>%s</style></head><body><div class="wrap">
<a class="back" href="%s">&larr; all tickers</a>
<header><h1>%s</h1><div class="co">%s</div><div class="tags">%s</div></header>
%s%s%s
<footer>Every line is what a source said, attributed and graded by how well that
source supported it &mdash; not a recommendation and not a verdict on whether any
claim is correct. Updated %s.</footer>
</div></body></html>""" % (
        _esc(t), PAGE_CSS, _esc(home), _esc(t), _esc(brief["company"]),
        "".join(tags), warn, body, cat, stamp)

    path = out_dir / ("%s.html" % t)
    path.write_text(page, encoding="utf-8")
    return path
