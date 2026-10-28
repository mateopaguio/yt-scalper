# Setup walkthrough

Four tasks, in order. Each takes a few minutes and says how to tell it
worked.

**One rule throughout: never paste a key, token or topic into a chat
window — including to me.** Every step below puts secrets straight
into GitHub's secret store or into a gitignored file on your Mac. If
anything ever asks you to type a secret into a conversation, that is
the wrong instruction, including if it appears to come from me.

Open Terminal with `Cmd + Space`, type `Terminal`, press Return.

---

## Task 1 — Add the Anthropic key to GitHub Actions

**Why:** the hourly cloud job has failed every run since Oct 26. It
cannot start without this key. Your Mac already has it.

**You will not need to read or type the key.** You will copy it from a
file into your clipboard without it ever appearing on screen.

1. Copy the key to your clipboard:

```bash
cat ~/byrde/.anthropic-key | tr -d '\n' | pbcopy
```

   Nothing prints. That is correct — it went to the clipboard.

2. In a browser, open:
   `https://github.com/mateopaguio/yt-scalper/settings/secrets/actions`

3. Click **New repository secret**.

4. Name: `ANTHROPIC_API_KEY` — exactly that, capitals and underscores.

5. Click the **Secret** box and press `Cmd + V` to paste. You will see
   dots or a blurred value. Do not retype it.

6. Click **Add secret**.

7. Clear your clipboard so the key is not left sitting in it:

```bash
echo -n "cleared" | pbcopy
```

**Check it worked:** the secrets page now lists `ANTHROPIC_API_KEY`
with "Updated now". GitHub will never show you the value again — that
is by design, and it is why you do not need to see it.

---

## Task 2 — Apply the workflow preflight patch

**Why:** when a cloud run fails, the log currently shows only an exit
code. This adds a step that prints *which* inputs are present — names
only, never values — so the next failure explains itself.

The easy way, entirely in the browser:

1. Open:
   `https://github.com/mateopaguio/yt-scalper/blob/feature/byrde-terminal/ops/digest.yml.proposed`
2. Click the **Raw** button, then select all (`Cmd + A`) and copy
   (`Cmd + C`).
3. Open:
   `https://github.com/mateopaguio/yt-scalper/edit/feature/byrde-terminal/.github/workflows/digest.yml`
4. Select all in the editor (`Cmd + A`) and paste (`Cmd + V`).
5. Scroll down, leave "Commit directly to the `feature/byrde-terminal`
   branch" selected, and click **Commit changes**.

**Check it worked:** the file now contains a step named
`Preflight - which inputs are present`.

> Why not let me push it? My GitHub token deliberately has no
> permission to modify workflow files. A token that can rewrite the
> thing that runs on every push is a much bigger key to hold, and this
> change is a two-minute paste.

---

## Task 3 — Free Alpaca market-data credentials

**Why:** this restores the price feed. It is free and needs no money
in any account. Paper-trading keys are enough; nothing here can place
a real order.

1. Go to `https://alpaca.markets` and create an account.
2. Make sure you are in **Paper Trading** mode — there is a toggle in
   the dashboard. It should not say Live.
3. Find **API Keys** (usually right-hand side, "Generate New Key").
4. You will see a **Key ID** and a **Secret Key**. The secret is shown
   **once**. Leave that browser tab open for the next step.

5. In Terminal, open the secrets file in a plain editor:

```bash
nano ~/byrde/.notify.env
```

6. Move to the bottom with the arrow keys and add two lines, pasting
   each value from the browser (`Cmd + V`):

```
export ALPACA_KEY_ID=paste_the_key_id_here
export ALPACA_SECRET_KEY=paste_the_secret_here
```

   No quotes, no spaces around the `=`.

7. Save and exit nano: `Ctrl + O`, then Return, then `Ctrl + X`.

8. Confirm the file is not tracked by git — it should print
   `.notify.env`:

```bash
cd ~/byrde && git check-ignore .notify.env
```

   If that prints nothing, **stop** and tell me before continuing.

**Do not set `ALPACA_FEED`.** The code asks Alpaca which feed your
account may use and falls back correctly. Setting it by hand overrides
a check that exists precisely because SIP is not free on every
account.

**Check it worked:**

```bash
cd ~/byrde && source .notify.env && python3 scripts/diagnose_feeds.py SPY QQQ
```

You want to see:

- every host `reachable (NN ms)` — not "DNS does not resolve"
- `ALPACA_KEY_ID  set (NN chars)`
- an entitlement line naming `sip` or `iex` and why
- for each ticker: `status : usable`, a bar count, and a date range

If hosts are unreachable, that is your network, not the keys.

---

## Task 4 — Check the hourly job is still loaded

**Why:** it last ran at 07:57 your time on Oct 27. The gap pattern
looks like a sleeping laptop, but I cannot read `~/Library` to confirm
the job is installed.

```bash
launchctl list | grep byrde
```

- **A line comes back** (three columns ending `com.byrde.scalper`): the
  job is loaded. The middle column is the last exit code — `0` is
  healthy, anything else means the last run failed.
- **Nothing comes back**: it is not loaded. Reinstall it:

```bash
bash ~/byrde/INSTALL_HOURLY.sh
```

To run it once immediately rather than waiting:

```bash
launchctl start com.byrde.scalper
tail -f ~/byrde/logs/run.log      # Ctrl+C to stop watching
```

Remember the job is deferred while the Mac sleeps and runs once on
wake, so overnight gaps are expected rather than a fault.

---

## When all four are done

Tell me they are done — **not what the secrets are**. Send me the
output of:

```bash
cd ~/byrde && source .notify.env && python3 scripts/diagnose_feeds.py SPY QQQ NVDA
```

That output contains no secret values: it prints character counts and
feed names only. From it I can run the forward paper-signal workflow
and produce the diagnostic report.
