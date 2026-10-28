"""The terminal shell: five tabbed sections, compact rows, phone first.

Replaces the single 400KB scrolling document. Each section is a tab
panel; the bottom bar switches them and remembers where you were in
each, so moving between tabs does not lose your place.

No framework, no build step, no external request. One file, same
deployment.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from . import clock
from .app_css import APP_CSS
from . import ticker_rows as TR

TABS = (
    ("today", "Today", "◉"),
    ("scalp", "Scalp Lab", "▲"),
    ("intel", "Intelligence", "☰"),
    ("radar", "Catalysts", "⚡"),
    ("perf", "Performance", "✓"),
)


def e(t: Any) -> str:
    return html.escape(str(t if t is not None else ""), quote=True)


def _ts(iso: str) -> str:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - d
        h = delta.total_seconds() / 3600
        if h < 1:
            return f"{int(delta.total_seconds() / 60)}m ago"
        if h < 48:
            return f"{int(h)}h ago"
        return f"{int(h / 24)}d ago"
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------- rows
def ticker_row(r: dict) -> str:
    tags = "".join(
        f'<span class="tag {t}">{e(dict(TR.TAGS).get(t, t))}</span>'
        for t in r["tags"][:3])
    dot = {"bullish": "bull", "bearish": "bear",
           "mixed": "mixed"}.get(r["sentiment"], "unclear")
    score = r["intelligence"]
    score_txt = f"{score:.0f}" if score is not None else "--"

    ent = r.get("entry") or {}
    own = [i for i in (ent.get("items") or [])
           if i.get("basis") in ("primary", "original")]
    takes = "".join(
        f'<div style="margin-top:9px"><div class="meta">'
        f'<b>{e(i.get("channel", ""))}</b>'
        f'<span>{e((i.get("stance") or "").lower())}</span>'
        f'<span>{e(i.get("evidence", ""))}</span></div>'
        f'<div style="color:var(--muted);font-size:12.5px;margin-top:3px">'
        f'{e((i.get("thesis") or "")[:300])}</div></div>'
        for i in own[:4])

    scalp = r["scalp"]
    scalp_line = (f'<span class="kv"><span>Scalp</span>'
                  f'<b>{e(scalp["label"])}</b></span>')

    vetoes = ""
    if r["vetoes"]:
        vetoes = ('<div class="note" style="margin-top:9px">Rejected: '
                  + e("; ".join(r["vetoes"])) + "</div>")

    cat = ""
    if r["catalyst_title"]:
        cat = (f'<div class="meta" style="margin-top:8px">'
               f'<span class="kv"><span>Catalyst</span>'
               f'<b>{e(r["catalyst_title"])}</b></span></div>')

    return (
        f'<details class="trow" data-ticker="{e(r["ticker"])}" '
        f'data-tags="{e(",".join(r["tags"]))}" data-sent="{e(r["sentiment"])}" '
        f'data-score="{score if score is not None else -1}" '
        f'data-indep="{r["independent"]}" data-fresh="{e(r["freshness"])}" '
        f'data-company="{e(r.get("company") or "")}">'
        f'<summary>'
        f'<span class="sym">{e(r["ticker"])}</span>'
        f'<span class="mid"><span class="dot {dot}"></span>'
        f'<span class="co">{e(r["company"])}</span>{tags}</span>'
        f'<span class="score">{score_txt}</span>'
        f'</summary>'
        f'<div class="inner">'
        f'<div class="meta" style="margin-top:9px">'
        f'<span class="kv"><span>Independent</span><b>{r["independent"]}</b></span>'
        f'<span class="kv"><span>Mentions</span><b>{r["mentions"]}</b></span>'
        f'<span class="kv"><span>Reprints</span><b>{r["reprints"]}</b></span>'
        f'<span class="kv"><span>Bull/Bear</span><b>{r["bulls"]}/{r["bears"]}</b></span>'
        f'{scalp_line}</div>'
        f'<div class="meta" style="margin-top:4px;color:var(--dim)">'
        f'<span>{e(scalp["detail"])}</span></div>'
        f'{cat}{takes}{vetoes}'
        f'<div style="margin-top:10px"><a href="ticker/{e(r["ticker"])}.html">'
        f'Full research &rarr;</a></div>'
        f'</div></details>')


# -------------------------------------------------------------- videos
def video_card(v: dict) -> str:
    f = v.get("findings") or {}
    rel = v.get("relevance") or {}
    tickers = [t.get("ticker") for t in (f.get("tickers") or [])
               if (t.get("ticker") or "").strip()][:5]
    flags = (f.get("red_flags") or [])[:2]
    hype = f.get("hype_level")
    promo = f.get("promotional")

    badges = []
    if isinstance(hype, (int, float)) and hype >= 4:
        badges.append(f'<span class="tag stale">hype {int(hype)}/5</span>')
    if promo:
        badges.append('<span class="tag stale">promo</span>')
    if rel.get("bucket") == "archive":
        badges.append('<span class="tag vetoed">archive</span>')

    sym = " ".join(f'<span class="tag catalyst">{e(t)}</span>' for t in tickers)
    summary = (f.get("summary") or "")

    full = []
    if summary:
        full.append(f'<div style="color:var(--muted);font-size:13px;'
                    f'margin-top:10px">{e(summary)}</div>')
    if f.get("red_flags"):
        full.append('<h3 class="sub2">Concerns</h3><ul style="margin:0;'
                    'padding-left:18px;color:var(--muted);font-size:12.5px">'
                    + "".join(f"<li>{e(x)}</li>" for x in f["red_flags"])
                    + "</ul>")
    if f.get("macro_points"):
        full.append('<h3 class="sub2">Macro</h3><ul style="margin:0;'
                    'padding-left:18px;color:var(--muted);font-size:12.5px">'
                    + "".join(f"<li>{e(x)}</li>" for x in f["macro_points"][:6])
                    + "</ul>")
    url = v.get("url", "")
    if url.startswith(("http://", "https://")):
        full.append(f'<div style="margin-top:10px">'
                    f'<a href="{e(url)}" rel="noopener">Open source &rarr;</a></div>')

    return (
        f'<details class="vcard" data-bucket="{e(rel.get("bucket", ""))}">'
        f'<summary>'
        f'<div class="vt">{e(v.get("title", ""))}</div>'
        f'<div class="vmeta"><span>{e(v.get("channel", ""))}</span>'
        f'<span>{e(_ts(v.get("published", "")))}</span>{"".join(badges)}</div>'
        f'{f"<div class=vmeta style=margin-top:5px>{sym}</div>" if sym else ""}'
        f'<div class="vsum">{e(summary[:200])}</div>'
        + (f'<div class="flag" style="margin-top:5px">&#9888; '
           f'{e(flags[0][:90])}</div>' if flags else "")
        + f'</summary><div class="inner">{"".join(full)}</div></details>')


# ----------------------------------------------------------------- app
def _tabs_html() -> str:
    btns = "".join(
        f'<button role="tab" data-tab="{k}" aria-selected="{"true" if i == 0 else "false"}">'
        f'<span class="ic">{ic}</span><span>{e(label)}</span></button>'
        for i, (k, label, ic) in enumerate(TABS))
    return f'<nav class="tabs" role="tablist">{btns}</nav>'


SCRIPT = """
(function(){
  var tabs=[].slice.call(document.querySelectorAll('nav.tabs button'));
  var panels={};[].slice.call(document.querySelectorAll('[role=tabpanel]'))
    .forEach(function(p){panels[p.id]=p;});
  var pos={};                       // remembered scroll per section
  var current=tabs[0]&&tabs[0].dataset.tab;
  function show(k){
    if(k===current)return;
    pos[current]=window.scrollY;    // keep your place in the old tab
    tabs.forEach(function(b){b.setAttribute('aria-selected',b.dataset.tab===k);});
    for(var id in panels)panels[id].hidden=(id!==k);
    current=k;
    // Restore AFTER layout. Scrolling immediately clamps to the old
    // (often much shorter) panel height - that is why returning to a
    // long section landed near the top instead of where you left it.
    var y=pos[k]||0;
    requestAnimationFrame(function(){
      requestAnimationFrame(function(){window.scrollTo(0,y);});
    });
    try{history.replaceState(null,'','#'+k);}catch(e){}
  }
  tabs.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.tab);});});

  var q=document.getElementById('q'),qc=document.getElementById('qclear');
  if(q){
    q.addEventListener('input',function(){
      state.q=q.value.trim().toLowerCase();
      // Typing is an explicit request to see matches, so lift the
      // five-row cap rather than hiding results behind "Show all".
      state.limit=state.q?0:5;
      if(qc)qc.hidden=!state.q;
      apply();
    });
    if(qc)qc.addEventListener('click',function(){
      q.value='';state.q='';state.limit=5;qc.hidden=true;apply();q.focus();});
  }
  if(location.hash){var h=location.hash.slice(1);if(panels[h])show(h);}

  // Any element carrying data-go jumps to that section. Without this
  // the health strip's "details" link was inert - an affordance that
  // looks clickable and does nothing is worse than no link at all.
  document.addEventListener('click',function(ev){
    var a=ev.target.closest&&ev.target.closest('[data-go]');
    if(!a)return;
    var k=a.getAttribute('data-go');
    if(panels[k]){ev.preventDefault();show(k);window.scrollTo(0,0);}
  });

  // Ticker filtering + sorting, no reload.
  // SCOPED to the intelligence panel. A global '.trow' query also matched
  // the catalyst and performance rows and hid them, which silently
  // emptied those tabs.
  var intelPanel=document.getElementById('intel');
  var rows=intelPanel?[].slice.call(intelPanel.querySelectorAll('.trow')):[];
  var state={tag:'',sent:'',sort:'score',limit:5,q:''};
  function apply(){
    var vis=rows.filter(function(r){
      if(state.tag&&(r.dataset.tags||'').split(',').indexOf(state.tag)<0)return false;
      if(state.sent&&r.dataset.sent!==state.sent)return false;
      if(state.q){
        var hay=((r.dataset.ticker||'')+' '+(r.dataset.company||'')).toLowerCase();
        if(hay.indexOf(state.q)<0)return false;
      }
      return true;});
    vis.sort(function(a,b){
      if(state.sort==='indep')return (+b.dataset.indep)-(+a.dataset.indep);
      if(state.sort==='ticker')return a.dataset.ticker.localeCompare(b.dataset.ticker);
      return (+b.dataset.score)-(+a.dataset.score);});
    rows.forEach(function(r){r.style.display='none';});
    var n=state.limit===0?vis.length:state.limit;
    vis.slice(0,n).forEach(function(r,i){r.style.display='';r.style.order=i;});
    var more=document.getElementById('more');
    if(more){
      var hidden=Math.max(0,vis.length-n);
      more.textContent=hidden?('Show all '+vis.length):'Show top 5';
      more.style.display=(vis.length>5)?'':'none';
    }
    var c=document.getElementById('rowcount');
    if(c){
      var shown=Math.min(n,vis.length);
      c.textContent=(shown<vis.length)
        ? ('showing '+shown+' of '+vis.length)
        : (vis.length+' ticker'+(vis.length===1?'':'s'));
    }
  }
  document.querySelectorAll('[data-filter]').forEach(function(b){
    b.addEventListener('click',function(){
      var k=b.dataset.filter,v=b.dataset.value;
      state[k]=(state[k]===v)?'':v;
      document.querySelectorAll('[data-filter="'+k+'"]').forEach(function(x){
        x.setAttribute('aria-pressed',x.dataset.value===state[k]);});
      apply();});});
  var sel=document.getElementById('sort');
  if(sel)sel.addEventListener('change',function(){state.sort=sel.value;apply();});
  var more=document.getElementById('more');
  if(more)more.addEventListener('click',function(){
    state.limit=state.limit===5?0:5;apply();});

  // Video relevance filter - scoped for the same reason: catalyst cards
  // in the radar tab are also .vcard and were being hidden.
  var vids=intelPanel?[].slice.call(intelPanel.querySelectorAll('.vcard')):[];
  var vstate='trade_relevant';
  function vapply(){
    var n=0;
    vids.forEach(function(v){
      var ok=(vstate==='all')||(v.dataset.bucket===vstate);
      v.style.display=ok?'':'none'; if(ok)n++;});
    var c=document.getElementById('vidcount');
    if(c)c.textContent=n+' video'+(n===1?'':'s');
  }
  document.querySelectorAll('[data-vfilter]').forEach(function(b){
    b.addEventListener('click',function(){
      vstate=b.dataset.vfilter;
      document.querySelectorAll('[data-vfilter]').forEach(function(x){
        x.setAttribute('aria-pressed',x.dataset.vfilter===vstate);});
      vapply();});});
  apply();vapply();
})();
"""


def _changed_since(digest: dict, history: list[dict]) -> str:
    """What is new since the previous run, separated from repetition."""
    sl = digest.get("signal_log") or {}
    created = sl.get("created") or []
    updated = sl.get("updated") or []
    unchanged = sl.get("unchanged", 0)
    # Freshness comes from our own record (scalper.changes), never from
    # how recently something was published.
    cc = (digest.get("catalyst_changes") or {}).get("counts") or {}
    new_cats, upd_cats = cc.get("new", 0), cc.get("updated", 0)

    if not (created or updated or new_cats or upd_cats):
        return ('<div class="empty">Nothing new since the last run.<br>'
                f'{unchanged} signal(s) and {cc.get("unchanged", 0)} catalyst(s) '
                'repeated without material change.</div>')

    bits = []
    if created:
        bits.append(f'<span class="kv"><span>New signals</span>'
                    f'<b>{len(created)}</b></span>')
    if updated:
        bits.append(f'<span class="kv"><span>Materially changed</span>'
                    f'<b>{len(updated)}</b></span>')
    if new_cats:
        bits.append(f'<span class="kv"><span>New catalysts</span>'
                    f'<b>{new_cats}</b></span>')
    if upd_cats:
        bits.append(f'<span class="kv"><span>Catalysts updated</span>'
                    f'<b>{upd_cats}</b></span>')
    # "Repeated" alone sat beside the catalyst counts and read as a
    # catalyst number; it is signals. Name both, so neither is mistaken
    # for the other.
    bits.append(f'<span class="kv"><span>Signals repeated</span>'
                f'<b>{unchanged}</b></span>')
    if cc.get("unchanged"):
        bits.append(f'<span class="kv"><span>Catalysts repeated</span>'
                    f'<b>{cc["unchanged"]}</b></span>')

    names = ", ".join(c["ticker"] for c in created[:6])
    detail = (f'<div style="color:var(--muted);font-size:12.5px;margin-top:6px">'
              f'{e(names)}</div>' if names else "")
    return (f'<div class="lead"><div class="meta">{"".join(bits)}</div>'
            f'{detail}</div>')




def _clip(text: str, n: int) -> str:
    """Trim to a word boundary and say so.

    A hard slice cuts mid-word - "a close below roughl" - which reads
    as corrupted data rather than as an abbreviated sentence.
    """
    text = (text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    space = cut.rfind(" ")
    if space > n * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "\u2026"


_EVIDENCE_RANK = {"quoted": 3, "reasoned": 2, "asserted": 1, "none": 0}
_FRESH_WORDS = {
    "unknown": ("age unknown", "We have no record of when this story started."),
    "single_sighting": ("seen once", "One sighting; nothing to compare it to yet."),
    "forward_catalyst": ("catalyst ahead", "The event driving it has not happened yet."),
    "late_to_story": ("after the move", "The coverage came after the move."),
    "running": ("running days", "This has been running for days."),
    "developing": ("building", "Coverage is building across sources."),
}


def _best_claim(entry: dict) -> dict | None:
    """The single strongest thing anyone actually said about this name.

    Ranked by how well the claim was supported, then by the source
    doing its own work rather than reprinting, then by conviction.
    "Why does this matter" deserves the best sentence available, not
    whichever one happened to be stored first.
    """
    items = entry.get("items") or []
    if not items:
        return None

    def rank(it):
        basis = (it.get("basis") or "")
        return (
            _EVIDENCE_RANK.get((it.get("evidence") or "").lower(), 0),
            1 if basis in ("original", "primary") else 0,
            {"high": 3, "medium": 2, "low": 1}.get(
                (it.get("conviction") or "").lower(), 0),
            -int(it.get("tier") or 9),
        )
    return sorted(items, key=rank, reverse=True)[0]


def _why_block(entry: dict, row: dict, new_ids: set, upd_ids: set) -> str:
    """Why this name is here, what is verified, and whether it is new."""
    out = []

    claim = _best_claim(entry)
    if claim and claim.get("thesis"):
        who = claim.get("channel") or "a source"
        ev = (claim.get("evidence") or "").lower()
        qual = {"quoted": "quoting the source material",
                "reasoned": "reasoned from evidence",
                "asserted": "asserted without support"}.get(ev, ev or "")
        out.append(
            f'<div class="why2"><span class="wl">Why</span>'
            f'<span>{e(_clip(claim["thesis"], 240))}</span></div>'
            f'<div class="wsub">{e(who)}'
            + (f' &middot; {e(qual)}' if qual else "") + '</div>')

    # What is actually verified, as opposed to claimed.
    events = entry.get("catalyst_events") or []
    ver = entry.get("verification") or {}
    if events:
        ev0 = events[0]
        f0 = ev0.get("findings") or {}
        label = "Filed"
        eid = ev0.get("event_id")
        if eid in new_ids:
            label = "Filed (new)"
        elif eid in upd_ids:
            label = "Filed (updated)"
        out.append(
            f'<div class="why2"><span class="wl">{e(label)}</span>'
            f'<span>{e(_clip(f0.get("summary") or ev0.get("title", ""), 200))}</span></div>')
    else:
        status = (ver.get("status") or "").replace("_", " ")
        reason = {
            "outside coverage": "No filing feed covers this kind of claim, "
                                "so it is neither confirmed nor denied.",
            "unconfirmed": "No filing behind it yet.",
            "confirmed": "Matched to a filed event.",
        }.get(status, status or "Nothing filed behind it.")
        out.append(f'<div class="why2"><span class="wl">Verified</span>'
                   f'<span>{e(reason)}</span></div>')

    # Genuinely new, or the same story again?
    fresh = row.get("freshness")
    word, explain = _FRESH_WORDS.get(fresh, (fresh or "", ""))
    if word:
        out.append(f'<div class="why2"><span class="wl">New?</span>'
                   f'<span>{e(explain or word)}</span></div>')
    return "".join(out)


def _health_strip(h: dict) -> str:
    """One line, at the top of Today: is any of this current?

    The page used to look identical whether the pipeline had run ten
    minutes ago or crashed on every firing for four days. This is the
    first thing on the screen because staleness invalidates everything
    below it.
    """
    if not h:
        return ""
    la, ls = h.get("last_attempt") or {}, h.get("last_success") or {}
    stale = h.get("stale")
    blind = (h.get("market_data") or {}).get("blind")

    if stale:
        cls, msg = "bad", f'No successful run since {ls.get("display", "never")}'
    elif la.get("ok") is False:
        cls, msg = "warn", f'Last run failed at {la.get("display", "")}'
    elif blind:
        cls, msg = "warn", "Running, but the price feed is down"
    else:
        cls, msg = "ok", f'Updated {ls.get("display", "")}'
    return (f'<div class="health {cls}"><span class="dot"></span>'
            f'<span>{e(msg)}</span>'
            f'<a href="#" data-go="scalp" class="hlink">details</a></div>')


def _health_panel(h: dict) -> str:
    """The three clocks, the run record, and the errors behind them."""
    if not h:
        return '<div class="empty">No health record yet.</div>'
    la, ls = h.get("last_attempt") or {}, h.get("last_success") or {}
    src, md = h.get("sources") or {}, h.get("market_data") or {}

    def row(label, value, note=""):
        return (f'<div class="hrow"><span class="hk">{e(label)}</span>'
                f'<span class="hv">{e(value)}</span>'
                + (f'<span class="hn">{e(note)}</span>' if note else "")
                + '</div>')

    body = [
        row("Last attempted", la.get("display") or "never",
            (la.get("trigger") or "")),
        row("Last successful", ls.get("display") or "never",
            f'{ls.get("ingested")} ingested' if ls.get("ingested") is not None else ""),
    ]
    if la.get("error"):
        body.append(f'<div class="herr">{e(la["error"])}</div>')
    if h.get("crashed_runs"):
        body.append(f'<div class="herr">{h["crashed_runs"]} run(s) started '
                    'and never finished</div>')

    # Three clocks, never merged.
    body += [
        row("Sources", f'{src.get("count", 0)} items / '
                       f'{src.get("channels", 0)} channels'),
        row("Newest published", src.get("newest_published_display") or "unknown",
            "when the source put it out"),
        row("Newest ingested", src.get("newest_ingested_display") or "unknown",
            "when we saw it"),
        row("Market data", md.get("as_of_display") or "unknown",
            "price feed as of"),
        row("Feed last tried", md.get("attempted_display") or "unknown",
            f'{md.get("usable", 0)} of {md.get("scanned", 0)} underlyings usable'),
    ]
    if md.get("failed"):
        body.append(f'<div class="herr">Price feed failed for '
                    f'{e(", ".join(md["failed"]))}</div>')

    recent = h.get("recent") or []
    if recent:
        chips = "".join(
            f'<span class="rchip {"ok" if r.get("ok") else ("bad" if r.get("ok") is False else "warn")}"'
            f' title="{e(r.get("error") or r.get("trigger") or "")}">'
            f'{e(r.get("display", ""))}</span>' for r in recent)
        body.append(f'<div class="hruns">{chips}</div>')

    body.append(f'<div class="note">{e(h.get("note", ""))}</div>')
    return '<div class="hpanel">' + "".join(body) + '</div>'


def build_page(digest: dict, history: list[dict], rows: list[dict]) -> str:
    agg = digest.get("aggregate") or {}
    gen = digest.get("generated_at", "")
    try:
        stamp = clock.stamp(gen)
    except ValueError:
        stamp = gen

    # ---- Today
    # Filter FIRST, then take five. Slicing to five and then skipping
    # vetoed rows produced fewer than five entries with gaps in the
    # numbering (1, 3, 5) whenever a rejected ticker scored highly.
    eligible = [r for r in rows if "vetoed" not in r["tags"]]
    by_symbol = {t["ticker"]: t for t in (agg.get("tickers") or [])}
    cc = digest.get("catalyst_changes") or {}
    new_ids = {x.get("event_id") for x in (cc.get("new") or [])}
    upd_ids = {x.get("event_id") for x in (cc.get("updated") or [])}

    lead = []
    for n, r in enumerate(eligible[:5], 1):
        entry = by_symbol.get(r["ticker"]) or {}
        facts = [f'{r["independent"]} independent']
        if r["catalyst_count"]:
            facts.append(f'{r["catalyst_count"]} filed event')
        lead.append(
            f'<div class="lead"><div class="r"><span class="n">{n}</span>'
            f'<h3>{e(r["ticker"])}</h3>'
            f'<span class="iscore">'
            f'{r["intelligence"] if r["intelligence"] is not None else "--"}'
            f'<span class="iunit">/100</span></span></div>'
            f'<div class="why">{" &middot; ".join(e(w) for w in facts)}</div>'
            + _why_block(entry, r, new_ids, upd_ids)
            + '</div>')
    health = digest.get("health") or {}
    today = (
        _health_strip(health)
        + '<h2 class="sec">What changed</h2>' + _changed_since(digest, history)
        + '<h2 class="sec">Top developments</h2>'
        + ("".join(lead) or '<div class="empty">Nothing qualified this run.</div>')
        + '<div class="note">Research priority, not trade signals. '
          'Price and liquidity are not in this score.</div>')

    # ---- Scalp Lab
    setups = digest.get("setups") or {}
    hits = setups.get("hits") or []
    if hits:
        scalp_body = "".join(
            f'<div class="trow"><div style="padding:12px">'
            f'<span class="sym">{e(h.get("ticker", ""))}</span> '
            f'<span class="tag corroborated">'
            f'{e(h.get("pattern", "").replace("_", " "))}</span>'
            f'<div class="meta" style="margin-top:7px">'
            f'<span class="kv"><span>Close</span><b>{e(h.get("signal_close"))}</b></span>'
            f'<span class="kv"><span>Invalidation</span>'
            f'<b>{e(h.get("invalidation"))}</b></span></div></div></div>'
            for h in hits)
    else:
        # "No setup" and "could not look" are different answers and
        # must never share a message. The first is information; the
        # second is an outage wearing the first one's clothes.
        md = (digest.get("health") or {}).get("market_data") or {}
        errs = setups.get("errors") or []
        if md.get("blind"):
            failed = ", ".join(md.get("failed") or []) or "every underlying"
            scalp_body = (
                '<div class="empty bad"><b>Could not scan.</b><br>'
                f'No usable price history for {e(failed)}, so no pattern '
                'could be evaluated. This is not a finding that there are '
                'no setups &mdash; the scanner never got to look.'
                + (f'<br><span class="hn">{e("; ".join(md.get("reasons") or []))}</span>'
                   if md.get("reasons") else "")
                + '</div>')
        else:
            scanned = md.get("scanned", 0)
            scalp_body = (
                '<div class="empty"><b>No setup today.</b><br>'
                f'{scanned} underlying(s) scanned with usable data; none met '
                'the pattern rules.</div>')
    scalp = (
        '<h2 class="sec">Paper setups</h2>' + scalp_body
        + '<h2 class="sec">Data health</h2>' + _health_panel(digest.get("health") or {})
        + '<div class="note"><b>Paper only.</b> No broker is connected and '
          'no order can be placed. Options are not priced &mdash; no chain '
          'data is configured, and an invented spread would defeat the point '
          'of measuring cost. Entry is the close of the session after the '
          'signal bar.</div>')

    # ---- Intelligence
    # Search first: with 128 names, scrolling to one is the common
    # action and six filter chips were the only way to narrow. The
    # chips sit on one scrollable row beneath it rather than wrapping
    # into three stacked rows of buttons.
    filters = (
        '<div class="searchbar">'
        '<input id="q" type="search" inputmode="search" autocomplete="off"'
        ' placeholder="Search ticker or company" aria-label="Search tickers">'
        '<button id="qclear" aria-label="Clear search" hidden>&times;</button>'
        '</div>'
        '<div class="controls scroller">'
        '<button data-filter="tag" data-value="corroborated" aria-pressed="false">Corroborated</button>'
        '<button data-filter="tag" data-value="under_radar" aria-pressed="false">Under radar</button>'
        '<button data-filter="tag" data-value="catalyst" aria-pressed="false">Catalyst</button>'
        '<button data-filter="tag" data-value="fresh" aria-pressed="false">New info</button>'
        '<button data-filter="sent" data-value="bullish" aria-pressed="false">Bullish</button>'
        '<button data-filter="sent" data-value="bearish" aria-pressed="false">Bearish</button>'
        '</div>'
        '<div class="controls">'
        '<select id="sort" aria-label="Sort">'
        '<option value="score">Sort: Intelligence 0-100</option>'
        '<option value="indep">Sort: independent sources</option>'
        '<option value="ticker">Sort: A-Z</option></select>'
        '</div>'
        '<div class="meta" style="margin-bottom:9px">'
        '<span id="rowcount"></span>'
        '<button id="more" style="cursor:pointer;background:var(--panel2);'
        'color:var(--accent);border:1px solid var(--line);border-radius:8px;'
        'padding:9px 13px;font-family:inherit;font-weight:600;'
        'font-size:12.5px;min-height:44px">Show all</button>'
        '</div>')
    videos = digest.get("videos") or []
    vfilter = (
        '<div class="controls">'
        '<button data-vfilter="trade_relevant" aria-pressed="true">Trade-relevant</button>'
        '<button data-vfilter="market_context" aria-pressed="false">Market context</button>'
        '<button data-vfilter="archive" aria-pressed="false">Archive</button>'
        '<button data-vfilter="all" aria-pressed="false">All</button>'
        '</div><div class="meta" style="margin-bottom:9px">'
        '<span id="vidcount"></span></div>')
    intel = (
        '<h2 class="sec">Tickers</h2>' + filters
        + "".join(ticker_row(r) for r in rows)
        + '<h2 class="sec">Sources</h2>' + vfilter
        + "".join(video_card(v) for v in videos))

    # ---- Catalyst Radar
    #
    # Promotion requires a company that resolves to a listed ticker.
    # The screen was 73 FDA notices out of 75 events, 55 of them
    # materiality 1/5, two with a ticker: a recall of a supplement
    # sold by a company nobody can buy shares in is a headline, not a
    # catalyst. Nothing is deleted - the archive keeps every one of
    # them with the reason it was archived.
    rad = digest.get("radar") or {}

    def _cat_card(c, show_state=True):
        f = c.get("findings") or {}
        rd = c.get("_radar") or {}
        syms = " ".join(f'<span class="tag corroborated">{e(v["ticker"])}</span>'
                        for v in (rd.get("verified") or []))
        state = c.get("_state")
        badge = ""
        if show_state and state in ("new", "updated"):
            badge = f'<span class="tag {state}">{state}</span>'
        dupes = c.get("_duplicates") or []
        dup = (f'<span class="hn">also filed {len(dupes)}x elsewhere</span>'
               if dupes else "")
        return (
            f'<details class="vcard"><summary><div class="vt">'
            f'{e(c.get("title", ""))}</div><div class="vmeta">'
            f'{syms}{badge}'
            f'<span class="tag catalyst">materiality '
            f'{e(rd.get("materiality") or f.get("materiality"))}/5</span>'
            f'<span>{e(c.get("source", ""))}</span>'
            f'<span>{e(_ts(c.get("published", "")))}</span>{dup}'
            f'</div></summary><div class="inner">'
            f'<div style="color:var(--muted);font-size:12.5px;margin-top:9px">'
            f'{e(f.get("summary", ""))}</div>'
            + (f'<div class="hn" style="margin-top:8px">archived: '
               f'{e(rd.get("reason"))}</div>' if rd.get("reason") else "")
            + '</div></details>')

    radar_body = ""
    up, pri, arch = (rad.get("upcoming") or [], rad.get("priority") or [],
                     rad.get("archive") or [])
    if up:
        radar_body += '<h3 class="sub2">Dated ahead</h3>' + "".join(
            f'<div class="trow"><div style="padding:11px 12px">'
            f'<b>{e(clock.day((c.get("findings") or {}).get("scheduled_date")))}</b> '
            f'<span style="color:var(--muted);font-size:13px">'
            f'{e(_clip((c.get("findings") or {}).get("summary") or c.get("title", ""), 160))}'
            f'</span></div></div>' for c in up[:12])
    if pri:
        radar_body += ('<h3 class="sub2">Company events</h3>'
                       + "".join(_cat_card(c) for c in pri[:20]))
    if not up and not pri:
        radar_body += ('<div class="empty"><b>No company events this run.</b><br>'
                       f'{len(arch)} filing(s) came in with no verified public '
                       'company behind them.</div>')
    if arch:
        reasons = ", ".join(f"{v} {k}" for k, v in
                            sorted((rad.get("archive_reasons") or {}).items(),
                                   key=lambda x: -x[1]))
        radar_body += (
            f'<details class="vcard" style="margin-top:14px">'
            f'<summary><div class="vt">Archive ({len(arch)})</div>'
            f'<div class="vmeta"><span>{e(reasons)}</span></div></summary>'
            f'<div class="inner">'
            + "".join(_cat_card(c, show_state=False) for c in arch[:40])
            + '</div></details>')
    if rad.get("duplicates_collapsed"):
        radar_body += (f'<div class="note">{rad["duplicates_collapsed"]} '
                       'duplicate filing(s) collapsed into the rows above.</div>')
    vm = digest.get("verification_meta") or {}
    if vm.get("limitation"):
        radar_body += f'<div class="note">{e(vm["limitation"])}</div>'
    if rad.get("note"):
        radar_body += f'<div class="note">{e(rad["note"])}</div>'
    radar = radar_body or '<div class="empty">No catalysts this run.</div>'

    # ---- Performance
    health = digest.get("price_health") or {}
    fn = digest.get("funnel") or {}
    perf = []
    if fn.get("counts"):
        top = max(fn["counts"].values()) or 1
        lbl = {"mentioned": "Mentioned", "valid_symbol": "Valid symbol",
               "researched": "Own research", "corroborated": "Corroborated",
               "catalyst_backed": "Filed catalyst", "qualified": "Passed vetoes",
               "alerted": "Alerted"}
        perf.append('<h3 class="sub2">Opportunity funnel</h3>')
        for st in fn.get("stages", []):
            n = fn["counts"].get(st, 0)
            drops = (fn.get("drops") or {}).get(st) or []
            reason = (f'<div style="color:var(--dim);font-size:11.5px;'
                      f'margin-top:3px">{e(drops[0]["reason"])}</div>'
                      if drops else "")
            perf.append(
                f'<div class="trow"><div style="padding:10px 12px">'
                f'<div class="meta"><b style="min-width:5ch">{n}</b>'
                f'<span>{e(lbl.get(st, st))}</span></div>'
                f'<div style="height:4px;border-radius:2px;background:#182030;'
                f'margin-top:6px"><div style="height:4px;border-radius:2px;'
                f'width:{max(1, int(100 * n / top))}%;background:var(--accent)">'
                f'</div></div>{reason}</div></div>')
    cr = digest.get("creators") or {}
    if cr.get("creators"):
        perf.append('<h3 class="sub2">Creator intelligence</h3>')
        perf.append(f'<div class="note">{e(cr.get("note", ""))}</div>')
        for c in cr["creators"][:12]:
            if c.get("sufficient_data"):
                m = (f'<span class="tag corroborated">'
                     f'{c["hit_rate_vs_spy"]}% beat SPY</span>'
                     f'<span class="kv"><span>edge</span>'
                     f'<b>{c["edge_mean_pct"]:+.2f}%</b></span>')
            else:
                m = f'<span style="color:var(--dim)">{e(c.get("withheld_reason", ""))}</span>'
            extra = []
            if c.get("timeliness_days") is not None:
                d = c["timeliness_days"]
                extra.append(f"{'early' if d > 0 else 'late'} {abs(d):.1f}d")
            if c.get("reprint_share") is not None:
                extra.append(f"{int(c['reprint_share'] * 100)}% reprints")
            perf.append(
                f'<div class="trow"><div style="padding:11px 12px">'
                f'<div class="meta"><b>{e(c["channel"])}</b>{m}</div>'
                + (f'<div style="color:var(--dim);font-size:11.5px;margin-top:4px">'
                   f'{" &middot; ".join(e(x) for x in extra)}</div>' if extra else "")
                + '</div></div>')
    if health:
        by = health.get("resolved_by_horizon") or {}
        perf.append('<h3 class="sub2">Signal outcomes</h3>')
        perf.append('<div class="trow"><div style="padding:11px 12px">' + "".join(
            f'<div class="meta"><b style="min-width:4ch">{e(h)}d</b>'
            f'<span>{n} with a measured return</span></div>'
            for h, n in sorted(by.items(), key=lambda kv: int(kv[0]))) + '</div></div>')
        perf.append(f'<div class="note">{health.get("total_signals", 0)} recorded, '
                    f'{health.get("measured", 0)} priced. '
                    + e(health.get("warning") or
                        "Statistical conclusions need a few hundred resolved "
                        "signals; until then this is a record, not evidence.")
                    + '</div>')
    perf.append('<div class="note">No strategy here has been validated. The '
                'backtest showed one pattern below buy-and-hold across 330 '
                'signals and the other resting on 22 observations.</div>')

    bodies = {"today": today, "scalp": scalp, "intel": intel,
              "radar": radar, "perf": "".join(perf)}
    panels = "".join(
        f'<div role="tabpanel" id="{k}"{"" if i == 0 else " hidden"}>{bodies[k]}</div>'
        for i, (k, _, _) in enumerate(TABS))

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07090d">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>The Byrde Terminal</title>
<style>{APP_CSS}</style>
</head><body>
{_tabs_html()}
<div class="wrap">
<header class="top"><h1>The Byrde Terminal</h1>
<div class="sub">{e(stamp)} &middot; {len(digest.get('videos') or [])} sources &middot;
{len(rows)} tickers</div></header>
{panels}
<footer>
This page reports what these sources claimed. It is not investment advice and
not a recommendation. Evidence labels describe how well a claim was supported
on the source material, not whether it is true. Scalp setups are paper only:
no broker is connected, no option is priced, and no order can be placed.
Verify anything before acting on it.
</footer>
</div>
<script>{SCRIPT}</script>
</body></html>"""
