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
import sys
import time
from datetime import datetime, timezone

from anthropic import Anthropic

from scalper import (
    alerts as alerting,
    analyze,
    briefs,
    catalysts as cat,
    config as cfgmod,
    feeds,
    freshness,
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

    ledger = track.build_ledger(videos)

    # Join catalysts onto the tickers creators are discussing. A name that
    # both has chatter AND a real filed event is the interesting case.
    # Refresh the name->ticker table from the corpus before joining, so
    # a company first seen today is resolvable today.
    security_master.build(videos)
    events_by_ticker = cat.by_ticker(catalyst_events)
    for entry in aggregate["tickers"]:
        entry["catalyst_events"] = events_by_ticker.get(entry["ticker"], [])

    # Check claimed catalysts against filed events. Distinguishes "no
    # filing found" from "these feeds cannot see this kind of event" -
    # no earnings source is configured, so an earnings claim is marked
    # outside_coverage rather than being cast as unconfirmed.
    verify.annotate(aggregate, events_by_ticker)

    return {
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
    """Alert only on things never alerted on before.

    Two independent triggers: a creator item carrying tickers, and a
    high-materiality catalyst event. At 24 runs a day the page can show a
    rolling window while texts stay strictly about what is new.
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

    # Signal-driven alerts: what is accelerating, newly supported, or
    # corroborated by a filed event - no fixed watchlist required.
    sig = digest.get("signals") or {}
    sig_msg, sig_keys = notify.build_signal_alert(sig, cfg.site_url)
    unseen = [k for k in sig_keys if k not in already]

    if not fresh and not hot and not unseen:
        print("\nNothing new to alert on.")
        return

    # record_signals=False: `fresh` is a subset, see build_digest docstring
    alert = build_digest(fresh, cfg, None, catalyst_events, record_signals=False)
    message = sig_msg or notify.build_sms(alert, cfg.site_url, new_only=True)

    if hot:
        tickers = []
        for c in hot:
            for company in (c["findings"].get("companies") or []):
                t = (company.get("ticker") or "").upper()
                if t and t not in tickers:
                    tickers.append(t)
        tag = " ".join(f"!{t}" for t in tickers[:3]) or "!macro"
        message = f"{tag} {len(hot)} filing(s)\n{message}"

    # --- tiered alerts -------------------------------------------------
    # Critical/watchlist go out as their own pushes with their own ntfy
    # priority, so an urgent one is never buried inside a digest. The
    # cooldown log is on disk: without it every run is the first run and
    # the same fact re-fires until the phone gets muted.
    candidates = alerting.classify(digest.get("aggregate") or {}, cfg.raw)
    picked = alerting.select(candidates, store.alert_log(), cfg.raw)
    tier_msgs = alerting.render(picked["send"], cfg.site_url)

    if args.dry_run:
        print(f"\n[dry run] would alert on {len(fresh)} item(s), {len(hot)} filing(s)")
        for tier, m in tier_msgs.items():
            print(f"  [{tier}] priority {m['priority']}: {m['count']} item(s)")
        if picked["suppressed"]:
            print(f"  suppressed: {len(picked['suppressed'])} "
                  f"({picked['suppressed'][0]['reason']})")
        print(message)
        return

    if tier_msgs:
        results = notify.send_tiered(cfg, tier_msgs)
        fired = [k for tier, m in tier_msgs.items()
                 if results.get(tier, {}).get("sent") for k in m["keys"]]
        store.record_alerts(fired)
        for tier, r in results.items():
            print(f"  {tier}: {'sent' if r.get('sent') else r.get('reason')} "
                  f"({r.get('count', 0)} item(s))")
    if picked["suppressed"]:
        print(f"  {len(picked['suppressed'])} alert(s) suppressed by cooldown/cap")

    print(f"\nAlerting: {len(fresh)} item(s), {len(hot)} filing(s)")
    notify.dispatch(cfg, alert, message=message)
    store.mark_notified(
        [r["video_id"] for r in fresh] + [c["event_id"] for c in hot] + unseen
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--video", help="process a single YouTube video ID")
    parser.add_argument("--url", help="process a single article URL")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="poll every channel now, ignoring tier timers")
    args = parser.parse_args()

    cfg = cfgmod.load()

    if args.rebuild:
        recent = store.recent_videos(days=cfg.window_days)
        digest = build_digest(
            recent, cfg, catalyst_events=store.recent_catalysts(days=cfg.window_days)
        )
        path = render.render(digest, store.load_digests())
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
        render.render(digest, store.load_digests())
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
    })

    path = render.render(digest, store.load_digests())
    print(f"\nRendered {path}")

    # Per-ticker briefs: a page per name, with bull and bear side by side.
    brief_dir = cfgmod.DOCS_DIR / "ticker"
    watch = set(cfg.watchlist)
    made = []
    for entry in digest["aggregate"]["tickers"]:
        if watch and entry["ticker"] not in watch:
            continue
        b = briefs.build(entry)
        briefs.render_page(b, brief_dir, cfg.site_url)
        made.append(b)
    if made:
        print(f"Built {len(made)} ticker brief(s) in docs/ticker/")

    if tokens_in or tokens_out:
        cost = tokens_in / 1_000_000 * 0.10 + tokens_out / 1_000_000 * 0.50
        print(f"Tokens: {tokens_in:,} in / {tokens_out:,} out  (~${cost:.4f})")

    notify_if_new(cfg, processed, digest, args, new_catalysts)

    return 0


if __name__ == "__main__":
    sys.exit(main())
