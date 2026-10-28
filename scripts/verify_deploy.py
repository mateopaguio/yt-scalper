#!/usr/bin/env python3
"""Is what is published actually what this commit builds?

Checks the things that silently drift:
  - index.html, digest.json and the ticker pages all from the SAME build
  - every ticker the index links to actually exists on disk
  - every ticker in the digest has a page (and vice versa)
  - the working tree matches what was committed
  - the scheduled job has run recently, and its last run succeeded

A page that looks fine while serving last week's data is the failure
mode this exists to catch: --rebuild regenerates index.html but NOT the
128 ticker briefs, so they can serve stale CSS and stale content
indefinitely without anything appearing wrong.

    python3 scripts/verify_deploy.py            # local only
    python3 scripts/verify_deploy.py --remote   # + Pages & Actions
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MAX_RUN_AGE_HOURS = 6


def _et(dt) -> str:
    """Show times in market time, like the pages do."""
    try:
        sys.path.insert(0, str(ROOT))
        from scalper import clock
        return clock.stamp(dt, "%Y-%m-%d %-I:%M %p")
    except Exception:                                   # noqa: BLE001
        return f"{dt:%Y-%m-%d %H:%M UTC}"


def _sh(*args):
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:                                   # noqa: BLE001
        return ""



# Beside the repository, never inside it: the repo is public.
TOKEN_FILES = (Path.home() / ".byrde-gh-token", ROOT.parent / ".byrde-gh-token")
REPO = "mateopaguio/yt-scalper"


def _api(path: str, token: str):
    """One GitHub API GET. Returns parsed JSON or None."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "byrde-verify"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:                                   # noqa: BLE001
        return None


def check_remote(problems: list, notes: list) -> None:
    """What is actually published, and is the schedule alive?

    Local files being self-consistent says nothing about what a visitor
    sees. Pages publishes from a specific branch, so a feature branch
    can be perfectly built and still not be the live site - and a
    workflow can be 'active' while failing every single run.

    The token is read from a file outside the repository and is never
    printed. Without it these checks are skipped, not failed.
    """
    token = ""
    for cand in TOKEN_FILES:
        try:
            if cand.exists():
                token = cand.read_text().strip()
                if token:
                    break
        except Exception:                               # noqa: BLE001
            continue
    if not token:
        notes.append("remote        : skipped (no readable token file)")
        return

    pages = _api("/pages", token)
    build = _api("/pages/builds/latest", token)
    if not pages:
        notes.append("remote        : GitHub API unreachable")
        return

    src = pages.get("source") or {}
    pub_branch = src.get("branch")
    notes.append(f"published from: {pub_branch}{src.get('path','')} "
                 f"-> {pages.get('html_url','')}")

    if build and build.get("commit"):
        live = build["commit"][:12]
        notes.append(f"live commit   : {live} "
                     f"({build.get('status')}, {build.get('created_at','')[:16]})")
        head_of = _api(f"/branches/{pub_branch}", token) if pub_branch else None
        if head_of:
            want = ((head_of.get("commit") or {}).get("sha") or "")[:12]
            if want and want != live:
                problems.append(
                    f"published build is {live} but {pub_branch} is at {want} "
                    "- the live site is behind its own branch")
            else:
                notes.append(f"              : matches {pub_branch} HEAD")

    local = _sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    if pub_branch and local and local != pub_branch:
        notes.append(f"              : this branch ({local}) is NOT the "
                     f"published one - local changes are not live by design")

    runs = _api("/actions/runs?per_page=20", token)
    if runs and runs.get("workflow_runs"):
        rs = runs["workflow_runs"]
        streak = 0
        for r in rs:
            if r.get("conclusion") == "failure":
                streak += 1
            else:
                break
        ok = [r for r in rs if r.get("conclusion") == "success"]
        notes.append(f"last workflow : {rs[0]['created_at'][:16]} "
                     f"{rs[0].get('conclusion')}")
        notes.append(f"last green    : "
                     f"{ok[0]['created_at'][:16] if ok else 'none in window'}")
        if streak:
            problems.append(
                f"scheduled workflow has failed {streak} run(s) in a row "
                f"(most recent {rs[0]['created_at'][:16]}) - it is firing, "
                "not succeeding")


def main() -> int:
    problems, notes = [], []

    index = DOCS / "index.html"
    digest = DOCS / "digest.json"
    if not index.exists():
        print("FAIL docs/index.html missing"); return 1
    html = index.read_text(encoding="utf-8")

    # --- 1. same build?
    gen_json = None
    if digest.exists():
        try:
            gen_json = json.loads(digest.read_text())["generated_at"]
        except Exception:                               # noqa: BLE001
            problems.append("digest.json unreadable")
    # The page shows market time now, not UTC.
    m = re.search(r"(\w{3} \d{1,2}, \d{1,2}:\d{2}\s*[AP]M\s*[A-Z]{2,4})", html)
    notes.append(f"index stamp   : {m.group(1) if m else '(none found)'}")
    notes.append(f"digest stamp  : {gen_json or '(none)'}")

    idx_m = index.stat().st_mtime
    tickers_dir = DOCS / "ticker"
    pages = sorted(tickers_dir.glob("*.html")) if tickers_dir.exists() else []
    if pages:
        oldest = min(p.stat().st_mtime for p in pages)
        drift_h = (idx_m - oldest) / 3600
        notes.append(f"ticker pages  : {len(pages)}, oldest is "
                     f"{drift_h:.1f}h behind index.html")
        if drift_h > 2:
            problems.append(
                f"{len(pages)} ticker page(s) up to {drift_h:.1f}h older than "
                "index.html - --rebuild does not regenerate briefs")
    else:
        problems.append("no ticker pages found")

    # --- 2. every link resolves
    linked = set(re.findall(r'href="ticker/([^"]+)\.html"', html))
    on_disk = {p.stem for p in pages}
    missing = sorted(linked - on_disk)
    if missing:
        problems.append(f"index links to {len(missing)} missing page(s): "
                        + ", ".join(missing[:5]))
    orphan = sorted(on_disk - linked)
    if orphan:
        notes.append(f"orphan pages  : {len(orphan)} on disk, not linked "
                     "(stale tickers from earlier runs)")

    # --- 3. digest vs pages
    if gen_json and digest.exists():
        try:
            d = json.loads(digest.read_text())
            syms = {t["ticker"] for t in (d.get("aggregate") or {}).get("tickers", [])}
            nopage = sorted(syms - on_disk)
            if nopage:
                notes.append(f"no page yet   : {len(nopage)} ticker(s) in the "
                             f"digest ({', '.join(nopage[:4])})")
        except Exception:                               # noqa: BLE001
            pass

    # --- 4. commit correspondence
    head = _sh("git", "rev-parse", "--short", "HEAD")
    branch = _sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    dirty = _sh("git", "status", "--porcelain")
    notes.append(f"commit        : {head} on {branch}")
    if dirty:
        n = len(dirty.splitlines())
        problems.append(f"{n} uncommitted change(s) - published output may "
                        "not correspond to any commit")

    # --- 5. scheduler health
    #
    # Measured against the last SUCCESSFUL run, not the last time a log
    # file was touched. A job that fires hourly and crashes every time
    # keeps run.log's mtime perfectly current, so mtime answers "did
    # something happen" when the only useful question is "did a digest
    # come out of it". That distinction is the whole finding here: by
    # mtime this deployment looked 13h stale; by digest history it had
    # not produced anything in four days.
    now = datetime.now(timezone.utc)
    log = ROOT / "logs" / "run.log"
    err = ROOT / "logs" / "error.log"

    last_success = None
    hist = ROOT / "data" / "digests.json"
    if hist.exists():
        try:
            runs = json.loads(hist.read_text())
            stamps = sorted(r["generated_at"] for r in runs if r.get("generated_at"))
            if stamps:
                last_success = datetime.fromisoformat(stamps[-1])
        except Exception:                               # noqa: BLE001
            problems.append("digests.json unreadable - cannot establish "
                            "when the pipeline last succeeded")

    last_sched = None
    if hist.exists():
        try:
            runs = json.loads(hist.read_text())
            sch = sorted(r["generated_at"] for r in runs
                         if r.get("generated_at")
                         and r.get("trigger") in ("scheduled", "github-actions"))
            if sch:
                last_sched = datetime.fromisoformat(sch[-1])
        except Exception:                               # noqa: BLE001
            pass
    if last_sched:
        notes.append(f"last SCHEDULED success: "
                     f"{(now - last_sched).total_seconds() / 3600:.1f}h ago")
    else:
        notes.append("last SCHEDULED success: none on record "
                     "(runs before today carry no trigger label)")

    if last_success:
        ok_h = (now - last_success).total_seconds() / 3600
        notes.append(f"last SUCCESS  : {ok_h:.1f}h ago "
                     f"({_et(last_success)})")
        if ok_h > MAX_RUN_AGE_HOURS:
            problems.append(f"no successful run in {ok_h:.1f}h "
                            f"(expected within {MAX_RUN_AGE_HOURS}h)")
    else:
        problems.append("no successful run on record")

    if log.exists():
        act_h = (now - datetime.fromtimestamp(log.stat().st_mtime, timezone.utc)
                 ).total_seconds() / 3600
        notes.append(f"last activity : {act_h:.1f}h ago (log written)")
        # Fired but produced nothing: the crash-loop signature.
        if last_success and act_h + 0.5 < ok_h:
            problems.append(
                f"job ran {act_h:.1f}h ago but last success was {ok_h:.1f}h "
                "ago - it is firing and failing, not idle")
    else:
        problems.append("no run log found")

    # Only a traceback NEWER than the last good run is a live problem;
    # the log keeps every historical crash forever.
    if err.exists() and err.stat().st_size:
        err_at = datetime.fromtimestamp(err.stat().st_mtime, timezone.utc)
        tail = err.read_text(errors="replace")[-6000:]
        if "Traceback" in tail:
            # The real exception line, not whatever warning the
            # interpreter printed afterwards.
            blk = tail.rsplit("Traceback", 1)[-1].strip().splitlines()
            exc = [l.strip() for l in blk
                   if re.match(r"^\w+(Error|Exception|Exit|Interrupt)\b", l.strip())]
            msg = (exc[-1] if exc else
                   next((l.strip() for l in reversed(blk) if l.strip()), "(unparsed)"))[:90]
            # Judged against the last SCHEDULED firing. A developer
            # running the pipeline by hand after a cron crash does not
            # mean cron recovered - and comparing against "last success"
            # of any kind quietly hides exactly that.
            same_run = (log.exists()
                        and abs(err_at.timestamp() - log.stat().st_mtime) < 120)
            if same_run:
                problems.append(
                    f"the last scheduled firing ({act_h:.1f}h ago) ended in "
                    f"a traceback: {msg}")
            elif last_success and err_at > last_success:
                problems.append(f"crash after the last good run: {msg}")
            else:
                notes.append(f"old crash     : {msg} (predates last success)")

    if "--remote" in sys.argv:
        check_remote(problems, notes)

    for n in notes:
        print(f"  {n}")
    print()
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        print(f"\n{len(problems)} deployment problem(s)")
        return 1
    print("deployment verified: index, digest and ticker pages are in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
