"""Styles for the terminal shell. Dark, dense, phone-first."""

APP_CSS = """
:root{
  --bg:#07090d; --panel:#0e1219; --panel2:#121823; --line:#1d2430;
  --ink:#e7edf5; --muted:#8b97a8; --dim:#5d6878;
  --up:#3ddc97; --down:#ff6b6b; --flat:#8b97a8; --accent:#3ddc97;
  --nav-h:58px;
}
*{box-sizing:border-box;min-width:0}
html,body{max-width:100%;overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--ink);
  /* explicit properties, not shorthand: a shorthand whose family slot is
     'inherit' is invalid CSS, and WebKit drops the WHOLE declaration
     rather than the one bad component - so the element silently falls
     back to UA defaults on iOS while looking correct in Chromium. */
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif;
  font-size:15px;line-height:1.5;
  -webkit-font-smoothing:antialiased;
  padding-bottom:calc(var(--nav-h) + env(safe-area-inset-bottom) + 12px)}
.wrap{max-width:900px;margin:0 auto;
  padding:0 max(14px,env(safe-area-inset-left)) 0 max(14px,env(safe-area-inset-right))}
a{color:var(--accent);text-decoration:none;word-break:break-word}

header.top{padding:16px 0 10px}
header.top h1{margin:0;font-size:19px;letter-spacing:-.01em}
header.top .sub{color:var(--dim);font-size:12.5px;margin-top:2px}

/* --- bottom nav (phone) --- */
nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:50;
  background:rgba(8,10,14,.96);backdrop-filter:blur(14px);
  border-top:1px solid var(--line);display:flex;
  padding-bottom:env(safe-area-inset-bottom)}
nav.tabs button{flex:1 1 0;min-width:0;background:none;border:0;color:var(--dim);
  /* 11px is the readability floor for this project. The font-shorthand
     fix replaced an invalid shorthand with explicit properties and
     carried 10.5px across with it, putting all five tabs below the
     floor on every phone width. Space is bought back from padding and
     letter-spacing, never from legibility: at 320px the labels
     ellipsis, which is the correct trade. */
  font-family:inherit;font-weight:600;font-size:11px;line-height:1.2;
  letter-spacing:-0.1px;
  padding:9px 1px 7px;cursor:pointer;min-height:var(--nav-h);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px}
nav.tabs button .ic{font-size:16px;line-height:1}
nav.tabs button span:last-child{max-width:100%;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
@media(max-width:360px){nav.tabs button{padding-left:0;padding-right:0;
  gap:3px;letter-spacing:-0.2px}}
nav.tabs button[aria-selected="true"]{color:var(--accent)}
nav.tabs button[aria-selected="true"] .ic{transform:translateY(-1px)}
@media(min-width:760px){
  body{padding-bottom:24px}
  nav.tabs{position:sticky;top:0;bottom:auto;border-top:0;
    border-bottom:1px solid var(--line);max-width:900px;margin:0 auto}
  nav.tabs button{flex:0 0 auto;flex-direction:row;gap:7px;min-height:46px;
    padding:0 16px;font-size:13px}
}
panel,[role=tabpanel]{display:block}
[role=tabpanel][hidden]{display:none}

h2.sec{font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted);margin:22px 0 11px;font-weight:700}
h3.sub2{font-size:13.5px;margin:20px 0 9px;font-weight:650}

.note{background:#15130a;border:1px solid #3b3312;color:#d6c377;
  padding:10px 12px;border-radius:9px;font-size:12.5px;margin-bottom:12px;
  overflow-wrap:anywhere}
.empty{color:var(--dim);padding:16px;text-align:center;font-size:13.5px;
  border:1px dashed var(--line);border-radius:10px}

/* --- compact ticker row --- */
.trow{border:1px solid var(--line);border-radius:10px;background:var(--panel);
  margin-bottom:7px;overflow:hidden}
.trow>summary{list-style:none;cursor:pointer;padding:11px 12px;
  display:grid;grid-template-columns:auto 1fr auto;gap:9px;align-items:center;
  min-height:44px}
.trow>summary::-webkit-details-marker{display:none}
.trow .sym{font-weight:700;font-size:15px;letter-spacing:-.01em}
.trow .mid{display:flex;flex-wrap:wrap;gap:5px;align-items:center;min-width:0}
.trow .co{color:var(--dim);font-size:12px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:14ch}
.trow .score{font-variant-numeric:tabular-nums;font-weight:700;font-size:14px;
  text-align:right;min-width:4ch}
.trow .inner{padding:0 12px 12px;border-top:1px solid var(--line);margin-top:2px}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:0 0 auto}
.dot.bull{background:var(--up)} .dot.bear{background:var(--down)}
.dot.mixed{background:#d8b24a} .dot.unclear{background:var(--dim)}

.tag{font-size:11px;padding:2px 7px;border-radius:5px;font-weight:600;
  border:1px solid currentColor;white-space:nowrap;line-height:1.5}
.tag.corroborated{color:var(--up)} .tag.under_radar{color:#6fb8ff}
.tag.catalyst{color:#d8b24a} .tag.echo{color:var(--down)}
.tag.heating{color:#ff9f55} .tag.vetoed{color:var(--dim)}
.tag.fresh{color:var(--up)} .tag.stale{color:var(--down)}
.tag.watchlist{color:#c58bff}

.meta{color:var(--muted);font-size:12.5px;display:flex;flex-wrap:wrap;gap:4px 12px}
.kv{display:flex;gap:6px}
.kv b{color:var(--ink);font-weight:600}

/* --- controls --- */
.controls{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:11px}
.controls button,.controls select{background:var(--panel2);color:var(--ink);
  border:1px solid var(--line);border-radius:8px;padding:9px 12px;
  font-family:inherit;font-weight:600;font-size:12.5px;
  min-height:44px;cursor:pointer}
.controls button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}

/* --- video card --- */
.vcard{border:1px solid var(--line);border-radius:10px;background:var(--panel);
  margin-bottom:7px;overflow:hidden}
.vcard>summary{list-style:none;cursor:pointer;padding:11px 12px;min-height:44px}
.vcard>summary::-webkit-details-marker{display:none}
.vcard .vt{font-weight:600;font-size:14px;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.vcard .vmeta{color:var(--dim);font-size:12px;margin-top:4px;
  display:flex;gap:9px;flex-wrap:wrap}
.vcard .vsum{color:var(--muted);font-size:12.5px;margin-top:6px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.vcard .inner{padding:0 12px 12px;border-top:1px solid var(--line)}
.flag{color:#d8b24a;font-size:12px}

.lead{background:linear-gradient(180deg,#121a26,#0e141d);
  border:1px solid #223047;border-radius:11px;padding:12px;margin-bottom:8px}
.lead .r{display:flex;gap:9px;align-items:baseline}
.lead .n{color:var(--dim);font-weight:700;font-size:12px;min-width:2ch}
.lead h3{margin:0;font-size:15.5px}
.lead .why{color:var(--muted);font-size:12.5px;margin-top:4px;line-height:1.45}

footer{color:var(--dim);font-size:11.5px;line-height:1.55;
  border-top:1px solid var(--line);margin-top:26px;padding:14px 0 10px}
"""
