# house-alert

Personal project of Thijs. **Hard boundary: nothing in this repo relates to Verity work.**
No Verity code, credentials, hostnames, ticket references, branding, or dependencies —
not in code, not in comments, not in commit messages. It is also independent of the
sibling `personal-planning` repo; they share no code and should not learn about each other.

## What it is

A daily email digest of new house listings scraped from the websites of ~87 estate
agencies in the Ghent and Kortrijk regions. Python, no services, no database.

## Shape of the code

| path | role |
| --- | --- |
| `config/agencies.yaml` | the agency registry — adding one is a config change, never code |
| `config/criteria.yaml` | what counts as a match; every field optional |
| `state/seen.json` | which listing URLs have been reported; committed to git on purpose |
| `platforms/` | discovery: which URLs on a site are listing detail pages |
| `extract.py` | one detail page → a `Listing`; JSON-LD first, Dutch heuristics second |
| `filters.py` | `Listing` + `Criteria` → keep or reject, with a reason |
| `mail/` | photo-card digest rendering and SMTP delivery |

## Conventions that matter

- **Never guess a field.** If a value can't be read off the page it stays `None`, and
  `include_unknown` in the criteria decides what that means. A wrong number is worse than a
  blank one because it silently changes what gets filtered.
- **Be polite on the wire.** `robots.txt` is respected, the User-Agent is honest, and hosts
  are rate-limited to ~1 req/s. Concurrency is per agency, never per page within one site.
- **A URL is fetched at most once, ever.** That's what makes a daily run cheap. Pages that
  fail to parse are recorded as seen too, deliberately — otherwise they're retried forever.
- **Every real-world parsing trap gets a regression test** using the markup shape that
  actually caused it. See `tests/test_location.py` and `tests/test_index_pages.py`; both
  exist because of specific pages on landbergh.be and immotion.be.
- **One broken site must never break a run.** Failures degrade to "this agency contributed
  nothing" and are reported in the summary.

## Testing

```powershell
.\.venv\Scripts\python -m pytest -q
```

Tests are offline and fast — no network, no fixtures directory, HTML inline in the test
file so the trap being guarded against is readable next to the assertion. For live checks
use `house-alert run --only <agency> --dry-run --no-seed -v`.
