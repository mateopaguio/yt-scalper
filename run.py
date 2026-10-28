#!/usr/bin/env python3
"""Pipeline: find new content -> fetch -> Claude -> digest -> notify.

    python run.py                 # normal run
    python run.py --dry-run       # process, send nothing
    python run.py --video ID      # one specific YouTube video
    python run.py --url URL       # one specific article
    python run.py --rebuild       # re-render the page, no fetching or API calls
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

from anthropic import Anthropic

from scalper import (
    alerts as alerting,
    analyze,
    catalysts as cat,
    changes,
    health,
    alert_state,
    radar,
    creators,
    config as cfgmod,
    feeds,
    freshness,
    funnel,
    narratives,
    outcomes,
    notify,
    rank as ranking,
    render,
    scanner,
    security_master,
    signal_log,
    signals,
    sites,
    store,
    track,
    transcripts,
    verify,
)


def build_digest(
    videos: list[dict],
    cfg,
    stats: dict | None = None,
    catalyst_events: list[dict] | None = None,
    record_signals: bool = True,
) -> dict:
    """Build the digest.

    record_signals MUST be False for any partial-video digest. The alert
    path calls this with only the newly-processed videos; recording from
    that thinner evidence base would read as a material change and expire
    the good full-window signal, replacing a well-supported decision with
    a worse one derived from a subset. Only the full digest records.
    """
    stats = stats or {}
    catalyst_events = catalyst_events or []
    aggregate = analyze.aggregate(
        videos, cfg.watchlist, cfgmod.consensus_tiers(cfg)
    )

    # Trustworthiness layer, in dependency order. Each step annotates the
    # aggregate in place and the next one reads what the last wrote.
    #   narratives -> classifies every claim as derivative/primary/original,
    #                 then rebuilds consensus from channels that did their
    #                 OWN work, so one wire story stops counting as five
    #                 independent confirmations.
    #   freshness  -> separates genuinely new information from commentary
    #                 on a move that already happened.
    #   ranking    -> explainable score with components recorded separately
    #                 and veto rules; market-data components are declared
    #                 unavailable rather than defaulted to neutral.
    narratives.annotate(aggregate)
    freshness.annotate(aggregate, videos)
    ranking.rank(aggregate)

    # Byrde Score: the same components on 0-100, folded with measured
    # source reliability ONLY where enough calls have resolved. Never a
    # probability of profit.
    board = creators.scoreboard(signal_log.load().get("signals", []))
    rel_by_channel = {c["channel"]: (c["hit_rate_vs_spy"] / 100.0)
                      for c in board["creators"]
                      if c.get("hit_rate_vs_spy") is not None}
    for entry in aggregate["tickers"]:
        chans = (entry.get("basis") or {}).get("independent_channels") or []
        rels = [rel_by_channel[c] for c in chans if c in rel_by_channel]
        entry["byrde"] = ranking.byrde_score(
            entry, sum(rels) / len(rels) if rels else None)

    ledger = track.build_ledger(videos)

    # Join catalysts onto the tickers creators are discussing. A name that
    # both has chatter AND a real filed event is the interesting case.
    # Refresh the name->ticker table from the corpus before joining, so
    # a company first seen today is resolvable today.
    security_master.build(videos)

    # Classify new / updated / seen BEFORE the join, and stamp each
    # event with it. by_ticker() copies events onto the tickers, so
    # anything not stamped by this point is invisible to the alert
    # path downstream - it would read "seen" on every event and the
    # freshness gate would block every CRITICAL forever.
    catalyst_state = changes.classify(catalyst_events)
    _state_by_id = {}
    for _k in ("new", "updated"):
        for _ev in catalyst_state.get(_k) or []:
            if _ev.get("event_id"):
                _state_by_id[_ev["event_id"]] = _k
    for _ev in catalyst_events:
        _ev["_state"] = _state_by_id.get(_ev.get("event_id"), "seen")

    events_by_ticker = cat.by_ticker(catalyst_events)
    for entry in aggregate["tickers"]:
        entry["catalyst_events"] = events_by_ticker.get(entry["ticker"], [])

    # Check claimed catalysts against filed events. Distinguishes "no
    # filing found" from "these feeds cannot see this kind of event" -
    # no earnings source is configured, so an earnings claim is marked
    # outside_coverage rather than being cast as unconfirmed.
    verify.annotate(aggregate, events_by_ticker)

    digest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "video_count": len(videos),
        "videos": videos,
        "aggregate": aggregate,
        "catalysts": catalyst_events,
        "ledger": ledger,
        "creator_stats": track.creator_stats(ledger),
        "under_the_radar": track.under_the_radar(aggregate, ledger),
        "signals": signals.compute(videos, aggregate, events_by_ticker),
        # Written to data/signals.json before anything is displayed or
        # sent. Rejected signals are recorded too: without them, any
        # later claim that the vetoes work is untestable, because the
        # surviving sample is selected on the variable under test.
        "signal_log": signal_log.record(aggregate) if record_signals else None,
        # Measure realised outcomes for signals recorded on earlier runs.
        # Only on the full digest, and only after recording, so a signal
        # generated this run can be measured from the next one onward.
        "outcomes": (outcomes.measure_all() if record_signals else None),
        "price_health": (outcomes.diagnostics() if record_signals else None),
        # Daily technical setups on SPY/QQQ, recorded as paper signals
        # under their own engine so they are never pooled with the
        # creator-consensus signals.
        "setups": (scanner.run() if record_signals else None),
        "ranked": [e["ticker"] for e in aggregate.get("ranked", [])],
        "vetoed": [{"ticker": e["ticker"], "reasons": e["score"]["vetoes"]}
                   for e in aggregate.get("vetoed", [])],
        "echo_chamber": [e["ticker"] for e in aggregate.get("echo_chamber", [])],
        "shared_events": aggregate.get("shared_events", []),
        "channel_originality": aggregate.get("channel_originality", []),
        "freshness_meta": aggregate.get("freshness_meta", {}),
        "rank_meta": aggregate.get("rank_meta", {}),
        # Where ideas died and why. A funnel without reasons is just a
        # shrinking number.
        # New / updated / unchanged against our own stored record, not
        # against publication dates.
        "catalyst_changes": catalyst_state,
        "funnel": funnel.build(aggregate),
        # Creator performance, every figure relative to SPY. Rates are
        # withheld below 10 resolved calls rather than shown with a caveat.
        "creators": creators.scoreboard(signal_log.load().get("signals", [])),
        "verification_meta": aggregate.get("verification_meta", {}),
        "tier4_candidates": [
            c for c in track.tier4_candidates(videos) if c["nominated"]
        ],
        "calendar": cat.upcoming(catalyst_events, cfg.catalyst_horizon_days),
        "orphan_catalysts": [
            t for t in events_by_ticker
            if t not in {e["ticker"] for e in aggregate["tickers"]}
        ],
        "blocked_count": stats.get("blocked", 0),
        "skipped_count": stats.get("skipped", 0),
        "pending_count": stats.get("pending", 0),
    }
    # Built last: it reads the finished digest (price feed state, scanner
    # diagnostics, source timestamps) to answer "is any of this current?"
    digest["radar"] = radar.build(
        catalyst_events or [], changes=digest.get("catalyst_changes"))
    digest["health"] = health.snapshot(
        digest, videos,
        expected_hours=((cfg.raw.get("alerts") or {})
                        .get("expected_interval_hours")))
    return digest


def gather_work(cfg, args) -> list[dict]:
    """Everything to process this run: retries first, then new content."""
    if args.video:
        return [
            feeds.Video(
                video_id=args.video,
                title=f"(manual) {args.video}",
                channel="manual",
                published=datetime.now(timezone.utc),
                url=f"https://www.youtube.com/watch?v={args.video}",
            ).as_dict()
        ]
    if args.url:
        return [
            sites.Article(
                article_id=sites._slug(args.url),
                title="(manual)",
                source="manual",
                published=datetime.now(timezone.utc),
                url=args.url,
            ).as_dict()
        ]

    work: list[dict] = []

    # Retries come first — they are already-known videos whose captions
    # were not ready. Expired ones get recorded as a real outcome.
    retry, expired = store.due_pending(
        max_attempts=cfg.pending_max_attempts,
        give_up_hours=cfg.pending_give_up_hours,
    )
    for item in expired:
        record = item["video"]
        record["findings"] = None
        att, thr = item.get("attempts", 0), item.get("throttled", 0)
        record["skip_reason"] = (
            f"gave up after {att} caption attempt(s)"
            + (f" and {thr} throttled attempt(s)" if thr else "")
            + (" — captions are probably disabled" if att else " — never got a clean try")
        )
        store.add_videos([record])
        store.drop_pending(record["video_id"])
        print(f"  - gave up on: {record['title'][:50]}")

    if retry:
        print(f"  - retrying {len(retry)} video(s) awaiting captions")
        work.extend(item["video"] for item in retry)

    seen = store.seen_ids() | {i["video"]["video_id"] for i in store.load_pending()}

    print("Checking YouTube feeds...")
    work.extend(v.as_dict() for v in feeds.find_new_videos(cfg, seen, force=args.force))

    if cfg.raw.get("sites"):
        print("Checking websites...")
        work.extend(a.as_dict() for a in sites.find_new_articles(cfg, seen))

    return work[: cfg.max_videos_per_run]


def get_content(record: dict, cfg) -> tuple[str, int, dict]:
    """Fetch the text for one item. Returns (text, word_count, status)."""
    if record.get("source_type") == "article":
        text, words = sites.fetch_article(record["url"])
        if not text:
            return "", 0, {"skip": "could not extract article text"}
        return text, words, {}

    tr = transcripts.fetch(record["video_id"], proxy_url=cfg.proxy)
    if tr.ok:
        return tr.text, tr.word_count, {}
    if tr.not_ready or tr.blocked:
        return "", 0, {"pending": tr.error, "blocked": tr.blocked}
    return "", 0, {"skip": tr.error}


def process_catalysts(client, cfg, args) -> tuple[list[dict], int, int]:
    """Poll and analyze catalyst feeds. Independent of video/article work:
    a quiet hour for creators can still be a loud hour for filings."""
    if not cfg.raw.get("catalyst_feeds") or args.video or args.url:
        return [], 0, 0

    print("\nChecking catalyst feeds...")
    events = cat.find_new_catalysts(cfg, store.catalyst_ids())
    if not events:
        return [], 0, 0

    out: list[dict] = []
    tin = tout = 0
    for event in events:
        record = event.as_dict()
        body, _ = sites.fetch_article(record["url"])
        # Whether we actually read the announcement, recorded as a fact
        # rather than inferred later from the model's prose. All four
        # promoted FDA events were headline-only; the summaries said so
        # ("no body text was retrieved") but nothing downstream could
        # act on a sentence. An event nobody read is not evidence.
        record["body_chars"] = len((body or "").strip())
        record["has_body"] = record["body_chars"] >= 200

        findings = cat.analyze_catalyst(client, record, body, model=cfg.model)
        if findings:
            usage = findings.pop("_usage", {})
            tin += usage.get("input_tokens", 0)
            tout += usage.get("output_tokens", 0)
            record["findings"] = findings
            if findings.get("is_market_relevant"):
                names = ", ".join(
                    c.get("ticker") or c.get("company", "?")
                    for c in (findings.get("companies") or [])
                ) or "macro"
                print(f"    {findings.get('event_type')}: {names} "
                      f"(materiality {findings.get('materiality')}/5)")
        else:
            record["findings"] = None
        out.append(record)

    store.add_catalysts(out)
    return out, tin, tout


def notify_if_new(cfg, processed, digest, args, catalyst_events=None) -> None:
    """Decide what to send, then let exactly one runner send it.

    Structured so that every send goes through a single gate. Three
    defects came out of having two notification systems side by side:

      - the legacy dispatch ran unconditionally after the tiered block,
        so a runner that did not own notifications still pushed;
      - the function returned early when no new video, filing or signal
        key had appeared, which skipped tiered evaluation entirely - a
        newly verified catalyst could not alert on its own;
      - the two systems could both push for the same underlying fact.

    Tiered evaluation now happens first and unconditionally. The legacy
    dispatch is one more thing the gate controls, not a parallel path
    around it.
    """
    catalyst_events = catalyst_events or []
    already = store.notified_ids()

    fresh = [
        r for r in processed
        if r.get("findings") and (r["findings"].get("tickers") or [])
        and r["video_id"] not in already
    ]

    threshold = cfg.raw.get("catalyst_alert_materiality", 4)
    hot = [
        c for c in catalyst_events
        if (c.get("findings") or {}).get("is_market_relevant")
        and (c["findings"].get("materiality") or 0) >= threshold
        and c["event_id"] not in already
    ]

    sig = digest.get("signals") or {}
    sig_msg, sig_keys = notify.build_signal_alert(sig, cfg.site_url)
    unseen = [k for k in sig_keys if k not in already]

    # --- tiered alerts, evaluated unconditionally ----------------------
    # Not gated on fresh/hot/unseen. Those describe the legacy triggers;
    # a catalyst that became verified since the last run is new
    # information even when no video was processed this run.
    candidates = alerting.classify(digest.get("aggregate") or {}, cfg.raw)
    candidates = alerting.merge(
        alerting.catalyst_alerts(digest.get("radar") or {}, cfg.raw), candidates)
    picked = alerting.select(candidates, alert_state.sent_map(), cfg.raw)
    tier_msgs = alerting.render(picked["send"], cfg.site_url)

    # One gate, consulted before anything leaves this process.
    allowed, why = alert_state.may_send(cfg.raw)

    legacy_due = bool(fresh or hot or unseen)
    if not tier_msgs and not legacy_due:
        print("\nNothing new to alert on.")
        _ops_alert(cfg, digest, allowed)
        return

    def _legacy_message():
        """Build the legacy digest text. Only called if it will be used.

        Deliberately after the ownership check: a runner that may not
        send should not spend an LLM-shaped rebuild on a message it is
        about to throw away.
        """
        alert = build_digest(fresh, cfg, None, catalyst_events,
                             record_signals=False)   # `fresh` is a subset
        text = sig_msg or notify.build_sms(alert, cfg.site_url, new_only=True)
        if hot:
            tickers = []
            for c in hot:
                for company in (c["findings"].get("companies") or []):
                    t = (company.get("ticker") or "").upper()
                    if t and t not in tickers:
                        tickers.append(t)
            tag = " ".join(f"!{t}" for t in tickers[:3]) or "!macro"
            text = f"{tag} {len(hot)} filing(s)\n{text}"
        return alert, text

    if args.dry_run:
        print(f"\n[dry run] {len(fresh)} item(s), {len(hot)} filing(s)")
        print(f"  notifications: {'ALLOWED' if allowed else 'SUPPRESSED'} - {why}")
        for tier, m in tier_msgs.items():
            print(f"  [{tier}] priority {m['priority']}: {m['count']} item(s)")
            for line in m["body"].splitlines():
                print(f"      {line}")
        if legacy_due:
            _, preview = _legacy_message()
            print("  [legacy digest] would also send:\n      "
                  + "\n      ".join((preview or "").splitlines()[:4]))
        for sup in picked["suppressed"][:8]:
            print(f"  held {sup.get('ticker', '')}: {sup.get('reason')}")
        alert_state.record(picked["send"], delivered=False, reason="dry run")
        return

    if not allowed:
        # Computed and recorded, deliberately not sent - including the
        # legacy digest, which used to go out regardless. Recording it
        # as sent here would start a cooldown that silences the owner.
        print(f"\n  notifications suppressed: {why}")
        alert_state.record(picked["send"], delivered=False,
                           reason="suppressed_not_owner")
        _ops_alert(cfg, digest, allowed)
        return

    if tier_msgs:
        results = notify.send_tiered(cfg, tier_msgs)
        for tier in tier_msgs:
            ok = bool(results.get(tier, {}).get("sent"))
            rows = [a for a in picked["send"] if a["tier"] == tier]
            # Only an acknowledged delivery starts a cooldown.
            alert_state.record(rows, delivered=ok,
                               reason="" if ok else
                               str(results.get(tier, {}).get("reason", "")))
        fired = [k for tier, m in tier_msgs.items()
                 if results.get(tier, {}).get("sent") for k in m["keys"]]
        store.record_alerts(fired)
        for tier, r in results.items():
            print(f"  {tier}: {'sent' if r.get('sent') else r.get('reason')} "
                  f"({r.get('count', 0)} item(s))")

    if picked["suppressed"]:
        # Held, not consumed: nothing was written to the cooldown log
        # for these, so they are eligible again on the next run.
        print(f"  {len(picked['suppressed'])} alert(s) held "
              "(cooldown or per-run cap) - still eligible next run")

    if legacy_due:
        print(f"\nAlerting: {len(fresh)} item(s), {len(hot)} filing(s)")
        alert, message = _legacy_message()
        notify.dispatch(cfg, alert, message=message)
        store.mark_notified(
            [r["video_id"] for r in fresh] + [c["event_id"] for c in hot] + unseen
        )

    _ops_alert(cfg, digest, allowed)


def _ops_alert(cfg, digest: dict, allowed: bool) -> None:
    """One alert when ingestion keeps failing. Not one per failure.

    An outage that pushes hourly is an outage that gets muted, and a
    muted notifier is the same as no notifier. The latch clears on the
    first success, so the next outage alerts again.
    """
    h = digest.get("health") or {}
    acfg = cfg.raw.get("alerts") or {}
    threshold = int(acfg.get("ops_failure_threshold", 3))
    fails = int(h.get("consecutive_failures") or 0)

    if not fails and not h.get("stale"):
        alert_state.ops_mark_recovered()
        return
    if not alert_state.ops_should_alert(fails, threshold,
                                        int(acfg.get("ops_cooldown_hours", 12))):
        return
    if not allowed:
        return

    body = (f"Ingestion has failed {fails} run(s) in a row.\n"
            f"{h.get('stale_message') or ''}\n"
            f"Last error: {(h.get('last_attempt') or {}).get('error') or 'unknown'}")
    msg = {"ops": {"title": "Byrde: ingestion failing",
                   "body": body.strip(), "priority": 4,
                   "tags": "warning", "count": 1, "keys": []}}
    res = notify.send_tiered(cfg, msg)
    if res.get("ops", {}).get("sent"):
        alert_state.ops_mark_alerted()
        print(f"  ops alert sent ({fails} consecutive failures)")


def _trigger() -> str:
    """Best guess at what started this run: scheduled, or a human."""
    explicit = os.environ.get("BYRDE_TRIGGER", "").strip()
    if explicit:
        return explicit
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "github-actions"
    # launchd and cron give the process no controlling terminal.
    try:
        if not sys.stdin.isatty():
            return "scheduled"
    except Exception:                                   # noqa: BLE001
        pass
    return "manual"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--video", help="process a single YouTube video ID")
    parser.add_argument("--url", help="process a single article URL")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="poll every channel now, ignoring tier timers")
    parser.add_argument("--test-notification", action="store_true",
                        help="send one test push and exit, to prove the "
                             "channel works without waiting for an event")
    parser.add_argument("--alerts-dry-run", action="store_true",
                        help="rebuild from stored data and print exactly what "
                             "would be pushed, sending nothing")
    args = parser.parse_args()

    cfg = cfgmod.load()

    if args.test_notification:
        allowed, why = alert_state.may_send(cfg.raw)
        print(f"runner      : {alert_state.runner_id()}")
        print(f"owner       : {alert_state.notify_owner(cfg.raw)}")
        print(f"may send    : {allowed} ({why})")
        print(f"ntfy topic  : {'configured' if cfg.ntfy_topic else 'NOT SET'}")
        if not allowed:
            print("Not sending: this runner does not own notifications.")
            return 0
        res = notify.send_tiered(cfg, {"digest": {
            "title": "Byrde test",
            "body": "Test notification. If you can read this, the alert "
                    "channel works.",
            "priority": 3, "tags": "white_check_mark", "count": 1, "keys": []}})
        ok = res.get("digest", {}).get("sent")
        print("sent" if ok else f"not sent: {res.get('digest', {}).get('reason')}")
        return 0 if ok else 1

    if args.alerts_dry_run:
        recent = store.recent_videos(days=cfg.window_days)
        digest = build_digest(
            recent, cfg, catalyst_events=store.recent_catalysts(days=cfg.window_days))
        cands = alerting.classify(digest.get("aggregate") or {}, cfg.raw)
        cands = alerting.merge(
            alerting.catalyst_alerts(digest.get("radar") or {}, cfg.raw), cands)
        picked = alerting.select(cands, alert_state.sent_map(), cfg.raw)
        allowed, why = alert_state.may_send(cfg.raw)
        print(f"\nnotifications: {'ALLOWED' if allowed else 'SUPPRESSED'} - {why}")
        print(f"candidates: {len(cands)}  would send: {len(picked['send'])}  "
              f"suppressed: {len(picked['suppressed'])}\n")
        for tier, m in alerting.render(picked["send"], cfg.site_url).items():
            print(f"--- {tier.upper()} (ntfy priority {m['priority']}) ---")
            print(m["body"])
            print()
        dash = picked.get("dashboard_only") or []
        if dash:
            print(f"--- DASHBOARD ONLY ({len(dash)}) - shown, never pushed ---")
            for a in dash:
                print(f"  {a['ticker']}: {a['reason']}")
            print()
        for sup in picked["suppressed"][:10]:
            print(f"  held {sup.get('ticker', '')}: {sup.get('reason')} "
                  "(eligible next run)")
        return 0

    if args.rebuild:
        recent = store.recent_videos(days=cfg.window_days)
        digest = build_digest(
            recent, cfg, catalyst_events=store.recent_catalysts(days=cfg.window_days)
        )
        path = render.render(digest, store.load_digests(), cfg=cfg)
        # Seen only after a page was produced; a crashed run
        # must not consume a catalyst's novelty.
        changes.commit(digest.get('catalysts') or [])
        print(f"Rebuilt {path} from {len(recent)} stored items")
        return 0

    if not cfg.anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        return 2

    client = Anthropic(api_key=cfg.anthropic_key)

    work = gather_work(cfg, args)
    stats = {"blocked": 0, "skipped": 0, "pending": 0}

    if not work:
        print("\nNo new videos or articles.")
        process_catalysts(client, cfg, args)
        window = store.recent_videos(days=cfg.window_days)
        digest = build_digest(
            window, cfg, stats, store.recent_catalysts(days=cfg.window_days)
        )
        render.render(digest, store.load_digests(), cfg=cfg)
        # Seen only after a page was produced; a crashed run
        # must not consume a catalyst's novelty.
        changes.commit(digest.get('catalysts') or [])
        notify_if_new(cfg, [], digest, args)
        return 0

    print(f"\n{len(work)} item(s) to process")

    processed: list[dict] = []
    tokens_in = tokens_out = 0  # noqa: used across both loops

    for idx, record in enumerate(work):
        # Space out transcript requests. YouTube throttles bursts from one IP,
        # and a 20-video run back-to-back is exactly that pattern.
        if idx and record.get("source_type") != "article":
            time.sleep(cfg.request_delay_seconds)
        kind = "article" if record.get("source_type") == "article" else "video"
        print(f"\n> [{kind}] {record['channel']}: {record['title'][:55]}")

        text, words, status = get_content(record, cfg)

        if status.get("pending"):
            # A throttle is about our IP, not about this video. Don't let it
            # burn the retry budget meant for "captions not generated yet".
            store.queue_pending(
                record, status["pending"], count_attempt=not status.get("blocked")
            )
            stats["pending"] += 1
            if status.get("blocked"):
                stats["blocked"] += 1
            print(f"    queued for retry — {status['pending']}")
            continue

        if status.get("skip"):
            print(f"    skipped — {status['skip']}")
            stats["skipped"] += 1
            record["findings"] = None
            record["skip_reason"] = status["skip"]
            processed.append(record)
            store.drop_pending(record["video_id"])
            continue

        if words < cfg.min_transcript_words:
            print(f"    skipped — only {words} words")
            stats["skipped"] += 1
            record["findings"] = None
            record["skip_reason"] = "content too short"
            processed.append(record)
            store.drop_pending(record["video_id"])
            continue

        print(f"    {words:,} words -> analyzing")
        findings = analyze.analyze(
            client, record, text, model=cfg.model, max_tokens=cfg.max_output_tokens
        )

        if findings:
            usage = findings.pop("_usage", {})
            tokens_in += usage.get("input_tokens", 0)
            tokens_out += usage.get("output_tokens", 0)
            n = len(findings.get("tickers") or [])
            print(f"    {n} ticker(s), hype {findings.get('hype_level', '?')}/5")
            record["findings"] = findings
        else:
            # An API error (auth, rate limit, outage) is transient. Queue the
            # video for another attempt instead of marking it done forever.
            store.queue_pending(record, "analysis failed - will retry")
            stats["pending"] += 1
            print("    queued for retry - analysis failed")
            continue

        processed.append(record)
        store.drop_pending(record["video_id"])

    store.add_videos(processed)

    new_catalysts, cat_in, cat_out = process_catalysts(client, cfg, args)
    tokens_in += cat_in
    tokens_out += cat_out

    window = store.recent_videos(days=cfg.window_days)
    catalyst_window = store.recent_catalysts(days=cfg.window_days)
    digest = build_digest(window, cfg, stats, catalyst_window)
    store.add_digest({
        "generated_at": digest["generated_at"],
        "video_count": len(processed),
        "ticker_count": digest["aggregate"]["total_tickers"],
        # Who fired this run. Without it the history cannot answer
        # "is the scheduler alive?", because a developer running the
        # pipeline by hand looks exactly like a healthy cron job.
        "trigger": _trigger(),
    })

    path = render.render(digest, store.load_digests(), cfg=cfg)
    # Seen only after a page was produced; a crashed run
    # must not consume a catalyst's novelty.
    changes.commit(digest.get('catalysts') or [])
    globals()["_LAST_INGESTED"] = len(processed)
    print(f"\nRendered {path}")

    if tokens_in or tokens_out:
        cost = tokens_in / 1_000_000 * 0.10 + tokens_out / 1_000_000 * 0.50
        print(f"Tokens: {tokens_in:,} in / {tokens_out:,} out  (~${cost:.4f})")

    notify_if_new(cfg, processed, digest, args, new_catalysts)

    return 0


def _guarded() -> int:
    """Record every invocation, whatever happens to it.

    The heartbeat is written before main() does anything, so a run that
    dies on import or two lines in still leaves evidence that the
    scheduler fired. A run with a start and no finish is a crash, and
    the Data Health panel reports it as one.
    """
    rid = health.heartbeat(_trigger())
    try:
        code = main()
    except BaseException as exc:                        # noqa: BLE001
        health.finish(rid, False, f"{type(exc).__name__}: {exc}")
        raise
    ingested = globals().get("_LAST_INGESTED")
    health.finish(rid, code == 0,
                  "" if code == 0 else f"exit code {code}", ingested)
    return code


if __name__ == "__main__":
    sys.exit(_guarded())
