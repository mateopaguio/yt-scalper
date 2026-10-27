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
/* --- responsive guards -------------------------------------------------
   The reported symptom - text wrapping into one letter per line - has one
   usual cause: a flex or grid CHILD defaults to min-width:auto, so it
   refuses to shrink below its content, the sibling gets squeezed to a few
   pixels, and the browser stacks letters vertically. It is invisible in
   Chromium here but reproduces readily in WebKit, so the guard is applied
   rather than waiting to reproduce it.
   Nothing below hides content; it only lets boxes shrink and text break. */
.row > *, .card > *, .lead > *, nav.jump > *, .stats > * { min-width: 0; }
.row, .stats, nav.jump { min-width: 0; }
.card, .lead, .row, .note, .empty, section { overflow-wrap: anywhere; }
/* long unbroken strings: URLs, tickers like BRK.B, channel handles */
.weak, .sm, .take, .meta, .who, .thesis, summary { overflow-wrap: anywhere; }
a { word-break: break-word; }
/* never let a child force the page wider than the phone */
html, body { max-width: 100%; overflow-x: hidden; }
img, svg, table, pre { max-width: 100%; }
/* iPhone safe areas (Dynamic Island / home indicator) */
.wrap { padding-left: max(16px, env(safe-area-inset-left));
        padding-right: max(16px, env(safe-area-inset-right)); }

h3.sub2{font-size:13px;color:var(--ink);margin:20px 0 9px;
  font-weight:650}
h2.sec{font-size:12px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent);border-bottom:1px solid var(--line);
  padding-bottom:7px;margin:26px 0 13px}
section:first-of-type h2.sec{margin-top:8px}
/* --- terminal layout: decisions first, bulk collapsed --- */
nav.jump{position:sticky;top:0;z-index:20;background:rgba(13,17,23,.94);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
  margin:0 -16px 14px;padding:9px 16px;display:flex;gap:14px;
  overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
nav.jump::-webkit-scrollbar{display:none}
nav.jump a{color:var(--muted);text-decoration:none;font-size:12.5px;
  font-weight:600;white-space:nowrap;letter-spacing:.02em}
nav.jump a:hover{color:var(--ink)}
.lead{background:linear-gradient(180deg,#151b24,#11161d);
  border:1px solid #243041;border-radius:12px;padding:14px;margin-bottom:14px}
.lead h3{margin:0 0 3px;font-size:15px;letter-spacing:.01em}
.lead .why{color:var(--muted);font-size:13px;line-height:1.45}
.rank{display:inline-flex;align-items:center;justify-content:center;
  width:21px;height:21px;border-radius:6px;background:#1d2733;
  color:var(--muted);font-size:11.5px;font-weight:700;margin-right:8px}
details.fold{border:1px solid var(--line);border-radius:10px;
  margin-bottom:10px;background:var(--panel);overflow:hidden}
details.fold>summary{cursor:pointer;padding:12px 14px;font-weight:650;
  font-size:14px;list-style:none;display:flex;justify-content:space-between;
  align-items:center;gap:10px}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary::after{content:"+";color:var(--muted);font-weight:400;
  font-size:17px;line-height:1}
details.fold[open]>summary::after{content:"\2212"}
details.fold>summary:hover{background:#151a21}
details.fold .inner{padding:0 10px 10px}
.count{color:var(--dim);font-weight:400;font-size:12.5px}
@media (max-width:480px){
  .lead{padding:12px}
  nav.jump{gap:11px}
}

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
.pill{font-size:11.5px;padding:2px 8px;border-radius:999px;font-weight:600;
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
.ev{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.ev.good{color:var(--good)} .ev.ok{color:var(--ok)}
.ev.weak{color:var(--weak)} .ev.bad{color:var(--bad)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.vid{padding:12px 14px;border-bottom:1px solid var(--line)}
.vid:last-child{border-bottom:none}
.vid .t{font-weight:600;margin-bottom:3px;line-height:1.35}
.vid .c{font-size:12px;color:var(--muted);margin-bottom:6px}
.vid .s{font-size:13.5px;color:#c9d1d9}
.kind{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;
  padding:1px 6px;border-radius:4px;border:1px solid var(--line);
  color:var(--dim);font-weight:700;margin-right:6px;vertical-align:1px}
.flag{font-size:12px;color:var(--flat);margin-top:7px;padding-left:16px;
  position:relative}
.flag::before{content:"!";position:absolute;left:4px;font-weight:700}
.cat{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  margin-bottom:8px;padding:12px 14px}
.cat.hot{border-color:#6e3030}
.cat .ch{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;margin-bottom:5px}
.cat .et{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;
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



def _fold(title: str, body: str, count: str = "", open_by_default: bool = False) -> str:
    """Collapse bulk detail. The page had 13 flat sections and 372 cards -
    everything equally loud, which on a phone is an endless scroll. The
    spec asks the home screen to lead with opportunities, so detail
    stays available but folded rather than deleted."""
    if not body:
        return ""
    o = " open" if open_by_default else ""
    c = f'<span class="count">{_esc(count)}</span>' if count else ""
    return (f'<details class="fold"{o}><summary>{_esc(title)}{c}</summary>'
            f'<div class="inner">{body}</div></details>')


def _lead_html(digest: dict) -> str:
    """The decision surface: the few names worth a human's attention
    first, each with the one line that says why."""
    agg = digest.get("aggregate") or {}
    ranked = (agg.get("ranked") or [])[:5]
    if not ranked:
        return ""

    out = []
    for n, e in enumerate(ranked, 1):
        sc = e.get("score") or {}
        basis = e.get("basis") or {}
        fresh = {
            "unknown": "age unknown", "single_sighting": "seen once",
            "forward_catalyst": "catalyst ahead", "late_to_story": "after the move",
            "running": "running days", "developing": "building",
        }.get((e.get("freshness") or {}).get("state", ""),
              (e.get("freshness") or {}).get("state", ""))
        comps = sc.get("components") or {}
        top = sorted((c for c in comps.items() if c[1]["contribution"]),
                     key=lambda kv: -kv[1]["contribution"])[:2]
        why = ", ".join(k.replace("_", " ") for k, _ in top)

        indep = len(basis.get("independent_channels") or [])
        bits = [f"{indep} channel{'s' if indep != 1 else ''} did own work"]
        if fresh:
            bits.append(fresh.replace("_", " "))
        if e.get("catalyst_events"):
            bits.append(f"{len(e['catalyst_events'])} filed event(s)")

        out.append(
            f'<div class="lead"><h3><span class="rank">{n}</span>'
            f'{_esc(e["ticker"])}'
            f'<span class="weak" style="font-weight:400;font-size:13px">'
            f'  {sc.get("total", 0):+.2f}</span></h3>'
            f'<div class="why">{" &middot; ".join(_esc(b) for b in bits)}</div>'
            f'<div class="why" style="margin-top:4px">strongest: {_esc(why)}</div>'
            f'</div>'
        )
    return ('<h2>Worth your attention</h2>'
            '<div class="note">Research priority, not trade signals. '
            'Ranked on independent sourcing, evidence grade, freshness and '
            'catalyst presence. Price and liquidity are not in the score '
            '&mdash; no market-data feed is wired into it.</div>'
            + "".join(out))


def _trust_html(digest: dict) -> str:
    """The parts that say how much the corroboration is worth."""
    agg = digest.get("aggregate") or {}
    out = []
    # System-health notes are important but they are not decisions. Three
    # stacked warnings above the first ticker pushed the actual content
    # off the first screen, so they fold into one line.
    status: list[str] = []

    # A dead price feed must look like a dead price feed, not like
    # "no results yet". Those are different facts and only one is fixable.
    health = digest.get("price_health") or {}
    if health and not health.get("price_feed_healthy") and health.get("total_signals"):
        status.append(
            '<div class="note"><b>Price feed unreachable.</b> '
            f'{health.get("fetch_failed", 0)} of {health["total_signals"]} '
            "recorded signals could not be priced, so none have a measured "
            "outcome. Performance cannot be evaluated until this is fixed "
            "&mdash; this is a broken data source, not an absence of "
            "results.</div>"
        )
    elif health.get("resolved_by_horizon"):
        by = health["resolved_by_horizon"]
        status.append(
            '<div class="note">Signals recorded: '
            f'{health.get("total_signals", 0)}. With a measured 7-day '
            f'return: {by.get("7", 0)}. Statistical conclusions need a few '
            "hundred; until then this is a record, not evidence.</div>"
        )

    vm = digest.get("verification_meta") or {}
    if vm.get("limitation"):
        status.append(
            '<div class="note"><b>Catalyst verification unavailable.</b> '
            + _esc(vm["limitation"]) + "</div>"
        )
    elif vm.get("by_status"):
        bs = vm["by_status"]
        status.append(
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
        for e in ranked[5:15]:          # first five are already in the lead
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
        if rows:
            out.append(_fold("Next in the research order",
                             "".join(rows), f"{len(ranked)} ranked total"))

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
            _fold("How much each channel reprints",
            '<div class="note">Share of claims that restate someone '
            "else&rsquo;s reporting. A news desk doing this is doing its job "
            "&mdash; it matters only because reprints must not count as "
            "independent confirmation.</div>"
            '<div class="card">' + "".join(rows) + "</div>",
            f"{len(orig)} channels")
        )

    if status:
        out.append(_fold("System status", "".join(status),
                         f"{len(status)} note(s)"))
    return "".join(out)



SECTIONS = (
    ("today", "Today"),
    ("scalp", "Scalp Lab"),
    ("intel", "Intelligence"),
    ("radar", "Catalyst Radar"),
    ("perf", "Performance Lab"),
)


def _section(anchor: str, title: str, body: str) -> str:
    """One of the five areas. Empty sections render a short honest line
    rather than vanishing - a missing section looks like a bug, and
    "nothing qualified today" is information."""
    inner = body or ('<div class="empty">Nothing here this run.</div>')
    return (f'<section id="{anchor}"><h2 class="sec">{_esc(title)}</h2>'
            f'{inner}</section>')


def _setups_html(digest: dict) -> str:
    """Daily setups, rendered into Scalp Lab."""
    out = []
    setups = digest.get("setups") or {}
    if setups.get("hits"):
        rows = []
        for h in setups["hits"]:
            ev = ", ".join(f"{k.replace('_',' ')} {v}"
                           for k, v in (h.get("evidence") or {}).items())
            rows.append(
                f'<div class="card"><div class="row">'
                f'<b>{_esc(h.get("ticker",""))}</b>'
                f'<span class="pill good">{_esc(h.get("pattern","").replace("_"," "))}</span>'
                f'<span class="weak">close {h.get("signal_close")}</span>'
                f'<span class="weak">invalidation {h.get("invalidation")}</span>'
                f'</div><div class="weak">{_esc(ev)}</div></div>'
            )
        out.append(
            '<div class="note">Paper signals on the underlying, recorded and '
            "scored separately from the creator signals. Daily bars only, so "
            "no intraday setup is attempted. <b>No option is priced</b> "
            "&mdash; measuring execution cost needs chain data that is not "
            "configured, and an invented spread would defeat the point. "
            "Entry is the close of the session after the signal bar.</div>"
            + "".join(rows)
        )
    elif setups.get("errors"):
        out.append(
            '<div class="note"><b>Setup scan unavailable.</b> '
            + _esc("; ".join(f"{e['ticker']}: {e['reason']}"
                             for e in setups["errors"]))
            + "</div>"
        )

    return "".join(out)


def _funnel_html(digest: dict) -> str:
    """Where ideas died, with the reason attached to each loss."""
    f = digest.get("funnel") or {}
    counts = f.get("counts") or {}
    if not counts:
        return ""
    top = max(counts.values()) or 1
    label = {"mentioned": "Mentioned", "valid_symbol": "Valid symbol",
             "researched": "Own research", "corroborated": "Independently corroborated",
             "catalyst_backed": "Filed catalyst", "qualified": "Passed vetoes",
             "alerted": "Alerted"}
    bars = []
    for st in f.get("stages", []):
        n = counts.get(st, 0)
        pct = max(1, int(100 * n / top))
        drops = (f.get("drops") or {}).get(st) or []
        reasons = ""
        if drops:
            seen, uniq = set(), []
            for d in drops:
                r = d.get("reason", "")
                if r not in seen:
                    seen.add(r)
                    uniq.append(f"{len([x for x in drops if x.get('reason') == r])} &times; {_esc(r)}")
            reasons = ('<div class="weak" style="margin-top:3px;font-size:12px">'
                       + " &middot; ".join(uniq[:3]) + "</div>")
        bars.append(
            f'<div class="card" style="padding:10px 13px">'
            f'<div class="row" style="padding:0">'
            f'<b style="min-width:9ch">{n}</b>'
            f'<span>{_esc(label.get(st, st))}</span></div>'
            f'<div style="height:5px;border-radius:3px;background:#1b2330;'
            f'margin-top:7px"><div style="height:5px;border-radius:3px;'
            f'width:{pct}%;background:var(--accent)"></div></div>'
            f'{reasons}</div>')
    return ('<h3 class="sub2">Opportunity funnel</h3>'
            '<div class="note">Every loss carries a reason. A funnel '
            'without them is just a shrinking number.</div>'
            + "".join(bars))


def _creators_html(digest: dict) -> str:
    """Creator scoreboard, entirely benchmark-relative."""
    c = digest.get("creators") or {}
    rows = c.get("creators") or []
    if not rows:
        return ""
    ready = [r for r in rows if r.get("sufficient_data")]
    out = [f'<h3 class="sub2">Creator intelligence</h3>'
           f'<div class="note">{_esc(c.get("note", ""))}</div>']
    if not ready:
        out.append(f'<div class="empty">{_esc(c.get("status", ""))}<br>'
                   "Timeliness and reprint share are shown where known; "
                   "hit rate and edge need resolved outcomes.</div>")
    body = []
    for r in rows[:14]:
        if r.get("sufficient_data"):
            edge = r.get("edge_mean_pct")
            cls = "good" if (edge or 0) > 0 else "bad"
            metrics = (f'<span class="pill {cls}">{r["hit_rate_vs_spy"]}% beat SPY</span>'
                       f'<span class="weak">edge {edge:+.2f}%</span>'
                       f'<span class="weak">n={r["resolved"]}</span>')
        else:
            metrics = (f'<span class="weak">{_esc(r.get("withheld_reason", ""))}</span>')
        extra = []
        if r.get("timeliness_days") is not None:
            d = r["timeliness_days"]
            extra.append(f"{'early' if d > 0 else 'late'} by {abs(d):.1f}d")
        if r.get("reprint_share") is not None:
            extra.append(f"{int(r['reprint_share'] * 100)}% reprints")
        if r.get("uniqueness") is not None:
            extra.append(f"{int(r['uniqueness'] * 100)}% unique names")
        body.append(
            f'<div class="card"><div class="row"><b>{_esc(r["channel"])}</b>'
            f'{metrics}</div>'
            + (f'<div class="weak" style="padding:0 14px 11px">'
               f'{_esc(" &middot; ".join(extra))}</div>' if extra else "")
            + "</div>")
    out.append("".join(body))
    return "".join(out)


def _perf_html(digest: dict) -> str:
    """Performance Lab: what has actually been measured, and what has not."""
    health = digest.get("price_health") or {}
    sl = digest.get("signal_log") or {}
    out = []

    if health:
        by = health.get("resolved_by_horizon") or {}
        rows = "".join(
            f'<div class="row"><b>{_esc(h)}d</b>'
            f'<span class="weak">{n} signal(s) with a measured return</span></div>'
            for h, n in sorted(by.items(), key=lambda kv: int(kv[0])))
        out.append(
            f'<div class="card">{rows}</div>'
            f'<div class="note">{health.get("total_signals", 0)} signals recorded, '
            f'{health.get("measured", 0)} priced. '
            + (_esc(health.get("warning")) if health.get("warning") else
               "Statistical conclusions need a few hundred resolved signals; "
               "until then this is a record, not evidence.")
            + "</div>")

    if sl:
        out.append(
            f'<div class="note">This run: {len(sl.get("created") or [])} new signal(s), '
            f'{len(sl.get("updated") or [])} materially changed, '
            f'{sl.get("unchanged", 0)} unchanged.</div>')

    out.append(
        '<div class="note">No strategy here has been validated. The backtest '
        'showed one pattern below buy-and-hold across 330 signals and the '
        'other resting on 22 observations. Nothing on this page should be '
        'read as a working edge.</div>')
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
        rest = [t for t in tickers if t["ticker"] not in {m["ticker"] for m in multi}]
        ticker_html = (
            (
                f"<h2>Corroborated by independent work</h2>"
                + "".join(_ticker_card(t) for t in multi)
                if multi
                else ""
            )
            # 100+ cards is reference material, not a decision surface
            + _fold("Every ticker mentioned",
                    "".join(_ticker_card(t) for t in rest),
                    f"{len(rest)}")
        )
    else:
        ticker_html = (
            '<div class="empty">No tickers found in this run.<br>'
            "Either there were no new videos, or none discussed specific names.</div>"
        )

    signals_html = _signals_html(digest.get("signals") or {})
    trust_html = _trust_html(digest)
    lead_html = _lead_html(digest)

    # Decisions first, reference material last. Anchors let a phone jump
    # rather than scroll 400KB.
    nav = ('<nav class="jump">'
           + "".join(f'<a href="#{a}">{_esc(t)}</a>' for a, t in SECTIONS)
           + '</nav>')
    catalyst_events = digest.get("catalysts") or []
    cat_cards = [c for c in (_catalyst_card(e) for e in catalyst_events) if c]
    catalyst_html = _fold("Catalysts filed", "".join(cat_cards),
                          f"{len(cat_cards)}") if cat_cards else ""

    calendar = digest.get("calendar") or []
    calendar_html = (
        "<h2>Upcoming dated events</h2>"
        + "".join(_calendar_row(e) for e in calendar)
        if calendar else ""
    )

    video_html = _fold(
        "Video breakdowns",
        "".join(_video_card(v) for v in videos),
        f"{len(videos)}",
    ) if videos else ""

    # --- the five areas ------------------------------------------------
    setups = digest.get("setups") or {}
    setups_html = ""
    if setups.get("hits") or setups.get("errors") or setups.get("diagnostics"):
        # _trust_html already renders the setup block; pull it out so it
        # lands in Scalp Lab rather than halfway down Intelligence.
        setups_html = _setups_html(digest)

    today_sec = _section("today", "Today", lead_html + signals_html)
    scalp_sec = _section(
        "scalp", "Scalp Lab",
        setups_html
        + '<div class="note">Paper only. No broker is connected and no order '
          'can be placed &mdash; there is no execution code in this project. '
          'Options are not priced: no chain data is configured, and an '
          'invented spread would defeat the point of measuring cost.</div>')
    intel_sec = _section("intel", "Intelligence",
                         trust_html + radar_html + ticker_html + video_html)
    radar_sec = _section("radar", "Catalyst Radar", calendar_html + catalyst_html)
    perf_sec = _section("perf", "Performance Lab",
                        _funnel_html(digest) + _creators_html(digest)
                        + _perf_html(digest))

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
{nav}
{note}
{today_sec}
{scalp_sec}
{intel_sec}
{radar_sec}
{perf_sec}
<footer>
  This page reports what these videos claimed. It is not investment advice,
  not a recommendation, and not a judgment on whether any call is correct.
  Evidence labels describe how well a claim was supported on the video, not
  whether it is true. Catalyst entries are summaries of official filings and
  announcements; the direction shown reflects only the plain nature of the
  event, not a price forecast. Verify anything before acting on it.
</footer>
</div></body></html>"""

    # The single 400KB scrolling document is replaced by the tabbed shell.
    # Everything else about this function is unchanged: same output dir,
    # same digest.json, same .nojekyll, same deployment.
    try:
        from . import app_render, relevance, ticker_rows as _tr
        relevance.annotate(videos)
        rows = _tr.build(
            agg,
            under_radar=digest.get("under_the_radar"),
            signals=digest.get("signals"),
            setups=digest.get("setups"),
            watchlist=list(getattr(digest.get("_cfg", None), "watchlist", []) or []),
        )
        page = app_render.build_page(digest, history, rows)
    except Exception as exc:  # noqa: BLE001
        # Never lose the page over a rendering change; fall back to the
        # previous layout and say so rather than shipping nothing.
        print(f"  ! new shell failed ({type(exc).__name__}: {exc}); "
              "falling back to the previous layout")

    out_path = out_dir / "index.html"
    out_path.write_text(page, encoding="utf-8")

    (out_dir / "digest.json").write_text(
        json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_path
