#!/usr/bin/env python3
"""
CLI interface for Google Hotels Scraper.
Usage:
  python cli.py -l "Guwahati" -ci 2026-05-09 -co 2026-05-10 -m 0 -o results.json
"""
import argparse
import json
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.scraper import scrape_hotels

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Google Hotels Scraper - Reverse-engineered API-based scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape all pages for Guwahati (default)
  python cli.py -l "Guwahati" -ci 2026-05-09 -co 2026-05-10 -o results.json

  # Scrape 5 pages only
  python cli.py -l "Mumbai" -ci 2026-05-05 -co 2026-05-08 -m 5 -o mumbai.json

  # With SOCKS5 proxy for rate limiting
  python cli.py -l "Delhi" -ci 2026-05-05 -co 2026-05-08 --proxy "socks5://127.0.0.1:9050"

  # With all parameters
  python cli.py -l "Kochi" -ci 2026-05-05 -co 2026-05-09 -a 2 -ch 1 --children-ages 5 -c INR --lang en-GB -m 10 -o kochi.json --raw-output kochi_raw.json
"""
    )

    parser.add_argument("-l", "--location", required=True, help="City/location name (e.g. Guwahati, Mumbai)")
    parser.add_argument("-ci", "--check-in", required=True, help="Check-in date YYYY-MM-DD")
    parser.add_argument("-co", "--check-out", required=True, help="Check-out date YYYY-MM-DD")
    parser.add_argument("-a", "--adults", type=int, default=2, help="Number of adults (default: 2)")
    parser.add_argument("-ch", "--children", type=int, default=0, help="Number of children (default: 0)")
    parser.add_argument("--children-ages", default="", help="Comma-separated child ages e.g. '5,8,10' (1-17)")
    parser.add_argument("-c", "--currency", default="USD", help="Currency code (default: USD)")
    parser.add_argument("--lang", default="en", help="Language code (default: en)")
    parser.add_argument("-m", "--max-pages", type=int, default=0, help="Max pages to scrape (0=all based on total results)")
    parser.add_argument("-t", "--timeout", type=int, default=60, help="Request timeout seconds (default: 60)")
    parser.add_argument("-o", "--output", help="Output JSON file path (clean/deduplicated)")
    parser.add_argument("--raw-output", help="Output JSON file path (raw, before deduplication)")
    parser.add_argument("-p", "--proxy", help="SOCKS5 proxy URL e.g. socks5://user:pass@host:port")

    args = parser.parse_args()

    logger.info(f"Scraping Google Hotels for: {args.location}")
    logger.info(f"  Check-in: {args.check_in}, Check-out: {args.check_out}")
    logger.info(f"  Adults: {args.adults}, Children: {args.children}")
    logger.info(f"  Currency: {args.currency}, Language: {args.lang}")
    logger.info(f"  Max pages: {'all (based on total results)' if args.max_pages == 0 else args.max_pages}")

    result = scrape_hotels(
        location=args.location,
        check_in=args.check_in,
        check_out=args.check_out,
        adults=args.adults,
        children=args.children,
        children_ages=args.children_ages,
        currency=args.currency,
        language=args.lang,
        max_pages=args.max_pages,
        proxy_url=args.proxy,
        timeout=args.timeout,
    )

    raw_counts = result.pop("_raw_counts", {})
    pages_scraped = raw_counts.get("pages_scraped", 0)

    if args.raw_output:
        raw_result = {
            "pagination": result["pagination"],
            "ads": result.get("ads", []) + [a for a in result.get("ads", [])],
            "brands": result.get("brands", []),
            "properties": result.get("properties", []),
        }
        with open(args.raw_output, "w") as f:
            json.dump(raw_result, f, indent=2, default=str)
        logger.info(f"Raw results saved to {args.raw_output}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Results saved to {args.output}")

    ad_count = len(result.get("ads", []))
    prop_count = len(result.get("properties", []))
    total = result.get("pagination", {}).get("total_results")
    print(f"Found {ad_count} ads + {prop_count} properties (scraped {pages_scraped} page(s))")
    if total:
        print(f"Total results from Google: {total}")


if __name__ == "__main__":
    main()
