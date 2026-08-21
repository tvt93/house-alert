"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import filters
from .config import CONFIG_DIR, ConfigError, load_agencies, load_criteria, save_agencies_platforms
from .detect import detect
from .mail import MailConfigError, render_html, render_text, send, subject_for
from .models import Agency, Listing
from .net import Fetcher
from .scan import ScanResult, scan
from .store import SeenStore

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
    return [
        Listing(
            url="https://example.be/te-koop/woning-gent-123",
            source="Voorbeeld Vastgoed", region="gent",
            title="Charmante rijwoning met zuidgerichte tuin",
            city="Gent", postal_code="9000", price=395000,
            property_type="house", bedrooms=3, living_area_m2=168,
            plot_area_m2=240, epc_kwh=248,
            image_url="https://placehold.co/600x400/e2e2dd/8a8a84?text=foto",
            description=(
                "Volledig gerenoveerde rijwoning op wandelafstand van het centrum, "
                "met ruime living, aangelegde tuin en zolder met uitbreidingspotentieel."
            ),
            matched_keywords=["tuin", "zuidgericht"],
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
        ),
        Listing(
            url="https://example.be/te-koop/woning-deinze-789",
            source="Derde Kantoor", region="gent",
            title="Nieuwbouwvilla met open bebouwing",
            city="Deinze", postal_code="9800",
            property_type="house", bedrooms=4, living_area_m2=232, plot_area_m2=680,
        ),
    ]


def cmd_preview(args: argparse.Namespace) -> int:
    listings = _sample_listings()
    html = render_html(
        listings, run_date=date.today(), agency_count=87,
        candidate_count=214, scan_errors=3,
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
    html = render_html(listings, run_date, agency_count=87, candidate_count=214)
    text = render_text(listings, run_date)
    try:
        send("[test] " + subject_for(listings, run_date), html, text)
    except MailConfigError as exc:
        print(f"mail not configured: {exc}", file=sys.stderr)
        return 2
    print("test email sent")
    return 0


def _report(result: ScanResult, matches: list[Listing],
            rejected: list[filters.Verdict], verbose: bool) -> None:
    print(f"\nagencies scanned : {len(result.per_agency)}")
    print(f"unreachable      : {len(result.unreachable)}")
    print(f"URLs discovered  : {result.discovered}")
    print(f"pages fetched    : {result.fetched}")
    print(f"listings parsed  : {len(result.listings)}")
    print(f"matched criteria : {len(matches)}")

    if verbose and result.unreachable:
        print("\nno listings found on:")
        for agency in result.unreachable:
            print(f"  {agency.name} ({agency.url})")

    if verbose and rejected:
        print("\nfiltered out:")
        for verdict in rejected[:60]:
            listing = verdict.listing
            print(f"  {listing.price_display:>14}  {verdict.reason:<38} {listing.url}")
        if len(rejected) > 60:
            print(f"  ... and {len(rejected) - 60} more")

    if matches:
        print("\nmatches:")
        for listing in matches:
            print(f"  {listing.price_display:>14}  {listing.location_display:<18} "
                  f"{(listing.title or '')[:46]:<46} {listing.url}")


def cmd_run(args: argparse.Namespace) -> int:
    criteria = load_criteria()
    agencies = load_agencies(regions=args.region, only=args.only)
    store = SeenStore.load()

    seeding = args.seed or store.is_empty
    if seeding and not args.seed:
        print(
            "State file is empty, so this is a first run: recording every listing "
            "currently online as already-seen and sending no email. Tomorrow's run "
            "reports only what appeared since. Use --no-seed to email them all."
        )
    if args.no_seed:
        seeding = False

    with Fetcher(delay=args.delay, respect_robots=not args.ignore_robots) as fetcher:
        result = scan(
            agencies, store, fetcher,
            max_listings=args.limit,
            workers=args.workers,
            discover_only=seeding,
        )

    if seeding:
        for url in result.all_seen_urls:
            store.record(Listing(url=url, source="seed"))
        if not args.dry_run:
            store.save()
        print(f"seeded {len(result.all_seen_urls)} listing URLs from "
              f"{len(result.per_agency)} agencies; state now holds {len(store)} entries")
        if result.unreachable:
            print(f"{len(result.unreachable)} agencies yielded nothing - "
                  f"run with -v to see which")
        return 0

    matches, rejected = filters.apply(result.listings, criteria)
    _report(result, matches, rejected, verbose=args.verbose)

    run_date = date.today()
    if args.out:
        html = render_html(matches, run_date, len(agencies), len(result.listings),
                           len(result.unreachable))
        Path(args.out).write_text(html, encoding="utf-8")
        print(f"\nwrote {args.out}")

    if args.dry_run:
        print("\ndry run: nothing emailed, state not updated")
        return 0

    if matches and not args.no_email:
        html = render_html(matches, run_date, len(agencies), len(result.listings),
                           len(result.unreachable))
        text = render_text(matches, run_date)
        try:
            send(subject_for(matches, run_date), html, text)
            print(f"\nemailed {len(matches)} listings")
        except MailConfigError as exc:
            print(f"\nmail not configured, nothing sent: {exc}", file=sys.stderr)
            return 2
    elif not matches:
        print("\nnothing new matched; no email sent")

    # Everything resolved this run counts as seen, including pages that failed
    # to parse - otherwise they are re-fetched every single day.
    for url in result.all_seen_urls:
        store.record(Listing(url=url, source="scan"))
    pruned = store.prune()
    store.save()
    print(f"state: {len(store)} entries ({pruned} pruned)")
    return 0


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="house-alert",
        description="Daily email digest of new house listings from Ghent- and "
                    "Kortrijk-area estate agencies.",
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

    p_run = sub.add_parser("run", help="scan, filter, email")
    add_selectors(p_run)
    p_run.add_argument("--dry-run", action="store_true",
                       help="scan and report, but send nothing and save nothing")
    p_run.add_argument("--no-email", action="store_true", help="scan and save, don't email")
    p_run.add_argument("--seed", action="store_true",
                       help="force seeding: mark everything online as seen, email nothing")
    p_run.add_argument("--no-seed", action="store_true",
                       help="on an empty state file, email everything found instead of seeding")
    p_run.add_argument("--limit", type=int, default=80,
                       help="max listing URLs to consider per agency (default 80)")
    p_run.add_argument("--workers", type=int, default=8,
                       help="agencies scanned concurrently (default 8)")
    p_run.add_argument("--ignore-robots", action="store_true",
                       help="do not consult robots.txt (off by default; be considerate)")
    p_run.add_argument("--out", help="also write the digest HTML to this file")
    p_run.set_defaults(func=cmd_run)

    p_detect = sub.add_parser("detect", help="fingerprint each agency's listing platform")
    add_selectors(p_detect)
    p_detect.add_argument("--write", action="store_true",
                          help="save detected platforms into agencies.yaml")
    p_detect.set_defaults(func=cmd_detect)

    p_list = sub.add_parser("list", help="show the agency registry")
    add_selectors(p_list)
    p_list.set_defaults(func=cmd_list)

    p_preview = sub.add_parser("preview", help="render the email with sample data")
    p_preview.add_argument("--out", default="preview.html")
    p_preview.set_defaults(func=cmd_preview)

    p_test = sub.add_parser("test-mail", help="send the sample email via SMTP")
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
