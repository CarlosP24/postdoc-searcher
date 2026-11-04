# Postdoc Radar

A daily search for postdoc positions, fellowships and scholarships that fit **your PIs** and **your topics**. It keeps a list of open and soon-to-open calls with the associated PI, call period and a summary of conditions. It emails you when it finds something interesting and publishes a dashboard on GitHub Pages (or locally).

```
inputs/pis.yaml      ─┐                       ┌─▶ data/opportunities.json   (the list)
inputs/topics.yaml   ─┼─▶  daily run (06:23) ─┼─▶ docs/  dashboard         (GitHub Pages / local)
inputs/me.yaml       ─┘                       └─▶ email digest             (new matches, calls opening)
```

## What it searches

| Source | How | Cost |
|---|---|---|
| Nature Careers, Science Careers, Physics Today Jobs, Times Higher Education | RSS search with your topics' `board_queries` | free |
| jobs.ac.uk | search results page | free |
| INSPIRE-HEP jobs (cond-mat / quant-ph) | API, includes deadlines | free |
| jobRxiv | RSS | free |
| Mastodon hashtags (#postdoc, #physicsjobs…), incl. Bluesky posts bridged into Mastodon | public API | free |
| Your PIs' group / "join us" pages | fetched daily, analysed only when they change and mention openings | free + a little AI |
| **Everything else:** LinkedIn posts and job ads, EURAXESS, AcademicJobsOnline, university and institute career pages, X/Twitter and Bluesky announcements | **Claude with web search**, a few rotating tasks per day (each PI, each topic) | AI |
| **Fellowships** (MSCA, Humboldt, Juan de la Cierva, "la Caixa", Newton, Veni, JSPS, Banting, Miller…) | `config/fellowships.yaml` + a periodic web check of the current call dates | AI |

LinkedIn and X are not scraped directly; their terms forbid it and they block bots. Instead, Claude's web search finds their public, search-indexed pages and posts.

### Pipeline
1. **Collect** items from the free sources.
2. **Pre-filter** by keywords (topics, PI names, title exclusions such as PhD or faculty).
3. **Triage** the candidates with Claude in batches. Each gets a 0–100 relevance score, a reason, the PI, the deadline and a summary.
4. **Enrich** the best ones by reading the full advert, to get the conditions and deadline.
5. **Web-search tasks:** one per PI (every 3/7/14 days by priority) and one per topic (every ~4 days), rotating so each run stays small.
6. **Fellowships:** each programme is matched against your PIs using host-country and host-institution rules plus your eligibility (mobility rule, nationality, years since PhD, "different from PhD institution"). Call windows are estimated from typical months (marked ≈) until a web check confirms the real dates (marked ✓).
7. **Merge and dedupe** into `data/opportunities.json`, then compute each item's status: open, upcoming, rolling or closed.
8. **Email** new items with relevance ≥ `notify_threshold`, plus fellowship calls that just opened and deadlines within 3 weeks.
9. **Publish** the dashboard data to `docs/data.js`.

## Quick start (local)

```bash
cd Postdoc_searcher
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY and the SMTP settings
.venv/bin/python -m postdoc_searcher plan       # check inputs, see what will run
.venv/bin/python -m postdoc_searcher run        # the daily run
open docs/index.html                            # the dashboard works straight from disk
```

Useful flags: `run --no-ai` (free sources and keyword scoring only), `--dry-run` (save and send nothing), `--no-email`, `--max-tasks 1`, `--only feeds,fellowships`.

To run daily on your Mac instead of GitHub, see `scripts/com.postdoc-radar.daily.plist`. Use one scheduler or the other, not both.

## Deploy on GitHub (recommended)

The app lives in its own repository. Its dashboard is served at `https://carlosp24.github.io/<repo-name>/`, a project page that does not touch your personal site `carlosp24.github.io`.

1. Create the repository (e.g. `postdoc-searcher`) and push this folder.
2. **Settings → Secrets and variables → Actions → New repository secret**:
   - `ANTHROPIC_API_KEY`
   - `SMTP_USER` (your Gmail address), `SMTP_PASSWORD` (a Gmail *App Password*, see below), `EMAIL_TO`
   - optional: `SMTP_HOST` and `SMTP_PORT` (defaults `smtp.gmail.com` and `465`), `EMAIL_FROM`
3. **Settings → Pages → Source: GitHub Actions**.
4. **Actions → Daily postdoc search → Run workflow** for the first run. After that it runs every day at 05:23 UTC, commits the updated `data/` and redeploys the dashboard.
5. Set `dashboard.url` in `config/settings.yaml` if your repository name differs. The emails link to that URL.

Not publishing a dashboard (e.g. a private repo on a free plan, where Pages isn't available)? Add the repository **variable** `PUBLISH_DASHBOARD = false`. Searches and emails still run; to see the dashboard, `git pull` and open `docs/index.html`.

### Gmail App Password
Google Account → Security → turn on 2-Step Verification → **App passwords** → create one named "Postdoc Radar" → use the 16-character password as `SMTP_PASSWORD`. Any other SMTP provider works too: set `SMTP_HOST` and `SMTP_PORT` (587 uses STARTTLS).

Check the setup with `python -m postdoc_searcher test-email`.

## Your inputs

- `inputs/pis.yaml` lists the PIs. **Give each one a `country`**, because that is what links PIs to fellowships. Add a `jobs_page` if the group has a "join us" page, and set `priority`.
- `inputs/topics.yaml` holds your topics, keywords and job-board queries, plus the title exclusions.
- `inputs/me.yaml` holds your profile and eligibility. **Fill in `phd_defense_date`, `nationality` and the dates in `residence_history`**, or the fellowship checks show "? check".
- `config/fellowships.yaml` is the fellowship catalog. Add programmes in the same format.
- `config/settings.yaml` holds the model, budgets, schedule, thresholds and sources.

Run `python -m postdoc_searcher plan` after editing. It validates everything and prints which fellowships each PI could host you for.

## Costs

The free sources cost nothing. AI usage is capped by `max_cost_per_run_usd` (default **$1.50/run**, so at most about $45/month; typical runs are lower). The dashboard's **Activity** tab shows the real cost of each run. To spend less:
- `max_search_tasks_per_run: 1–2`, or longer intervals in `schedule`
- `model: claude-sonnet-5-5`, about half the price per token
- `run --no-ai` on some days

GitHub Actions minutes are free for public repositories, and a private repository uses about 5 of its 2,000 free minutes per day.

## Privacy

On a **public** repository, everything in `inputs/` (your PIs, topics and profile) and the dashboard are public. Your email address and API keys are never in the repo; they are GitHub secrets. If you'd rather keep your PI list private, use a private repository with `PUBLISH_DASHBOARD=false` and view the dashboard locally. The dashboard sets `noindex`, so search engines won't list it.

## Commands

| Command | What it does |
|---|---|
| `python -m postdoc_searcher run` | daily run |
| `python -m postdoc_searcher plan` | validate inputs, show the next tasks, PI × fellowship matrix and cost estimate |
| `python -m postdoc_searcher site` | rebuild `docs/data.js` from `data/` |
| `python -m postdoc_searcher test-email` | send a test email |
| `.venv/bin/python -m pytest` | run the tests |

## Layout

```
inputs/                 your PIs, topics, profile
config/                 settings + fellowship catalog
postdoc_searcher/       the app (sources/, llm.py, fellowships.py, pipeline.py, …)
data/                   opportunities.json + state.json (written by the runs)
docs/                   dashboard (index.html, app.js, style.css, data.js)
.github/workflows/      daily GitHub Actions job
```

## Notes

- Dates marked **≈** are estimates from previous cycles. Always confirm deadlines on the official page.
- Claude requests use server-side refusal fallbacks (`fallbacks: "default"`). If a safety classifier declines a request, it is retried on Anthropic's recommended fallback model.
- Job-board HTML and feeds change over time. A broken source only logs a warning, and the run continues.
