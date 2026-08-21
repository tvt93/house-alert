# house-alert

Personal project. **No connection to Verity work of any kind.** Also unrelated to
`personal-planning` — separate repo, separate purpose, nothing shared.

Every morning, one email: the houses that came online yesterday at estate agencies around
Ghent and Kortrijk and match the criteria you set. It reads the agencies' own websites
directly, so listings show up here as soon as the agency publishes them — usually before
they reach the big portals.

## How it works

```
config/agencies.yaml   87 agencies, Ghent + Kortrijk regions
        │
        ├─ discover     robots.txt → sitemap.xml → listing detail URLs
        │               (falls back to crawling the "te koop" index)
        │
        ├─ skip known   state/seen.json — a URL is only ever fetched once
        │
        ├─ extract      schema.org JSON-LD if the site emits it, otherwise
        │               Dutch-language heuristics over the page
        │
        ├─ filter       config/criteria.yaml
        │
        └─ email        photo-card digest over SMTP
```

The key economy is `state/seen.json`: a listing page is fetched exactly once, ever. The
first run records everything currently online without emailing you, and after that a
daily run only pays for pages that genuinely appeared since yesterday — a few dozen
fetches, not a few thousand.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env      # then fill in the Gmail app password
```

For Gmail you need an **app password** ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
requires 2-step verification) — your normal Google password will be rejected.

## Setting your criteria

`config/criteria.yaml` ships with placeholders that match almost nothing, so a first run
stays quiet until you fill it in. Every field is optional; leaving one blank skips that
check entirely.

Two things worth knowing:

- **`include_unknown`** decides what happens when a field can't be read off the page. Not
  every agency publishes the EPC or the plot size, and `include_unknown: true` keeps those
  listings rather than silently dropping them. Set it to `false` for anything you consider
  non-negotiable.
- **`municipalities.include`** matters more than you'd expect. Several agencies in the
  registry (Kleyo, Immo Francois, Dewaele) sell across the whole country, so without a
  municipality filter you'll get houses in Antwerp and Knokke too. Leave it empty until
  you've seen a day's output, then narrow it.

## Daily use

Everything runs through one command:

```powershell
.\.venv\Scripts\house-alert run                      # the real thing
.\.venv\Scripts\house-alert -v run --dry-run         # scan, explain, send nothing
.\.venv\Scripts\house-alert run --region kortrijk    # one region only
.\.venv\Scripts\house-alert run --only Immotion      # one agency, for debugging
.\.venv\Scripts\house-alert preview                  # write preview.html, no network
.\.venv\Scripts\house-alert test-mail                # prove SMTP works
.\.venv\Scripts\house-alert list                     # show the registry
.\.venv\Scripts\house-alert detect --write           # fingerprint each site's platform
```

`-v --dry-run` is the one to reach for when the email looks wrong: it prints every listing
that was filtered out and the exact reason, which is usually enough to spot a criterion set
one notch too tight.

## Scheduling

`.github/workflows/daily.yml` runs it at 06:15 UTC on GitHub's runners, so it does not
matter whether your laptop is on. Push the repo (**private**), then add these repository
secrets under *Settings → Secrets and variables → Actions*:

| secret | value |
| --- | --- |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the app password |
| `MAIL_TO` | where the digest goes |
| `SMTP_HOST`, `SMTP_PORT`, `MAIL_FROM` | optional; sensible defaults |

The workflow commits the updated `state/seen.json` back to the repo after each run — that
file *is* the memory of what you've already been shown, so it belongs in version control.
You can trigger a run by hand from the Actions tab, and each run uploads the rendered
digest as an artifact so you can see exactly what was sent.

## Adding an agency

One line in `config/agencies.yaml`:

```yaml
  - { name: Nieuw Kantoor, url: "https://www.nieuwkantoor.be", region: gent }
```

No code. The generic scraper handles any site that publishes a sitemap or a browsable
"te koop" index, which is nearly all of them. `house-alert detect --write` then records
which platform each site runs on (Whise, SweepBright, Omnicasa, Skarabee), and
`platforms/` uses that to pick better URL patterns.

## Being a good citizen

These are small businesses' websites, not a CDN. The scraper reads `robots.txt` and obeys
it, identifies itself honestly in the User-Agent, and makes at most one request per second
per host. Because seen URLs are never re-fetched, a steady-state daily run is a handful of
requests per agency — far less traffic than one person browsing the site. Please don't
raise `--workers` or drop `--delay` much; there's nothing to gain and it would be rude.

## Known limitations

- **JavaScript-only sites** return nothing useful. There is no headless browser here, by
  choice — it would turn a 30-second job into a heavy one. Those agencies show up as
  contributing zero listings; `-v` names them.
- **Extraction is heuristic** on sites without JSON-LD. Bedrooms, EPC and plot size are the
  fields most often missing; price and location are reliable. A field that can't be read
  stays blank rather than guessed.
- **Category pages** — some agencies publish per-municipality landing pages at the same URL
  shape as real listings. These are detected and skipped (see `is_index_page`), but a new
  site could invent a shape that slips through. Symptom: a listing whose price doesn't
  match its photo.
- **No price-drop tracking.** A listing is reported once, when it first appears. Watching
  for reductions on listings you've already seen would be the obvious next feature.
