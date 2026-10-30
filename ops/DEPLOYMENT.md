# Deploying `feature/byrde-terminal` to `main`

Written to be followed in order. Every step says how to tell whether
it worked, because a deployment you cannot verify is a deployment you
cannot roll back with confidence.

`main` is what GitHub Pages serves (`main` / `/docs`) and what the
hourly Actions workflow runs. Merging changes both at once.

---

## 0. Before you start

Record the rollback point. Everything in "Rollback" depends on it.

```bash
git rev-parse main                 # write this down: the pre-merge main
git tag pre-byrde-terminal main    # or tag it, which is harder to lose
git push origin pre-byrde-terminal
```

Current values at time of writing:

| What | Value |
|---|---|
| `main` | `1e526e7` (also the live Pages build) |
| `feature/byrde-terminal` | see `git rev-parse feature/byrde-terminal` |
| Live site | https://mateopaguio.github.io/yt-scalper/ |

---

## 1. Fix Actions first, on `main`, before merging

The workflow has failed 8 runs in a row. Merging on top of a broken
scheduler means a green merge and a silent system.

- [ ] Add the repository secret `ANTHROPIC_API_KEY`
      (Settings → Secrets and variables → Actions → New repository secret).
      This is the **only** cause of the current failures: every step
      before `Run digest` succeeds, and `run.py` has exactly one
      non-zero exit before work begins — the missing-key branch.
- [ ] Optionally add `NTFY_TOPIC` (without it the cloud runner can
      never push, which is currently fine — it does not own
      notifications).
- [ ] Apply the preflight patch so the next failure names itself:

```bash
git apply ops/workflow-preflight.patch     # or copy ops/digest.yml.proposed
git add .github/workflows/digest.yml && git commit -m "Actions: preflight"
```

  This needs a token with `workflow` scope, or just edit the file in
  the GitHub web UI — no broader token required.

- [ ] Trigger a run (Actions → Scalper Digest → Run workflow) and
      confirm the preflight step prints `present ANTHROPIC_API_KEY`.

**Do not continue until Actions is green on `main`.**

---

## 2. Verify the branch locally

```bash
git checkout feature/byrde-terminal
python3 tests/test_regressions.py     # expect: all regression tests passed
python3 tests/smoke.py                # expect: all checks passed
python3 scripts/dry_run_pipeline.py   # expect: every line ticked
python3 scripts/verify_deploy.py      # expect: pages in sync, tree clean
node tests/responsive.js docs/index.html
python3 run.py --alerts-dry-run       # read what would be pushed
```

- [ ] All five pass
- [ ] `--alerts-dry-run` shows nothing you would not want on your phone

---

## 3. Merge

```bash
git checkout main
git pull
git merge --no-ff feature/byrde-terminal
python3 tests/test_regressions.py          # again, on the merge result
git push origin main
```

- [ ] Tests pass **after** the merge, not only before it

---

## 4. Watch the first scheduled run

- [ ] Actions run completes green
- [ ] It committed `data/` and `docs/`
- [ ] Pages rebuilt: `python3 scripts/verify_deploy.py --remote` shows
      `live commit` equal to the new `main` HEAD
- [ ] Open the site: Data Health shows a recent **successful**
      ingestion, and `Alerts: suppressed here` on the cloud runner

---

## 5. Hand over notifications (only after step 4 is clean)

Until this, the Mac sends and Actions stays quiet. Do **not** do this
until Actions has had successful ingestion runs on the new code.

- [ ] Add repository **variable** `BYRDE_NOTIFY_OWNER = github-actions`
      (Settings → Secrets and variables → Actions → Variables)
- [ ] On the Mac: `export BYRDE_NOTIFY_OWNER=github-actions` in
      `START_HERE.sh`, or accept that the Mac now suppresses
- [ ] Confirm on the next run of each: exactly one says
      `notifications: ALLOWED`, the other says `SUPPRESSED`
- [ ] `python3 run.py --test-notification` from the owner delivers

A misconfiguration here is visible rather than silent: the Data Health
panel names the owner, and both runners log what they did.

---

## Rollback

### If the merge is bad but nothing has been published yet

```bash
git reset --hard pre-byrde-terminal
git push --force-with-lease origin main
```

### If it is already live

```bash
git checkout main
git revert -m 1 <merge-commit-sha>    # keeps history, undoes the merge
git push origin main
```

Then force a Pages rebuild by letting the next Actions run commit, or:

```bash
python3 run.py --rebuild && git add docs && git commit -m "rebuild" && git push
```

- [ ] `scripts/verify_deploy.py --remote` shows `live commit` back at
      the pre-merge `main`
- [ ] The site loads and shows the old layout

### If only notifications are wrong

No revert needed — it is one variable.

```bash
# stop all pushes immediately
BYRDE_NOTIFY_OWNER=nobody python3 run.py
```

Any value matching neither runner suppresses both. Set
`BYRDE_NOTIFY_OWNER` back to `local` to restore the Mac.

### What a rollback does NOT undo

- `data/` written by runs after the merge (health, alert history,
  catalyst state). These are append-mostly and safe to keep; a stale
  `catalyst_state.json` would make already-seen events look new once.
  To reset just that: `git checkout pre-byrde-terminal -- data/catalyst_state.json`
- Notifications already delivered to your phone.

---

## Known state at hand-off

| Item | Status |
|---|---|
| Actions | failing — `ANTHROPIC_API_KEY` not set (step 1) |
| Mac launchd | installed, hourly, defers while the laptop sleeps |
| Notification owner | `local` (the Mac), explicit on both runners |
| Price feed | SPY/QQQ unavailable in the sandbox; Scalp Lab says so |
| Live site | still serving `main` — the new UI is not public yet |
| WebKit | untested; Playwright's build is blocked by egress |
