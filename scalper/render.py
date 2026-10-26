"""Render the digest as a static, phone-first HTML page for GitHub Pages."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DOCS_DIR

DIRECTION_STYLE = {
    "likely_positive": ("up", "+"),
    "likely_negative": ("down", "-"),
    "mixed": ("flat", "~"),
    "unclear": ("flat", "?"),
}

STANCE_STYLE = {
    "bullish": ("up", "Bullish"),
    "bearish": ("down", "Bearish"),
    "split": ("flat", "Split"),
    "neutral": ("flat", "Neutral"),
    "unclear": ("flat", "Unclear"),
}
EVIDENCE_LABEL = {
    "data": ("good", "cites data"),
    "reasoned": ("ok", "reasoned"),
    "speculation": ("weak", "speculation"),
    "none": ("bad", "no support"),
}

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#0d1117; --panel:#161b22; --panel-2:#1c232c; --line:#2a313c;
  --ink:#e6edf3; --muted:#8b949e; --dim:#6e7681;
  --up:#3fb950; --down:#f85149; --flat:#d29922; --accent:#58a6ff;
  --good:#3fb950; --ok:#58a6ff; --weak:#d29922; --bad:#f85149;
}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  padding:0 0 56px}
.wrap{max-width:720px;margin:0 auto;padding:0 16px}
header{border-bottom:1px solid var(--line);padding:20px 0 14px;margin-bottom:18px}
h1{margin:0;font-size:19px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
.stats{display:flex;gap:18px;margin-top:14px;flex-wrap:wrap}
.stat b{display:block;font-size:21px;font-variant-numeric:tabular-nums}
.stat span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
  margin:26px 0 10px;font-weight:600}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  margin-bottom:10px;overflow:hidden}
.card.flagged{border-color:#8a6d1f}
.row{display:flex;align-items:baseline;gap:9px;padding:13px 14px;flex-wrap:wrap}
.tkr{font-weight:700;font-size:17px;letter-spacing:.02em}
.co{color:var(--muted);font-size:13px;flex:1;min-width:120px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{font-size:11px;padding:2px 8px;border-radius:999px;font-weight:600;
  border:1px solid currentColor;white-space:nowrap}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--flat)}
.count{color:var(--dim);font-size:12px;font-variant-numeric:tabular-nums}
details{border-top:1px solid var(--line)}
summary{padding:9px 14px;cursor:pointer;color:var(--accent);font-size:13px;
  list-style:none;user-select:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ ";color:var(--dim)}
details[open] summary::before{content:"▾ "}
.take{padding:12px 14px;border-top:1px solid var(--line);background:var(--panel-2)}
.take:first-of-type{border-top:none}
.take .who{font-size:12px;color:var(--muted);margin-bottom:5px;
  display:flex;gap:7px;flex-wrap:wrap;align-items:center}
.take p{margin:0 0 8px}
.meta{font-size:12px;color:var(--dim);display:flex;gap:12px;flex-wrap:wrap}
.meta b{color:var(--muted);font-weight:500}
.ev{font-size:10px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.ev.good{color:var(--good)} .ev.ok{color:var(--ok)}
.ev.weak{color:var(--weak)} .ev.bad{color:var(--bad)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.vid{padding:12px 14px;border-bottom:1px solid var(--line)}
.vid:last-child{border-bottom:none}
.vid .t{font-weight:600;margin-bottom:3px;line-height:1.35}
.vid .c{font-size:12px;color:var(--muted);margin-bottom:6px}
.vid .s{font-size:13.5px;color:#c9d1d9}
.kind{font-size:10px;text-transform:uppercase;letter-spacing:.07em;
  padding:1px 6px;border-radius:4px;border:1px solid var(--line);
  color:var(--dim);font-weight:700;margin-right:6px;vertical-align:1px}
.flag{font-size:12px;color:var(--flat);margin-top:7px;padding-left:16px;
  position:relative}
.flag::before{content:"!";position:absolute;left:4px;font-weight:700}
.cat{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  margin-bottom:8px;padding:12px 14px}
.cat.hot{border-color:#6e3030}
.cat .ch{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;margin-bottom:5px}
.cat .et{font-size:10px;text-transform:uppercase;letter-spacing:.06em;
  font-weight:700;color:var(--dim);border:1px solid var(--line);
  padding:1px 6px;border-radius:4px}
.cat .mt{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}
.cat .sm{font-size:13.5px;color:#c9d1d9;margin-top:4px}
.cat .sr{font-size:12px;color:var(--muted);margin-top:6px}
.cal{display:flex;gap:10px;padding:10px 14px;background:var(--panel);
  border:1px solid var(--line);border-radius:8px;margin-bottom:7px;align-items:baseline}
.cal .d{font-size:12px;color:var(--accent);font-weight:700;white-space:nowrap;
  font-variant-numeric:tabular-nums}
.catlink{font-size:12px;margin-top:7px;padding-top:7px;border-top:1px solid var(--line)}
.catlink b{color:var(--muted);font-weight:600}
.empty{color:var(--muted);text-align:center;padding:40px 16px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px}
footer{margin-top:32px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--dim);font-size:12px;line-height:1.6}
.note{background:#1c1908;border:1px solid #4d3d0e;color:#d8c272;
  padding:11px 13px;border-radius:8px;font-size:13px;margin-bottom:16px}
@media(max-width:400px){.co{display:none}}
"""


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def _safe_url(url: str) -> str:
    """Only allow http(s) links through. Titles and URLs originate from
    YouTube, so nothing from a feed gets to become a javascript: href."""
    url = (url or "").strip()
    if url.lower().startswith(("http://", "https://")):
        return html.escape(url, quote=True)
    return "#"


def _ticker_card(entry: dict) -> str:
    stance_cls, stance_label = STANCE_STYLE.get(
        entry.get("consensus", "split"), ("flat", "Split")
    )
    channels = entry.get("channels", [])
    nchan = len(channels)
    corroborating = entry.get("corroborating_channels") or []
    speculative = entry.get("speculative_channels") or []
    # "Independent" must mean the channel did its own work. Counting raw
    # mentions labelled a name covered by three reprints of one wire
    # story as "3 independent" - the exact overstatement this is for.
    basis = entry.get("basis") or {}
    indep = basis.get("independent_channels")
    reprints = basis.get("reprint_channels") or []
    if indep is not None:
        if len(indep) > 1:
            chan_note = f"{len(indep)} did own work"
        elif indep:
            chan_note = _esc(indep[0])
        elif reprints:
            chan_note = "reprints only"
        else:
            chan_note = f"{len(speculative)} speculative only"
        if reprints:
            chan_note += f" &middot; {len(reprints)} reprint"
    elif len(corroborating) > 1:
        chan_note = f"{len(corroborating)} channels"
    elif corroborating:
        chan_note = _esc(corroborating[0])
    else:
        chan_note = f"{len(speculative)} speculative only"

    takes = []
    for item in entry.get("items", []):
        ev_cls, ev_label = EVIDENCE_LABEL.get(
            item.get("evidence", "none"), ("bad", "no support")
        )
        item_stance = STANCE_STYLE.get(item.get("stance", "neutral"), ("flat", "?"))

        meta_bits = []
        if item.get("catalyst"):
            meta_bits.append(f"<b>Catalyst:</b> {_esc(item['catalyst'])}")
        if item.get("price_target"):
            meta_bits.append(f"<b>Target:</b> {_esc(item['price_target'])}")
        if item.get("timeframe"):
            meta_bits.append(f"<b>Horizon:</b> {_esc(item['timeframe'])}")
        if item.get("conviction"):
            meta_bits.append(f"<b>Conviction:</b> {_esc(item['conviction'])}")

        ts = _esc(item.get("timestamp", ""))
        src_kind = "read" if item.get("source_type") == "article" else "watch"
        link = _safe_url(item.get("url", ""))
        ts_link = (
            f'<a href="{link}" target="_blank" rel="noopener">'
            f'{ts or src_kind}</a>'
        )

        takes.append(
            f'<div class="take">'
            f'<div class="who"><span class="{item_stance[0]}">●</span>'
            f"<span>{_esc(item.get('channel'))}</span>"
            f'<span class="et">T{item.get("tier", 2)}</span>'

            f'<span class="ev {ev_cls}">{ev_label}</span>'
            f"<span>· {ts_link}</span></div>"
            f"<p>{_esc(item.get('thesis'))}</p>"
            + (f'<div class="meta">{" ".join(meta_bits)}</div>' if meta_bits else "")
            + "</div>"
        )

    events = entry.get("catalyst_events") or []
    cat_html = ""
    if events:
        bits = []
        for ev in events[:3]:
            cls, sign = DIRECTION_STYLE.get(ev.get("direction", "unclear"), ("flat", "?"))
            bits.append(
                f'<span class="{cls}">{sign}</span> '
                f'{_esc(ev.get("event_type", "").replace("_", " "))} '
                f'(<a href="{_safe_url(ev.get("url", ""))}" target="_blank" '
                f'rel="noopener">{_esc(ev.get("source", ""))}</a>)'
            )
        cat_html = (
            '<div class="catlink"><b>Filed:</b> ' + " · ".join(bits) + "</div>"
        )

    star = "★ " if entry.get("on_watchlist") else ""
    return (
        f'<div class="card">'
        f'<div class="row"><span class="tkr">{star}{_esc(entry["ticker"])}</span>'
        f'<span class="pill {stance_cls}">{stance_label}</span>'
        + ('<span class="pill flat">unverified</span>'
           if entry.get("speculative_only") else "")
        + f'<span class="co">{_esc(entry.get("company"))}</span>'
        f'<span class="count">{chan_note}</span></div>'
        f"{cat_html}"
        f"<details><summary>{len(takes)} take{'s' if len(takes) != 1 else ''}</summary>"
        f'{"".join(takes)}</details></div>'
    )


def _video_card(video: dict) -> str:
    findings = video.get("findings") or {}
    flags = findings.get("red_flags") or []
    hype = findings.get("hype_level", 0)
    classes = "card flagged" if (flags or hype >= 4) else "card"

    kind = "article" if video.get("source_type") == "article" else "video"
    badges = []
    if hype:
        badges.append(f"hype {hype}/5")
    if findings.get("promotional"):
        badges.append("promotional")
    if findings.get("discloses_position"):
        badges.append("discloses position")

    flag_html = "".join(f'<div class="flag">{_esc(f)}</div>' for f in flags)

    return (
        f'<div class="{classes}"><div class="vid">'
        f'<div class="t"><span class="kind">{kind}</span>'
        f'<a href="{_safe_url(video["url"])}" target="_blank" '
        f'rel="noopener">{_esc(video["title"])}</a></div>'
        f'<div class="c">{_esc(video["channel"])}'
        + (f" · {' · '.join(_esc(b) for b in badges)}" if badges else "")
        + "</div>"
        f'<div class="s">{_esc(findings.get("summary", "No summary available."))}</div>'
        f"{flag_html}</div></div>"
    )


def _catalyst_card(event: dict) -> str:
    findings = event.get("findings") or {}
    if not findings.get("is_market_relevant"):
        return ""

    materiality = findings.get("materiality", 1)
    cls = "cat hot" if materiality >= 4 else "cat"
    dir_cls, sign = DIRECTION_STYLE.get(findings.get("direction", "unclear"), ("flat", "?"))

    names = []
    for company in findings.get("companies") or []:
        ticker = (company.get("ticker") or "").upper()
        names.append(ticker or company.get("company", ""))
    who = ", ".join(n for n in names if n) or "macro"

    return (
        f'<div class="{cls}"><div class="ch">'
        f'<span class="{dir_cls}">{sign}</span>'
        f'<span class="tkr">{_esc(who)}</span>'
        f'<span class="et">{_esc(findings.get("event_type", "").replace("_", " "))}</span>'
        f'<span class="mt">materiality {materiality}/5</span></div>'
        f'<div class="sm">{_esc(findings.get("summary", ""))}</div>'
        f'<div class="sr"><a href="{_safe_url(event.get("url", ""))}" target="_blank" '
        f'rel="noopener">{_esc(event.get("title", ""))}</a> — {_esc(event.get("source", ""))}</div>'
        "</div>"
    )


def _calendar_row(event: dict) -> str:
    findings = event.get("findings") or {}
    names = [
        (c.get("ticker") or "").upper() or c.get("company", "")
        for c in (findings.get("companies") or [])
    ]
    who = ", ".join(n for n in names if n) or "macro"
    return (
        f'<div class="cal"><span class="d">{_esc(event.get("_when", ""))}</span>'
        f'<span>{_esc(who)} — {_esc(findings.get("summary", ""))}</span></div>'
    )


def _signals_html(sig: dict) -> str:
    if not sig:
        return ""
    out = []

    mom = [d for d in (sig.get("momentum") or []) if d["lift"] > 0][:10]
    if mom:
        rows = []
        for d in mom:
            tag = '<span class="pill up">new</span>' if d["is_new"] else ""
            rows.append(
                '<div class="cal"><span class="d">%s</span>'
                '<span>%dx in 24h (was %.1f/day) &middot; %d channel(s) &middot; '
                'quality %.1f</span>%s</div>'
                % (_esc(d["ticker"]), d["recent_mentions"], d["prior_per_day"],
                   len(d["channels"]), d["quality"], tag))
        out.append("<h2>Heating up</h2>" + "".join(rows))

    nl = sig.get("news_linked") or []
    if nl:
        rows = []
        for d in nl[:8]:
            ev = d["events"][0] if d["events"] else {}
            rows.append(
                '<div class="cat"><div class="ch"><span class="tkr">%s</span>'
                '<span class="et">%s</span><span class="mt">materiality %s/5</span></div>'
                '<div class="sm">%s</div></div>'
                % (_esc(d["ticker"]),
                   _esc(str(ev.get("event_type","")).replace("_"," ")),
                   _esc(d["max_materiality"]), _esc(ev.get("summary",""))))
        out.append("<h2>Discussed on video AND filed as news</h2>" + "".join(rows))

    emg = sig.get("emerging") or []
    if emg:
        rows = []
        for d in emg[:8]:
            rows.append(
                '<div class="cal"><span class="d">%s</span>'
                '<span>%s &mdash; first seen via %s &middot; quality %.1f</span></div>'
                % (_esc(d["ticker"]), _esc(d.get("company","")),
                   _esc(d.get("channel","")), d["quality"]))
        out.append("<h2>Newly appearing, with support</h2>" + "".join(rows))

    sec = [d for d in (sig.get("sectors") or []) if d["quality"] > 0][:8]
    if sec:
        rows = []
        for d in sec:
            cls = {"bullish":"up","bearish":"down"}.get(d["tilt"], "flat")
            rows.append(
                '<div class="cal"><span class="d %s">%s</span>'
                '<span>%d mentions &middot; %d names &middot; %d channel(s) &middot; %s</span></div>'
                % (cls, _esc(d["sector"]), d["mentions"], len(d["tickers"]),
                   d["channels"], _esc(d["tilt"])))
        out.append("<h2>Where attention is concentrating</h2>" + "".join(rows))

    return "".join(out)



def _score_bar(entry: dict) -> str:
    """Show WHY a ticker ranks where it does, component by component."""
    sc = entry.get("score") or {}
    comps = sc.get("components") or {}
    parts = []
    for key, c in sorted(comps.items(), key=lambda kv: -abs(kv[1]["contribution"])):
        v = c["contribution"]
        if not v:
            continue
        cls = "good" if v > 0 else "bad"
        label = key.replace("_", " ")
        parts.append(f'<span class="pill {cls}">{_esc(label)} {v:+.2f}</span>')
    return " ".join(parts)


def _trust_html(digest: dict) -> str:
    """The parts that say how much the corroboration is worth."""
    agg = digest.get("aggregate") or {}
    out = []

    # A dead price feed must look like a dead price feed, not like
    # "no results yet". Those are different facts and only one is fixable.
    health = digest.get("price_health") or {}
    if health and not health.get("price_feed_healthy") and health.get("total_signals"):
        out.append(
            '<div class="note"><b>Price feed unreachable.</b> '
            f'{health.get("fetch_failed", 0)} of {health["total_signals"]} '
            "recorded signals could not be priced, so none have a measured "
            "outcome. Performance cannot be evaluated until this is fixed "
            "&mdash; this is a broken data source, not an absence of "
            "results.</div>"
        )
    elif health.get("resolved_by_horizon"):
        by = health["resolved_by_horizon"]
        out.append(
            '<div class="note">Signals recorded: '
            f'{health.get("total_signals", 0)}. With a measured 7-day '
            f'return: {by.get("7", 0)}. Statistical conclusions need a few '
            "hundred; until then this is a record, not evidence.</div>"
        )

    vm = digest.get("verification_meta") or {}
    if vm.get("limitation"):
        out.append(
            '<div class="note"><b>Catalyst verification unavailable.</b> '
            + _esc(vm["limitation"]) + "</div>"
        )
    elif vm.get("by_status"):
        bs = vm["by_status"]
        out.append(
            '<div class="note">Catalyst claims checked against filings: '
            f'{bs.get("confirmed", 0)} confirmed, '
            f'{bs.get("unconfirmed", 0)} not found, '
            f'{bs.get("outside_coverage", 0)} of a kind these feeds cannot see '
            "(no earnings source is configured, so absence there proves "
            "nothing).</div>"
        )

    ranked = agg.get("ranked") or []
    if ranked:
        rows = []
        for e in ranked[:10]:
            sc = e.get("score") or {}
            # "unknown" alone reads as a failure; it means the story may
            # predate all our data, so we decline to claim it is new.
            fresh = {
                "unknown": "age unknown",
                "single_sighting": "seen once",
                "forward_catalyst": "catalyst ahead",
                "late_to_story": "after the move",
                "running": "running days",
            }.get((e.get("freshness") or {}).get("state", ""),
                  (e.get("freshness") or {}).get("state", ""))
            rows.append(
                f'<div class="card"><div class="row">'
                f'<b>{_esc(e["ticker"])}</b>'
                f'<span class="weak">{sc.get("total", 0):+.2f} '
                f'of {sc.get("max_possible", 0):.1f}</span>'
                f'<span class="weak">{sc.get("independent_channels", 0)} '
                f'did own work</span>'
                f'<span class="weak">{_esc(fresh.replace("_", " "))}</span>'
                f'</div><div class="row">{_score_bar(e)}</div></div>'
            )
        out.append(
            "<h2>Research priority</h2>"
            '<div class="note">Order to look at these in, not a trade signal. '
            "Price, volume, liquidity and options data are not wired in, so "
            "they are excluded from the score rather than assumed neutral. "
            "No component has been validated against outcomes yet.</div>"
            + "".join(rows)
        )

    echo = agg.get("echo_chamber") or []
    if echo:
        rows = []
        for e in echo:
            b = e.get("basis") or {}
            rows.append(
                f'<div class="card"><div class="row"><b>{_esc(e["ticker"])}</b>'
                f'<span class="weak">{len(e.get("corroborating_channels") or [])}'
                f' channels covered it</span>'
                f'<span class="pill bad">{len(b.get("independent_channels") or [])}'
                f' did own work</span></div>'
                f'<div class="weak">reprinting: '
                f'{_esc(", ".join(b.get("reprint_channels") or []))}</div></div>'
            )
        out.append(
            "<h2>Looks corroborated, isn&rsquo;t</h2>"
            '<div class="note">Several channels carried these, but not two of '
            "them did their own work &mdash; the rest are reprints of one "
            "report. Counting them as agreement overstates it.</div>"
            + "".join(rows)
        )

    shared = agg.get("shared_events") or []
    if shared:
        rows = []
        for ev in shared:
            wire = '<span class="pill bad">wire-driven</span>' if ev.get("wire_driven") else ""
            rows.append(
                f'<div class="card"><div class="row">'
                f'<b>{_esc(" + ".join(ev["tickers"]))}</b>{wire}'
                f'<span class="weak">{ev["mentions"]} mentions, '
                f'{len(ev["channels"])} channels</span></div>'
                f'<div class="weak">{_esc(ev.get("representative_thesis", "")[:220])}</div>'
                f'</div>'
            )
        out.append(
            "<h2>One story, several tickers</h2>"
            '<div class="note">These moved together on the same event. Four '
            "names reacting to one report is one event, not four signals.</div>"
            + "".join(rows)
        )

    orig = agg.get("channel_originality") or []
    if orig:
        rows = []
        for a in orig:
            if not a.get("total"):
                continue
            pct = int(round(a["derivative_share"] * 100))
            cls = "bad" if pct >= 25 else ("flat" if pct else "good")
            rows.append(
                f'<div class="row"><span class="pill {cls}">{pct}%</span>'
                f'<b>{_esc(a["channel"])}</b>'
                f'<span class="weak">{a["total"]} claims</span></div>'
            )
        out.append(
            "<h2>How much each channel reprints</h2>"
            '<div class="note">Share of claims that restate someone '
            "else&rsquo;s reporting. A news desk doing this is doing its job "
            "&mdash; it matters only because reprints must not count as "
            "independent confirmation.</div>"
            '<div class="card">' + "".join(rows) + "</div>"
        )

    return "".join(out)


def render(digest: dict, history: list[dict], out_dir: Path | None = None) -> Path:
    out_dir = out_dir or DOCS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    agg = digest.get("aggregate", {})
    tickers = agg.get("tickers", [])
    videos = digest.get("videos", [])
    generated = digest.get("generated_at", datetime.now(timezone.utc).isoformat())

    try:
        stamp = datetime.fromisoformat(generated).strftime("%b %d, %Y at %H:%M UTC")
    except ValueError:
        stamp = generated

    multi = agg.get("multi_channel", [])
    note = ""
    if digest.get("blocked_count"):
        note = (
            f'<div class="note">{digest["blocked_count"]} transcript(s) could not '
            "be fetched — YouTube throttled the request. See the README on setting "
            "a proxy if this keeps happening.</div>"
        )

    radar = digest.get("under_the_radar") or []
    radar_html = (
        "<h2>Under the radar — supported, barely covered</h2>"
        + "".join(_ticker_card(t) for t in radar)
        if radar else ""
    )

    if tickers:
        ticker_html = (
            (
                f"<h2>Mentioned by more than one channel</h2>"
                + "".join(_ticker_card(t) for t in multi)
                if multi
                else ""
            )
            + "<h2>All tickers</h2>"
            + "".join(
                _ticker_card(t)
                for t in tickers
                if t["ticker"] not in {m["ticker"] for m in multi}
            )
        )
    else:
        ticker_html = (
            '<div class="empty">No tickers found in this run.<br>'
            "Either there were no new videos, or none discussed specific names.</div>"
        )

    signals_html = _signals_html(digest.get("signals") or {})
    trust_html = _trust_html(digest)
    catalyst_events = digest.get("catalysts") or []
    cat_cards = [c for c in (_catalyst_card(e) for e in catalyst_events) if c]
    catalyst_html = (
        "<h2>Catalysts filed</h2>" + "".join(cat_cards) if cat_cards else ""
    )

    calendar = digest.get("calendar") or []
    calendar_html = (
        "<h2>Upcoming dated events</h2>"
        + "".join(_calendar_row(e) for e in calendar)
        if calendar else ""
    )

    video_html = (
        "<h2>Video breakdowns</h2>" + "".join(_video_card(v) for v in videos)
        if videos
        else ""
    )

    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Scalper Digest</title>
<style>{CSS}</style>
</head><body><div class="wrap">
<header>
  <h1>Scalper Digest</h1>
  <div class="sub">Updated {_esc(stamp)}</div>
  <div class="stats">
    <div class="stat"><b>{len(videos)}</b><span>videos</span></div>
    <div class="stat"><b>{len(tickers)}</b><span>tickers</span></div>
    <div class="stat"><b>{len(multi)}</b><span>corroborated</span></div>
    <div class="stat"><b>{len(cat_cards)}</b><span>catalysts</span></div>
  </div>
</header>
{note}
{trust_html}
{signals_html}
{calendar_html}
{catalyst_html}
{radar_html}
{ticker_html}
{video_html}
<footer>
  This page reports what these videos claimed. It is not investment advice,
  not a recommendation, and not a judgment on whether any call is correct.
  Evidence labels describe how well a claim was supported on the video, not
  whether it is true. Catalyst entries are summaries of official filings and
  announcements; the direction shown reflects only the plain nature of the
  event, not a price forecast. Verify anything before acting on it.
</footer>
</div></body></html>"""

    out_path = out_dir / "index.html"
    out_path.write_text(page, encoding="utf-8")

    (out_dir / "digest.json").write_text(
        json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_path
