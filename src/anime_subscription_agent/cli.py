"""Small CLI for manually verifying the read-only connector."""

from __future__ import annotations

import argparse
import json
import os
import sys
from .autobangumi import AutoBangumiError, AutoBangumiReadOnlyConnector
from .redaction import redact_sensitive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoBangumi 3.2.8 read-only client")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="read program status")
    subparsers.add_parser("rss", help="read RSS subscriptions")
    torrents = subparsers.add_parser("rss-torrents", help="read one RSS feed's records")
    torrents.add_argument("rss_id", type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    username = os.environ.get("AB_USERNAME")
    password = os.environ.get("AB_PASSWORD")
    if not username or not password:
        print("AB_USERNAME and AB_PASSWORD are required", file=sys.stderr)
        return 2
    connector = AutoBangumiReadOnlyConnector(
        os.environ.get("AB_BASE_URL", "http://127.0.0.1:7892"),
        expected_version=os.environ.get("AB_EXPECTED_VERSION", "3.2.8"),
    )
    try:
        connector.login(username, password)
        if args.command == "status":
            result = connector.get_status()
        elif args.command == "rss":
            result = connector.list_rss()
        else:
            result = connector.get_rss_torrents(args.rss_id)
    except (AutoBangumiError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(redact_sensitive(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
