# YouTube Scalper

Watches your finance YouTube channels, pulls the transcript of every new
video, and turns them into one digest: which tickers got talked about, what
the actual argument was, and whether that argument rested on anything.

You read the digest on your phone instead of watching six hours of video.

**What this is not:** investment advice, or a judgment on whether any call is
any good. It reports what was said and how well it was supported. The
"evidence" label on every claim describes the quality of the reasoning given
on the video — not whether the claim is true. Verify before you act on
anything.

---

## How it works

1. Reads each YouTube channel's public RSS feed and each website's feed to
   spot new content (no API key, no quota)
2. Pulls the transcript of a new video, or the article text of a new post
3. Sends each transcript to Claude with a strict schema: ticker, stance,
   thesis, catalyst, price target, conviction, evidence quality, timestamp
4. Aggregates across channels — the names several hosts hit independently
   float to the top
5. Writes a mobile web page, texts you, and pushes a notification

Runs hourly on GitHub Actions. No server, no cost beyond pennies of API usage.

---

## Setup

### 1. Create the repo

```bash
git init
git add .
git commit -m "Initial commit"
gh repo create yt-scalper --private --source=. --push
```

Private is fine — GitHub Pages works on private repos for personal accounts
on a paid plan; on the free tier, Pages requires a public repo. If you want
the page but not a public repo, use the push notification instead and skip
Pages, or keep the repo public (it contains no secrets — everything sensitive
lives in Actions Secrets).

### 2. Add your sources

Edit `config.yml`. YouTube channels:

```yaml
channels:
  - name: "Value Lens"
    handle: "@ValueLens"
    channel_id: null
```

Websites — give the URL and leave `rss` null:

```yaml
sites:
  - name: "Market Wire"
    url: "https://marketwire.example.com"
    rss: null
```

Then resolve the stable IDs once:

```bash
pip install -r requirements.txt
python resolve_sources.py
```

That writes each `channel_id` back into `config.yml`. Commit the result — from
then on, lookups never break even if a channel changes its handle.

### 3. Add your secrets

GitHub → repo → Settings → Secrets and variables → Actions → New secret:

| Secret | Needed for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `NTFY_TOPIC` | push | Any unguessable string you invent, e.g. `scalper-7fa31c9b` |
| `TWILIO_ACCOUNT_SID` | SMS | Twilio console dashboard |
| `TWILIO_AUTH_TOKEN` | SMS | Twilio console dashboard |
| `TWILIO_FROM_NUMBER` | SMS | Your Twilio number, `+15551234567` |
| `TWILIO_TO_NUMBER` | SMS | Your own phone, `+15551234567` |
| `YT_PROXY_URL` | optional | Only if transcripts get blocked — see below |

### 4. Turn on push notifications

Install the **ntfy** app (iOS/Android), then subscribe to the exact string you
used for `NTFY_TOPIC`. That is the whole setup — no account, no phone number.

Treat the topic name like a password: anyone who knows it can read your
notifications. Make it long and random.

### 5. Turn on the web page

Settings → Pages → Source: **Deploy from a branch** → branch `main`, folder
`/docs`. Then put that URL into `site_url` in `config.yml` so it appears in
your texts.

### 6. Test it

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run.py --dry-run          # processes videos, sends nothing
```

Or process one specific video to check the output quality:

```bash
python run.py --video dQw4w9WgXcQ --dry-run
```

Then trigger the real thing from the Actions tab → Scalper Digest → Run workflow.

---

## The one thing that might break

YouTube rate-limits transcript requests coming from datacenter IP addresses,
and GitHub Actions runners are datacenter IPs. You may see some videos come
back as blocked. The pipeline handles this honestly — blocked videos are
reported on the page rather than silently dropped — but if it happens often,
you have three options:

1. **Run it on your own machine instead** via cron. Residential IP, no
   blocking. Same code, just `python run.py` on a schedule.
2. **Add a residential proxy.** Set `YT_PROXY_URL` to
   `http://user:pass@host:port`. Webshare's rotating residential plan is the
   usual choice, a few dollars a month.
3. **Live with partial coverage.** Retries often clear it on their own, and
   a missed video shows up as a gap you can see rather than a silent hole.

Start without a proxy and only add one if you actually hit the limit.

---

## Running hourly

The workflow runs at the top of every hour. Three things make that safe
rather than noisy:

**Polling is free.** Checking feeds costs nothing — no API key, no quota.
Only *new* content reaches Claude, so 24 runs a day cost the same as one run
a day with the same amount of new content.

**Notifications are deduplicated.** `data/notified.json` records every item
you have already been alerted about. The page shows a rolling window, but a
text only goes out for something genuinely new. Two new videos in a day means
two alerts, not forty-eight.

**Videos found before their captions exist get retried.** Hourly polling often
catches a video within minutes of upload, before YouTube has generated the
caption track. Those go into `data/pending.json` and get retried each hour
rather than being marked processed and lost. After 12 attempts or 30 hours,
the pipeline concludes captions are genuinely disabled and records that as the
outcome.

### What hourly actually costs you

| | |
|---|---|
| Claude API | unchanged — priced per new item, not per run |
| GitHub Actions | ~1,400 min/month. **Free and unlimited on a public repo.** On a *private* repo the free tier is 2,000 min/month, so this nearly fills it. |
| Twilio SMS | ~1¢ per alert, and only for genuinely new findings |
| Transcript blocking | **this is the real risk** — see below |

The Actions budget is the thing to decide. A public repo is unlimited and this
code holds no secrets, so public is the simple answer. If you want it private,
either accept the quota or drop to every two hours (`0 */2 * * *`), which
halves the minutes and loses very little, since these channels do not post
hourly.

### On blocking

24x the polling means 24x the transcript requests from a datacenter IP, which
raises the odds YouTube throttles you. The retry queue absorbs the occasional
block. If it becomes constant, the fixes are in the section below — and
running on your own machine is the most reliable of them.

---

## Cost

With `claude-haiku-5-5` (the default), a one-hour video is roughly 12k input
tokens at $0.10 per million. Twelve videos a day is about **two cents**.
Twilio SMS is about a cent a message. GitHub Actions and ntfy are free at this
volume.

Switch `model` in `config.yml` to `claude-sonnet-5-5` for richer write-ups at
roughly 20x the token cost — still only a few dollars a month.

---

## Tuning

| Setting | What it does |
|---|---|
| `lookback_hours` | How far back a run looks for new items. Default 6 — comfortably above the hourly interval, so a missed run leaves no permanent gap. |
| `window_days` | How much history the digest page displays. |
| `pending_max_attempts` / `pending_give_up_hours` | When to stop retrying a video whose captions never appeared. |
| `max_videos_per_run` | Hard cap on cost. |
| `min_transcript_words` | Skips shorts and clips with no real content. |
| `watchlist` | Tickers to always pin to the top of the digest. |
| `notify.only_if_findings` | Stay quiet on days with nothing to report. |

## Commands

```bash
python run.py                    # normal run
python run.py --dry-run          # process, don't notify
python run.py --video VIDEO_ID   # one specific video
python run.py --url URL          # one specific article
python run.py --rebuild          # re-render the page from stored data, no API calls
python resolve_sources.py        # look up channel IDs and site feeds
```

## Layout

```
run.py                  pipeline entry point
resolve_sources.py      one-time channel ID + site feed lookup
config.yml              channels and tuning
scalper/
  config.py             config + secrets from env
  feeds.py              YouTube RSS polling
  sites.py              website feed discovery + article extraction
  transcripts.py        transcript fetching, retries, proxy
  analyze.py            Claude extraction schema + aggregation
  notify.py             Twilio SMS + ntfy push
  render.py             static HTML digest
data/                   videos, digests, retry queue, notification log
docs/                   the published page
```

---

## Running it for real

This project was developed in an environment with no outbound access to
YouTube, Stooq, or the Claude API, so the pipeline has never executed
against live data. Everything below is what you run to change that.

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # required
export YOUTUBE_API_KEY=...              # only for the one-time backfill
```

```bash
python resolve_sources.py               # fills any missing channel IDs
python scripts/backfill.py --limit 30   # one-time: discover 30 videos/channel
python run.py --dry-run                 # process, send nothing
python api.py --port 8000               # serve the JSON API
```

### Verifying it works

```bash
python run.py --video <ANY_VIDEO_ID> --dry-run
```

That exercises the full path — transcript fetch, Claude extraction, ticker
normalization, storage, rendering — on one video. If it prints a ticker
and an evidence label, the pipeline works end to end.

## Service layer and API

`scalper/services.py` holds every capability as a plain function returning
JSON-serializable dicts. Nothing in it prints, renders, or notifies. That
is the integration seam for The Byrde: import these functions and wrap each
in your framework's routing. `api.py` is a zero-dependency reference
implementation of exactly that.

| Service function | Endpoint |
|---|---|
| `list_channels` / `channel_detail` | `/channels`, `/channels/<name>` |
| `list_videos` / `video_detail` | `/videos`, `/videos/<id>` |
| `stock_mentions` | `/mentions?ticker=&days=` |
| `creator_reliability` | `/creators?days=` |
| `catalyst_intelligence` | `/catalysts?days=` |
| `opportunity_rankings` | `/rankings?days=&limit=` |
| `hourly_report` | `/report` |
| `historical_performance` | `/performance?ticker=` |
| `data_health` | `/health` |

`api.py` has no authentication and binds to localhost. Put auth in front of
it before exposing it anywhere.

## Performance scoring

```bash
python scripts/score_performance.py --days 365
```

Prices come from Stooq (free, no key). Three rules are enforced in code:

1. **Entry is the first close at or after publication**, never the prior
   close. A video published Saturday enters at Monday's close.
2. **Horizons are trading days**, counted from returned rows.
3. **An unelapsed horizon returns null.** It is never estimated or
   forward-filled, and a creator with too few scored calls gets no
   risk-adjusted figure at all.

Scoring cannot be retroactive. It begins accumulating the day you enable it.

## What is deliberately not here

- **No single 0–100 opportunity score.** `/rankings` returns separate
  queues — best-supported, independent consensus, under the radar, most
  discussed, with-filed-catalyst, speculative-only. Collapsing those into
  one number hides the distinction that matters: two analysts citing
  filings is not the same as high mention volume.
- **No point-in-time fundamentals**, so no honest long-horizon backtest.
  Backtesting on restated financials is worse than not backtesting.
