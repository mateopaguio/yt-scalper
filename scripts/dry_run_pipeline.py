#!/usr/bin/env python3
"""End-to-end rehearsal of the alert pipeline, against throwaway state.

Nothing here touches data/, sends a push, or calls an API. It drives
the real modules with synthetic events through every stage that can
silence or release an alert:

  1. freshness      new / updated / unchanged, against our own record
  2. evidence       body retrieved, or only a headline
  3. radar          verified public company, or archived
  4. classification critical / watchlist / dashboard-only
  5. selection      cooldowns and per-run caps
  6. ownership      which runner may actually send
  7. acknowledgement delivered vs failed, and what each writes

    python3 scripts/dry_run_pipeline.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import alert_state, alerts, changes, radar        # noqa: E402

OK, NO = "  ✓", "  ✗"


def banner(n, title):
    print(f"\n{'=' * 66}\n{n}. {title}\n{'=' * 66}")


def event(eid, title, company, ticker, mat, body, published=None, rel=True):
    return {
        "event_id": eid, "title": title, "source": "FDA Drugs",
        "url": f"http://example.test/{eid}",
        "published": published or datetime.now(timezone.utc).isoformat(),
        "has_body": body, "body_chars": 5000 if body else 0,
        "findings": {"companies": [{"company": company, "ticker": ticker}],
                     "materiality": mat, "is_market_relevant": rel,
                     "event_type": "regulatory_approval", "direction": "up",
                     "summary": f"{company}: {title}"},
    }


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    alert_state.PATH = tmp / "alert_log.json"
    changes.PATH = tmp / "catalyst_state.json"
    print(f"throwaway state: {tmp}")

    read_new = event("e-read", "Approval granted", "Pfizer", "PFE", 5, True)
    headline = event("e-head", "Warning letter", "Amgen Inc.", "AMGN", 4, False)
    private = event("e-priv", "Supplement recall", "Beaute Bounty", "", 2, True)
    stale = event("e-old", "Old approval", "Pfizer", "PFE", 4, True,
                  published=(datetime.now(timezone.utc)
                             - timedelta(days=30)).isoformat())
    batch = [read_new, headline, private, stale]

    # 1. freshness --------------------------------------------------
    banner(1, "Freshness: measured against our own record")
    first = changes.classify(batch, {})
    print(f"  first sighting : {first['counts']}")
    state = changes.commit(batch, {}, persist=False)
    second = changes.classify(batch, state)
    print(f"  same run again : {second['counts']}")
    print(OK if second["counts"]["new"] == 0 else NO,
          "re-running does not invent new events")

    bumped = json.loads(json.dumps(batch))
    bumped[0]["findings"]["materiality"] = 2          # material change
    third = changes.classify(bumped, state)
    print(f"  after a change : {third['counts']}")
    print(OK if third["counts"]["updated"] == 1 else NO,
          "a material change is detected; cosmetic ones are not")

    for ev in batch:
        ev["_state"] = "new"

    # 2 & 3. evidence and radar -------------------------------------
    banner(2, "Evidence and radar: what may be promoted at all")
    result = radar.build(batch)
    print(f"  buckets: {result['counts']}  "
          f"primary-source backed: {result['primary_source_backed']}")
    for ev in result["upcoming"] + result["priority"]:
        rd = ev["_radar"]
        syms = ",".join(v["ticker"] for v in rd["verified"])
        print(f"    promoted {syms:<12} {rd['evidence']['grade']}")
    for ev in result["archive"]:
        print(f"    archived {ev['event_id']:<10} {ev['_radar']['reason']}")
    print(OK if not any(e["event_id"] == "e-priv"
                        for e in result["priority"]) else NO,
          "no verified public company -> never promoted")

    # 4. classification ---------------------------------------------
    banner(3, "Classification: critical, watchlist, or dashboard-only")
    cands = alerts.catalyst_alerts(result)
    for a in cands:
        print(f"    {a['tier']:<10} {a['ticker']:<8} {a['reason']}")
    crit = [a for a in cands if a["tier"] == alerts.CRITICAL]
    print(OK if all(a["evidence"]["primary"] for a in crit) else NO,
          "every CRITICAL had its announcement actually read")
    print(OK if any(a["tier"] == alerts.DASHBOARD for a in cands) else NO,
          "headline-only filings are shown, never pushed")

    # 5. selection ---------------------------------------------------
    banner(4, "Selection: cooldowns and per-run caps")
    cfg = {"alerts": {"max_per_run": {"critical": 1, "watchlist": 1,
                                      "digest": 1},
                      "cooldown_hours": {"critical": 12, "watchlist": 24,
                                         "digest": 6}}}
    noise = [{"tier": alerts.WATCHLIST, "ticker": f"N{i}", "score": 1,
              "headline": f"N{i}", "key": f"watch:N{i}:k"} for i in range(3)]
    picked = alerts.select(alerts.merge(cands, noise), {}, cfg)
    print(f"  send {len(picked['send'])}, held {len(picked['suppressed'])}, "
          f"dashboard {len(picked['dashboard_only'])}")
    for a in picked["suppressed"]:
        print(f"    held {a['ticker']:<8} {a['reason']}")

    # 6 & 7. ownership and acknowledgement ---------------------------
    banner(5, "Ownership and acknowledgement: what each outcome writes")

    alert_state.record(picked["send"], delivered=False,
                       reason="suppressed_not_owner")
    print(f"  after a non-owner run      : cooldowns={len(alert_state.sent_map())}")
    print(OK if not alert_state.sent_map() else NO,
          "a non-owner writes no cooldown, so the owner is not silenced")

    alert_state.record(picked["send"], delivered=False, reason="ntfy 503")
    print(f"  after a FAILED delivery    : cooldowns={len(alert_state.sent_map())}")
    print(OK if not alert_state.sent_map() else NO,
          "a failed delivery writes no cooldown; the alert is retried")

    alert_state.record(picked["send"], delivered=True)
    sent = alert_state.sent_map()
    print(f"  after an ACKED delivery    : cooldowns={len(sent)}")
    print(OK if sent else NO, "only an acknowledged delivery starts a cooldown")

    again = alerts.select(alerts.merge(cands, noise), sent, cfg)
    resent = [a for a in again["send"] if a["key"] in sent]
    print(OK if not resent else NO, "a delivered alert does not re-fire")
    held_keys = {a["key"] for a in picked["suppressed"]}
    back = [a for a in again["send"] if a["key"] in held_keys]
    print(OK if back else NO,
          f"a capped alert returns next run ({len(back)} back in the queue)")

    print(f"\n{'=' * 66}\nNothing was sent. Nothing in data/ was touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
