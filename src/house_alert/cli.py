"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import filters
from .config import (
    CONFIG_DIR, ConfigError, Subscription, load_agencies, load_subscriptions,
    save_agencies_platforms,
)
from .detect import detect
from .geo import Geocoder
from .mail import MailConfigError, render_html, render_text, send, subject_for
from .models import Agency, Listing
from .net import Fetcher
from .scan import ScanResult, scan
from .site import SITE_DIR, export_site
from .store import ListingStore, SeenStore

log = logging.getLogger("house_alert")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv()


# ---------------------------------------------------------------- commands

def cmd_list(args: argparse.Namespace) -> int:
    agencies = load_agencies(regions=args.region, only=args.only)
    by_region: dict[str, list[Agency]] = {}
    for a in agencies:
        by_region.setdefault(a.region or "(geen)", []).append(a)
    for region, items in sorted(by_region.items()):
        print(f"\n{region} ({len(items)})")
        for a in items:
            print(f"  {a.name:<34} {a.platform:<12} {a.url}")
    print(f"\n{len(agencies)} agencies total")

    subs = load_subscriptions()
    print(f"\nsubscriptions ({len(subs)})")
    for sub in subs:
        who = ", ".join(sub.recipients) or "(no resolvable recipient)"
        print(f"  {sub.name:<24} -> {who}")
        for name in sub.unresolved:
            print(f"  {'':<24}    {name} is not set")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    agencies = load_agencies(regions=args.region, only=args.only)
    detected: dict[str, str] = {}
    counts: dict[str, int] = {}

    with Fetcher(delay=args.delay) as fetcher:
        for agency in agencies:
            result = detect(agency, fetcher)
            status = "unreachable" if not result.reachable else result.platform
            counts[status] = counts.get(status, 0) + 1
            print(f"{agency.name:<34} {status:<13} {result.note}")
            if result.reachable and result.platform != "auto":
                detected[agency.url] = result.platform

    print("\nsummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if args.write and detected:
        changed = save_agencies_platforms(detected)
        print(f"wrote platform for {changed} agencies into {CONFIG_DIR / 'agencies.yaml'}")
    elif detected and not args.write:
        print(f"{len(detected)} platforms recognised; re-run with --write to save them")
    return 0


def _sample_listings() -> list[Listing]:
    today = date.today().isoformat()
    return [
        Listing(
            url="https://example.be/te-koop/woning-gent-123",
            source="Voorbeeld Vastgoed", region="gent",
            title="Charmante rijwoning met zuidgerichte tuin",
            city="Gent", postal_code="9000", street="Kortrijksesteenweg 12",
            price=395000, property_type="house", bedrooms=3, living_area_m2=168,
            plot_area_m2=240, epc_kwh=248,
            image_url="https://placehold.co/600x400/e2e2dd/8a8a84?text=foto",
            description=(
                "Volledig gerenoveerde rijwoning op wandelafstand van het centrum, "
                "met ruime living, aangelegde tuin en zolder met uitbreidingspotentieel."
            ),
            matched_keywords=["tuin", "zuidgericht"],
            first_seen=today, lat=51.0362, lon=3.7110, geo="address",
        ),
        Listing(
            url="https://example.be/te-koop/woning-kortrijk-456",
            source="Immo Voorbeeld Kortrijk", region="kortrijk",
            title="Te renoveren woning met 4 slaapkamers",
            city="Kortrijk", postal_code="8500", price=289000,
            property_type="house", bedrooms=4, living_area_m2=205,
            epc_kwh=512,
            description="Ruime woning met veel potentieel, centraal gelegen.",
            matched_keywords=["te renoveren"],
            first_seen=today, lat=50.828, lon=3.2649, geo="postcode",
        ),
        Listing(
            url="https://example.be/te-koop/woning-deinze-789",
            source="Derde Kantoor", region="gent",
            title="Nieuwbouwvilla met open bebouwing",
            city="Deinze", postal_code="9800",
            property_type="house", bedrooms=4, living_area_m2=232, plot_area_m2=680,
            first_seen=today, lat=50.9833, lon=3.5333, geo="postcode",
        ),
        Listing(
            url="https://example.be/te-koop/appartement-gent-321",
            source="Voorbeeld Vastgoed", region="gent",
            title="Lichtrijk appartement met terras",
            city="Gent", postal_code="9000", price=265000,
            property_type="apartment", bedrooms=2, living_area_m2=95,
            first_seen="2026-10-01", lat=51.05, lon=3.7167, geo="postcode",
        ),
    ]


def cmd_preview(args: argparse.Namespace) -> int:
    listings = _sample_listings()
    html = render_html(
        listings, run_date=date.today(), agency_count=87,
        candidate_count=214, scan_errors=3, label="Voorbeeld",
    )
    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html):,} bytes)")
    print("\n--- plain text alternative ---\n")
    print(render_text(listings, date.today()))
    return 0


def cmd_test_mail(args: argparse.Namespace) -> int:
    listings = _sample_listings()
    run_date = date.today()
    subs = load_subscriptions()
    targets = [s for s in subs if s.deliverable]
    if not targets:
        print("no subscription has a resolvable recipient; set MAIL_TO or edit "
              "config/subscriptions/", file=sys.stderr)
        return 2
    for sub in targets:
        html = render_html(listings, run_date, agency_count=87, candidate_count=214,
                           label=sub.name)
        text = render_text(listings, run_date)
        try:
            send("[test] " + subject_for(listings, run_date, sub.name), html, text,
                 recipients=sub.recipients)
        except MailConfigError as exc:
            print(f"mail not configured: {exc}", file=sys.stderr)
            return 2
        print(f"test email sent to {', '.join(sub.recipients)} ({sub.name})")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    out_dir = Path(args.out) if args.out else SITE_DIR
    if args.sample:
        store = ListingStore(path=Path("unused"), listings={})
        store.add_all(_sample_listings())
    else:
        store = ListingStore.load()
        with Geocoder.load(online=False) as geocoder:
            for listing in store.listings.values():
                geocoder.locate(listing)
    agencies = load_agencies()
    target = export_site(store, out_dir, agency_count=len(agencies))
    print(f"wrote {target} ({len(store)} listings)")
    return 0


def cmd_geocode(args: argparse.Namespace) -> int:
    store = ListingStore.load()
    pending = store.needing_address_lookup()
    print(f"{len(store)} listings, {len(pending)} with a street still to resolve")
    with Geocoder.load(max_requests=args.max) as geocoder:
        resolved = 0
        for listing in store.listings.values():
            geocoder.locate(listing)
        for listing in pending:
            if geocoder.budget_left == 0:
                break
            if geocoder.locate(listing) and listing.geo == "address":
                resolved += 1
                if args.verbose:
                    print(f"  {listing.location_display:<48} {listing.lat:.5f},{listing.lon:.5f}")
        geocoder.save()
    store.save()
    print(f"resolved {resolved} addresses with {geocoder.requests_made} lookups")
    export_site(store, agency_count=len(load_agencies()))
    return 0


def _locate(geocoder: Geocoder, fresh: list[Listing], store: ListingStore) -> tuple[int, int]:
    """Place today's listings, then spend what is left of the lookup budget on
    older ones still waiting for a street-level point."""
    exact = 0
    for listing in fresh:
        geocoder.locate(listing)
        exact += listing.geo == "address"
    backlog = 0
    for listing in store.needing_address_lookup():
        if geocoder.budget_left == 0:
            break
        if listing in fresh:
            continue
        if geocoder.locate(listing) and listing.geo == "address":
            backlog += 1
    for listing in store.listings.values():
        if listing.lat is None:
            geocoder.locate(listing)
    return exact, backlog


def _report(result: ScanResult, per_sub: dict[str, tuple[list[Listing], list[filters.Verdict]]],
            verbose: bool) -> None:
    print(f"\nagencies scanned : {len(result.per_agency)}")
    print(f"unreachable      : {len(result.unreachable)}")
    print(f"URLs discovered  : {result.discovered}")
    print(f"pages fetched    : {result.fetched}")
    print(f"listings parsed  : {len(result.listings)}")
    for name, (matches, _) in per_sub.items():
        print(f"matched '{name}'{'':<{max(0, 8 - len(name))}}: {len(matches)}")

    if verbose and result.unreachable:
        print("\nno listings found on:")
        for agency in result.unreachable:
            print(f"  {agency.name} ({agency.url})")

    for name, (matches, rejected) in per_sub.items():
        if verbose and rejected:
            print(f"\nfiltered out of '{name}':")
            for verdict in rejected[:60]:
                listing = verdict.listing
                print(f"  {listing.price_display:>14}  {verdict.reason:<38} {listing.url}")
            if len(rejected) > 60:
                print(f"  ... and {len(rejected) - 60} more")
        if matches:
            print(f"\nmatches for '{name}':")
            for listing in matches:
                print(f"  {listing.price_display:>14}  {listing.location_display:<32} "
                      f"{(listing.title or '')[:40]:<40} {listing.url}")


def cmd_run(args: argparse.Namespace) -> int:
    subscriptions = load_subscriptions()
    agencies = load_agencies(regions=args.region, only=args.only)
    seen = SeenStore.load()
    store = ListingStore.load()
    today = date.today()

    for sub in subscriptions:
        for name in sub.unresolved:
            print(f"subscription '{sub.name}': {name} is not set, so it cannot be delivered")

    seeding = args.seed or seen.is_empty
    if seeding and not args.seed:
        print(
            "State file is empty, so this is a first run: every listing currently "
            "online is recorded and put on the overview page, but nothing is "
            "emailed. Tomorrow's run reports only what appeared since. Use "
            "--no-seed to email them all."
        )
    if args.no_seed:
        seeding = False

    with Fetcher(delay=args.delay, respect_robots=not args.ignore_robots) as fetcher:
        result = scan(
            agencies, seen, fetcher,
            max_listings=args.limit,
            workers=args.workers,
        )

    fresh = result.listings
    for listing in fresh:
        listing.first_seen = today.isoformat()
    store.add_all(fresh, today)

    with Geocoder.load(online=not args.dry_run, max_requests=args.geocode_max) as geocoder:
        exact, backlog = _locate(geocoder, fresh, store)
        if not args.dry_run:
            geocoder.save()
    print(f"located          : {exact} of {len(fresh)} new at street level, "
          f"{backlog} older resolved, {geocoder.requests_made} lookups")

    per_sub = {sub.name: filters.apply(fresh, sub.criteria) for sub in subscriptions}
    _report(result, per_sub, verbose=args.verbose)

    if args.out:
        union: dict[str, Listing] = {}
        for matches, _ in per_sub.values():
            for listing in matches:
                union.setdefault(listing.id, listing)
        html = render_html(list(union.values()), today, len(agencies), len(fresh),
                           len(result.unreachable))
        Path(args.out).write_text(html, encoding="utf-8")
        print(f"\nwrote {args.out}")

    if args.dry_run:
        print("\ndry run: nothing emailed, state not updated, site not exported")
        return 0

    if seeding:
        print(f"\nseeded {len(fresh)} listings from {len(result.per_agency)} agencies; "
              f"no email on a first run")
    elif not args.no_email:
        _deliver(subscriptions, fresh, today, len(agencies), len(result.unreachable))

    for url in result.all_seen_urls:
        seen.record(Listing(url=url, source="scan"))
    pruned = seen.prune()
    seen.save()
    store.prune()
    store.save()
    target = export_site(store, agency_count=len(agencies), unreachable=len(result.unreachable))
    print(f"state: {len(seen)} seen URLs ({pruned} pruned), {len(store)} listings on {target}")
    return 0


def _deliver(subscriptions: list[Subscription], fresh: list[Listing], today: date,
             agency_count: int, unreachable: int) -> None:
    for sub in subscriptions:
        # Re-evaluate per subscription: matched_keywords live on the listing
        # and must reflect this subscription's highlight words.
        matches, _ = filters.apply(fresh, sub.criteria)
        if not matches:
            print(f"'{sub.name}': nothing new matched; no email")
            continue
        if not sub.deliverable:
            print(f"'{sub.name}': {len(matches)} matches but no recipient could be "
                  f"resolved; nothing sent", file=sys.stderr)
            continue
        html = render_html(matches, today, agency_count, len(fresh), unreachable,
                           label=sub.name)
        text = render_text(matches, today)
        try:
            send(subject_for(matches, today, sub.name), html, text, recipients=sub.recipients)
            print(f"'{sub.name}': emailed {len(matches)} listings to {', '.join(sub.recipients)}")
        except MailConfigError as exc:
            print(f"'{sub.name}': mail not configured, nothing sent: {exc}", file=sys.stderr)


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="house-alert",
        description="Daily email digest and map of new house listings from Ghent- "
                    "and Kortrijk-area estate agencies.",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="explain what was filtered out and why")
    parser.add_argument("--debug", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_selectors(p: argparse.ArgumentParser) -> None:
        p.add_argument("--region", action="append",
                       help="only this region tag (repeatable): gent, kortrijk")
        p.add_argument("--only", action="append",
                       help="only agencies whose name contains this (repeatable)")
        p.add_argument("--delay", type=float, default=1.0,
                       help="seconds between requests to the same host (default 1.0)")

    p_run = sub.add_parser("run", help="scan, locate, email, export the site")
    add_selectors(p_run)
    p_run.add_argument("--dry-run", action="store_true",
                       help="scan and report, but send nothing and save nothing")
    p_run.add_argument("--no-email", action="store_true", help="scan and save, don't email")
    p_run.add_argument("--seed", action="store_true",
                       help="force seeding: record everything online, email nothing")
    p_run.add_argument("--no-seed", action="store_true",
                       help="on an empty state file, email everything found instead of seeding")
    p_run.add_argument("--limit", type=int, default=80,
                       help="max listing URLs to consider per agency (default 80)")
    p_run.add_argument("--workers", type=int, default=8,
                       help="agencies scanned concurrently (default 8)")
    p_run.add_argument("--geocode-max", type=int, default=300,
                       help="max Nominatim lookups this run (default 300)")
    p_run.add_argument("--ignore-robots", action="store_true",
                       help="do not consult robots.txt (off by default; be considerate)")
    p_run.add_argument("--out", help="also write the digest HTML to this file")
    p_run.set_defaults(func=cmd_run)

    p_detect = sub.add_parser("detect", help="fingerprint each agency's listing platform")
    add_selectors(p_detect)
    p_detect.add_argument("--write", action="store_true",
                          help="save detected platforms into agencies.yaml")
    p_detect.set_defaults(func=cmd_detect)

    p_list = sub.add_parser("list", help="show the agency registry and subscriptions")
    add_selectors(p_list)
    p_list.set_defaults(func=cmd_list)

    p_preview = sub.add_parser("preview", help="render the email with sample data")
    p_preview.add_argument("--out", default="preview.html")
    p_preview.set_defaults(func=cmd_preview)

    p_export = sub.add_parser("export", help="rewrite docs/listings.json from the state")
    p_export.add_argument("--out", help="directory to write into (default docs/)")
    p_export.add_argument("--sample", action="store_true",
                          help="write sample listings instead, to try the page locally")
    p_export.set_defaults(func=cmd_export)

    p_geo = sub.add_parser("geocode", help="resolve stored street addresses to map points")
    p_geo.add_argument("--max", type=int, default=300, help="max lookups (default 300)")
    p_geo.set_defaults(func=cmd_geocode)

    p_test = sub.add_parser("test-mail", help="send the sample email to every subscription")
    p_test.set_defaults(func=cmd_test_mail)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else (
            logging.INFO if args.verbose else logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
