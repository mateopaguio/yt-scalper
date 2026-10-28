# What needs you, and what does not

Nothing below is blocked on code. Each item needs a credential, a
permission, or a decision that is yours.

---

## 1. GitHub Actions — `ANTHROPIC_API_KEY`  (5 minutes)

The workflow has failed every run since Oct 26. One cause.

1. Settings → Secrets and variables → **Actions** → New repository secret
2. Name: `ANTHROPIC_API_KEY`
3. Value: the same key already in `~/byrde/.anthropic-key` on the Mac
   (106 chars, `sk-ant-` prefix — verified present and well-formed;
   its value has never been printed in this session)
4. Optional: `NTFY_TOPIC`, only needed once the cloud runner owns
   notifications

**Verify:** Actions → Scalper Digest → Run workflow → the run goes green.

---

## 2. Workflow preflight patch  (2 minutes, no broad token)

My token has no `workflow` scope by design, so I cannot push this.

- Easiest: open `.github/workflows/digest.yml` in the GitHub web UI and
  paste from `ops/digest.yml.proposed`
- Or locally: `git apply ops/workflow-preflight.patch`

It adds a step that prints which inputs are present — **names and
presence only, never values** — so the next failure names itself
instead of being inferred from an exit code.

---

## 3. Alpaca keys on the Mac  (10 minutes, free)

This is what restores the price feed, at no cost.

1. Create a free account at alpaca.markets and generate API keys
   (paper-trading keys are fine; no funding, no live trading)
2. Add to `~/byrde/.notify.env` (gitignored, never committed):

```
export ALPACA_KEY_ID=...
export ALPACA_SECRET_KEY=...
```

3. Leave `ALPACA_FEED` unset — the code probes SIP and falls back to
   IEX based on what your account is actually entitled to, rather than
   assuming either.

**Verify, on the Mac, not in a sandbox:**

```bash
cd ~/byrde && python3 scripts/diagnose_feeds.py SPY QQQ NVDA
```

Expect reachability `reachable (NN ms)` on every host, an entitlement
line naming the feed, and a per-ticker `usable` verdict with a bar
count and date range.

---

## 4. Check the Mac scheduler is still loaded  (1 minute)

It has not fired since 11:57 UTC Oct 27. The run history is consistent
with a sleeping laptop, but I cannot read `~/Library` to confirm the
job is loaded.

```bash
launchctl list | grep byrde
```

Nothing back means it needs reinstalling: `bash ~/byrde/INSTALL_HOURLY.sh`

---

## 5. Decisions that are yours

- **Merge timing.** `ops/DEPLOYMENT.md` has the checklist and rollback.
  It says fix Actions *before* merging; merging onto a broken scheduler
  buys a green merge and a silent system.
- **Notification handover.** Stay `local` until Actions has had green
  ingestion runs on the new code. Then set the repository variable
  `BYRDE_NOTIFY_OWNER=github-actions`.
- **Market data spend.** `ops/MARKET_DATA.md` recommends spending
  nothing yet. Equities are free; option quotes are the expensive need
  and there is not yet a measured edge to justify pricing.

---

## What I could not do, and why

- **Test the Alpaca adapter against live data.** No environment I can
  reach resolves `data.alpaca.markets` — both the Mac's Linux VM and
  the cloud container return "DNS does not resolve", while
  `api.github.com` returns 200. The adapter is covered by four recorded
  Alpaca-shaped fixtures instead, and `diagnose_feeds.py` is written to
  be run by you where the network works.
- **Re-run the strategies (Part C.1).** Same reason: it needs market
  data.
- **Verify WebKit rendering.** Playwright's WebKit build is blocked by
  egress, and Safari can only be granted read-only here. A look at the
  site on your iPhone would settle it.
- **Read `~/Library/LaunchAgents`.** Protected location; hence item 4.
