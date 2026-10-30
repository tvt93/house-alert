# house-alert

Personal project. **No connection to Verity work of any kind.** Also unrelated to
`personal-planning` — separate repo, separate purpose, nothing shared.

Every house that comes online at ~87 estate agencies around Ghent and Kortrijk, on one
page — as a list and on a map — plus one email every morning per recipient with the new
ones that match their filters. It reads the agencies' own websites directly, so listings
show up as soon as the agency publishes them, usually before they reach the big portals.

- **Overview page:** `https://<owner>.github.io/house-alert/` (GitHub Pages, served from `docs/`)
- **Daily mail:** one file per recipient in `config/subscriptions/`

## How it works

```
config/agencies.yaml   87 agencies around Ghent and Kortrijk
        │
        ├─ discover     robots.txt → sitemap.xml → listing detail URLs
        │               (falls back to crawling the "te koop" index)
        │
        ├─ skip known   state/seen.json — a URL is only ever fetched once
        │
        ├─ extract      schema.org JSON-LD if the site emits it, otherwise
        │               Dutch-language heuristics; street address when the
        │               page prints one (and it isn't the agency's own)
        │
        ├─ locate       street → Nominatim (OpenStreetMap), cached in
        │               state/geocache.json; otherwise postcode centroid
        │
        ├─ store        state/listings.json — everything seen in 180 days
        │
        ├─ email        per subscription: filter, render photo cards, SMTP
        │
        └─ export       docs/listings.json → the overview page
```

The key economy is `state/seen.json`: a listing page is fetched exactly once, ever. The
first run records everything currently online (and puts it on the page) without emailing
anyone; after that a daily run only pays for pages that genuinely appeared since
yesterday.

## The overview page

`docs/index.html` is a static page that loads `docs/listings.json`. No build step, no
framework — Leaflet for the map, everything else is plain HTML.

- **Lijst / Kaart** toggle. The map clusters pins; a solid pin is a street address, a
  dashed one is the postcode (or municipality) centroid because the agency did not
  print the street.
- **Filters:** price, postcode (exact or prefix: `85` = 8500–8599), type, plot area,
  habitable area, bedrooms, how recently it appeared, agency. Unknown values are
  included by default; untick the box to make every filter strict. A garage or
  parking space sold on its own is its own type (*Garage*), hidden by default.
- Filters live in the URL, so a filtered view can be bookmarked or sent to someone.
- **Dagelijkse mail** turns the current filters into a daily mail right from the page: a
  name for the selection, an e-mail address, done. No GitHub account needed; see
  *Setting up a mail from the page* below for the one-time wiring.

## Daily mail: subscriptions

Each file in `config/subscriptions/` is one email. `default.yaml` documents every field.

```yaml
name: Thijs
to:
  - $MAIL_TO            # a GitHub secret / env var holding the address
property_types: [house]
price: { min: 250000, max: 500000, include_unknown: true }
bedrooms: { min: 3, include_unknown: true }
living_area_m2: { min: 120, include_unknown: true }
plot_area_m2: { min: 300, include_unknown: true }
postcodes: ["9000", "90", "9820"]
```

Two things worth knowing:

- **Recipients never appear in clear.** This repository is public (GitHub Pages on a
  free plan requires it), so a recipient is either a `$NAME` reference to an Actions
  secret (the workflow maps `MAIL_TO`, `MAIL_TO_PARTNER`, `MAIL_TO_2` and `MAIL_TO_3`
  into the run; another name needs one extra line in `daily.yml`) or an `enc:...` token:
  the address sealed in the browser with the site's public key, which only the mail run
  can open with the `MAIL_KEY` secret. Files written from the page use the second form.
  A literal address also works if you don't mind it being visible.
- **`include_unknown`** decides what happens when a field can't be read off the page. Not
  every agency publishes the EPC or the plot size, and `true` keeps those listings rather
  than silently dropping them. Set it to `false` for anything non-negotiable. The
  `postcodes` filter has no such switch: a listing without a readable postcode never
  passes it.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env      # then fill in the Gmail app password
```

For Gmail you need an **app password** ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
requires 2-step verification) — your normal Google password will be rejected.

## Daily use

```powershell
.\.venv\Scripts\house-alert run                      # the real thing
.\.venv\Scripts\house-alert -v run --dry-run         # scan, explain, send nothing
.\.venv\Scripts\house-alert run --only Immotion      # one agency, for debugging
.\.venv\Scripts\house-alert geocode                  # resolve stored streets to map points
.\.venv\Scripts\house-alert export                   # rewrite docs/listings.json from state
.\.venv\Scripts\house-alert export --sample --out x  # sample data to try the page locally
.\.venv\Scripts\house-alert preview                  # write preview.html, no network
.\.venv\Scripts\house-alert test-mail                # prove SMTP works, per subscription
.\.venv\Scripts\house-alert list                     # agencies and subscriptions
.\.venv\Scripts\house-alert detect --write           # fingerprint each site's platform
.\.venv\Scripts\house-alert forget --only Rosseel     # refetch one agency after a scraper fix
```

`-v --dry-run` is the one to reach for when the email looks wrong: it prints every listing
that was filtered out, per subscription, with the exact reason.

To try the page locally: `house-alert export --sample --out site`, copy `docs/index.html`
next to the generated `listings.json`, and serve the folder (`python -m http.server`).

## Is it still for sale?

A listing page is normally fetched once, but a house sold yesterday still has a page.
So every run re-visits up to 400 stored listings whose status is more than two days
old and reads the banner on the page: *verkocht*, *in optie*, *onder bod*, *compromis in
opmaak*, *onder voorbehoud*... A page that 404s or redirects up to the index is *gone*.
The page shows the status on the card and on the pin, filters on "Beschikbaar" by
default, and forgets sold or vanished listings a month after that was established.

## Scheduling and hosting

`.github/workflows/daily.yml` runs three times a day on GitHub's runners (UTC times):

| when | what |
| --- | --- |
| 23:00 (around midnight in Brussels) | scan, re-check, refresh the page |
| 06:15 (morning) | the same, **and send the mails** |
| 11:00 (around noon in Brussels) | scan, re-check, refresh the page |

A mail covers everything found since the previous mail, so nothing found at midnight
or noon is lost; it waits in the queue for the morning. The ↻ button on the page opens
the workflow on GitHub, where *Run workflow* starts a refresh on demand (tick *send_mail*
to mail right away). Each run commits the updated `state/` and `docs/listings.json` back
to the repo, and GitHub Pages redeploys the page from `docs/` on its own.

One-time setup on GitHub:

1. Repository **Settings → Pages**: source *Deploy from a branch*, branch `main`,
   folder `/docs`.
2. **Settings → Secrets and variables → Actions**, add:

| secret | value |
| --- | --- |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the app password |
| `MAIL_TO` | where the default digest goes |
| `MAIL_TO_PARTNER`, `MAIL_TO_2`, `MAIL_TO_3` | other recipients a subscription refers to (optional) |
| `MAIL_KEY` | private key from `house-alert keygen`; opens the addresses sealed by the page |
| `SMTP_HOST`, `SMTP_PORT`, `MAIL_FROM` | optional; sensible defaults |

3. Run the workflow once by hand from the Actions tab. The first run seeds the state and
   fills the page; from the next morning on, the mails go out.

### Setting up a mail from the page

The page is static, so something has to commit the subscription file on its behalf. That
something is `worker/subscribe.js`: a Cloudflare Worker (free tier, one file, no build
step) that validates the request, renders the YAML and commits it with a token that can
touch nothing but this repository's contents. The address is encrypted in the browser
before it leaves, so neither the Worker nor the repository ever holds it readably.

1. **Keys.** `house-alert keygen` prints a pair. The public one goes into
   `SUBSCRIBE_KEY` in `docs/index.html`; the private one becomes the Actions secret
   `MAIL_KEY`. Keep it: addresses sealed with the public key can only be opened with it.
2. **GitHub token.** *Settings → Developer settings → Fine-grained tokens*: this repository
   only, permission *Contents: read and write*, nothing else. Note the expiry (a year at
   most) and renew it in the Worker when it lapses.
3. **Worker.** From the `worker/` folder (needs a free Cloudflare account; the first
   command opens the browser to log in):

   ```powershell
   npx wrangler login
   npx wrangler deploy                   # prints the Worker URL
   npx wrangler secret put GITHUB_TOKEN  # paste the token from step 2
   ```

   `wrangler.toml` already sets `GITHUB_REPO`, `ALLOWED_ORIGIN` and `MAX_SUBSCRIPTIONS`
   (default 20: the total number of files the Worker will ever create, since anyone who
   finds the page can use the button). The dashboard route works too: *Workers & Pages →
   Create*, paste `subscribe.js`, add the same variables under *Settings → Variables*.
4. **Page.** Put the Worker's URL in `SUBSCRIBE_URL` next to `SUBSCRIBE_KEY`, commit.
   Until both are filled the dialog says the feature is not active yet.

Each click creates `config/subscriptions/<name>.yaml` (a repeated name gets `-2`, `-3`).
Stopping a mail is deleting its file; the next morning run is the first that mails it.

The first run fetches every listing currently online (up to 80 new pages per agency per
run, so a site with a long sitemap catches up over a few runs) and takes
15–30 minutes. Street-level geocoding is capped at 300 lookups per run, so pins refine
from postcode to street over the first few days.

## Adding an agency

One line in `config/agencies.yaml`:

```yaml
  - { name: Nieuw Kantoor, url: "https://www.nieuwkantoor.be" }
```

No code. The generic scraper reads the sitemap and follows the site's own "te koop" /
"aanbod" links from the homepage, which covers nearly every agency. Two optional keys:

- `platform: omnicasa | skarabee | whise | sweepbright` when `house-alert detect` recognises
  one; it teaches discovery that platform's URL shapes (Omnicasa keeps the property id in
  the query string, Skarabee ends the path in the property number).
- `postcodes: ["8", "9"]` for chains that sell across the country: only listings whose
  postcode starts with one of the prefixes are kept (and URLs that carry another postcode are
  never fetched). A listing without a readable postcode is kept, never guessed away - unless
  the town it names can only mean postcodes elsewhere ("Antwerpen" is never 8 or 9).
- `index_urls: [...]` for a site whose sitemap and homepage never lead to the listings: these
  pages are read for listing links before anything else. Engel & Völkers (`platform:
  engelvoelkers`, one Next.js site for the whole country) is the one that needs it: its
  catalogue is a set of region and town index pages, newest first, and its detail pages keep
  every fact in a data blob rather than in the page text. The agency's own map point, which it
  states it moved by a few hundred metres, is kept as `geo: approx` ("± omgeving" on the page).

Sites that render their listings purely in JavaScript stay empty; `config/agencies.yaml`
marks the known ones in a comment. A site that was fixed after it had been scanned needs
`house-alert forget --only <name>` once, so its pages are fetched again.

## Being a good citizen

These are small businesses' websites, not a CDN. The scraper reads `robots.txt` and obeys
it, identifies itself honestly in the User-Agent, and makes at most one request per second
per host. Nominatim gets the same treatment: one request per second, an identifying
User-Agent, every answer cached. Please don't raise `--workers` or drop `--delay`.

## Known limitations

- **JavaScript-only sites** return nothing useful; there is no headless browser here, by
  choice. Those agencies show up as contributing zero listings; `-v` names them.
- **Extraction is heuristic** on sites without JSON-LD. Bedrooms, EPC, plot size and the
  street are the fields most often missing; price and location are reliable. A field that
  can't be read stays blank rather than guessed, and a listing without a street gets a
  dashed pin at its postcode.
- **The agency's own address** is filtered out (page footer, JSON-LD organisation block,
  and any street that most of one agency's listings share), but a new site could print
  its office somewhere unexpected. Symptom: several listings from one agency on one pin.
- **No price-drop tracking.** A listing is reported once, when it first appears.
