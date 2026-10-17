# Insider Cluster

Live at https://insidercluster.net. A free website showing which executives and directors are buying their own company's stock, built from SEC Form 4 filings. It updates itself every weekday and costs nothing to run.

## How it works

1. **Fetch** (`pipeline/edgar.py`): reads SEC's daily index of filings, then downloads each Form 4. SEC requires a User-Agent with your name and email, and allows up to 10 requests per second; the pipeline uses about 8.
2. **Parse** (`pipeline/parse.py`): turns each filing's XML into rows: who traded, their role, shares, price, holdings after, whether it was a pre-scheduled 10b5-1 plan trade.
3. **Score** (`pipeline/signals.py`): keeps only open-market buys (code P) and sells (code S), combines each filing into one trade, then scores buys:

   | Signal | Points |
   |---|---|
   | $1M+ purchase / $250K+ / $50K+ | 3 / 2 / 1 |
   | CEO or CFO / President, COO or Chair / other officer or director | 2 / 1.5 / 1 |
   | Opens a new position or more than doubles holdings / raises holdings 20%+ | 2 / 1 |
   | Three or more insiders bought the same stock in 30 days (a "cluster") | 2 |
   | Pre-scheduled 10b5-1 plan | −1 |

   6+ points is **High**, 4+ is **Medium**. Each buy shows the reasons behind its score.
4. **Publish** (`site/index.html`): a single static page that reads `site/data/insiders.json`.
5. **Automate** (`.github/workflows/update.yml`): GitHub Actions runs everything each weekday morning, saves new filing days in `data/days/`, and deploys the site to GitHub Pages.

## Set it up on GitHub (about 15 minutes)

1. Create a new **public** repository on GitHub (public repos get free Actions minutes and free Pages hosting) and push this folder to it.
2. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
   Name: `SEC_USER_AGENT`. Value: your name and an email, e.g. `Jane Smith jane@example.com`.
3. Go to **Settings → Pages** and set **Source** to **GitHub Actions**.
4. Go to **Actions → Update insider data → Run workflow**, and enter `35` for days. The first run downloads about five weeks of filings and takes roughly 1–2 hours. Later daily runs take a few minutes.
5. When it finishes, your site is live at `https://<your-username>.github.io/<repo-name>/`.

To use your own domain later, add it under **Settings → Pages → Custom domain**.

## Run it on your own computer

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name you@example.com"   # Windows: set SEC_USER_AGENT=...
python -m pipeline.run update --days 3 --max-filings 25   # quick test, nothing saved
python -m pipeline.run update --days 7
python -m pipeline.run build
cd site && python -m http.server 8000                    # open http://localhost:8000
```

Offline preview with made-up sample data:

```bash
python tests/make_sample.py tests/fixtures 2026-10-15
python -m pipeline.run demo tests/fixtures --as-of 2026-10-15
```

Tests: `pytest`

## Known limits in this first version

- Amended filings (Form 4/A) are skipped, so a corrected filing keeps its original numbers.
- Only common-stock transactions are scored; option exercises, grants, gifts and tax withholding are excluded on purpose because they don't reflect a decision to buy.
- When several insiders file jointly, the first one listed gets credit.
- The site isn't investment advice, and the footer says so. Keep that disclaimer.

## Ideas for next versions

- A page per ticker (`/t/NWRB`) so each stock can show up in search results.
- A daily email or RSS feed of high-signal buys.
- Price performance after each buy (needs a licensed price source to display commercially).
