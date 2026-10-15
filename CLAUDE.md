# house-alert

Personal project of Thijs. **Hard boundary: nothing in this repo relates to Verity work.**
No Verity code, credentials, hostnames, ticket references, branding, or dependencies —
not in code, not in comments, not in commit messages. It is also independent of the
sibling `personal-planning` repo; they share no code and should not learn about each other.

## What it is

New house listings scraped from the websites of ~87 estate agencies in the Ghent and
Kortrijk regions, shown on a GitHub Pages site (list + map) and emailed daily per
subscription. Python, no services, no database; the state is JSON files in git.

## Shape of the code

| path | role |
| --- | --- |
| `config/agencies.yaml` | the agency registry — adding one is a config change, never code |
| `config/subscriptions/*.yaml` | one daily email each: recipients (`$SECRET` refs) + criteria |
| `state/seen.json` | which listing URLs have ever been fetched; committed on purpose |
| `state/listings.json` | the parsed listings of the last 180 days, with coordinates |
| `state/geocache.json` | Nominatim answers, including misses, so nothing is asked twice |
| `docs/` | the Pages site: `index.html` (static app) + `listings.json` (exported each run) |
| `platforms/` | discovery: which URLs on a site are listing detail pages |
| `extract.py` | one detail page → a `Listing`; JSON-LD first, Dutch heuristics second |
| `geo.py` | street → Nominatim point; else postcode / city centroid from bundled table |
| `filters.py` | `Listing` + `Criteria` → keep or reject, with a reason |
| `mail/` | photo-card digest rendering and SMTP delivery |
| `site.py` | `ListingStore` → `docs/listings.json` |

## Conventions that matter

- **Never guess a field.** If a value can't be read off the page it stays `None`, and
  `include_unknown` in the criteria decides what that means. A wrong number is worse than a
  blank one because it silently changes what gets filtered. Coordinates follow the same
  rule: `geo` says whether a point is the house (`address`), the postcode centroid, or the
  city, and the page draws the approximate ones differently.
- **The street must be the house's, never the agency's.** Office addresses are excluded
  three ways: JSON-LD Organization blocks, page chrome (footer/header/contact regions),
  and any street shared by most of one agency's listings in a run (`scan.strip_shared_street`).
- **Be polite on the wire.** `robots.txt` is respected, the User-Agent is honest, and hosts
  are rate-limited to ~1 req/s. Nominatim: ≥1 s between requests, capped per run,
  answers cached. Concurrency is per agency, never per page within one site.
- **A URL is fetched at most once for extraction.** That's what makes a run cheap. Pages
  that fail to parse are recorded as seen too, deliberately — otherwise they're retried
  forever. The one exception is `scan.recheck_status`: stored listings are re-visited
  every couple of days (capped per run) purely to read their status banner, because a
  sold house keeps its page. Fields are never re-extracted on that visit.
- **Mail covers the queue, not the run.** `Listing.mailed` is set by a mailing run; the
  midnight and noon runs pass `--no-email` and leave new listings queued for the morning.
- **Email addresses never go in files.** The repo is public (Pages on a free plan).
  Subscriptions say `$MAIL_TO`; the workflow maps each recipient secret to an env var by
  name. Never pass `toJSON(secrets)`: GitHub's workflow protections hold such runs for
  manual approval, which silently kills the schedule.
- **Every real-world parsing trap gets a regression test** using the markup shape that
  actually caused it. See `tests/test_location.py`, `tests/test_index_pages.py`,
  `tests/test_street.py` and the price-then-postcode case in `tests/test_extract.py`.
- **One broken site must never break a run.** Failures degrade to "this agency contributed
  nothing" and are reported in the summary.

## Testing

```powershell
.\.venv\Scripts\python -m pytest -q
```

Tests are offline and fast — no network, HTML inline in the test file so the trap being
guarded against is readable next to the assertion. For live checks use
`house-alert run --only <agency> --dry-run --no-seed -v`. The page's script can be
syntax-checked with `node --check` after extracting it; there is no JS test suite.
