"""One-time desk setup operations; never provide this token to a research session."""
from __future__ import annotations

import argparse
import json
import os

import httpx

from kalshi_bot.desks.doctor import validate_connection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "preflight", "test-alerts", "start"))
    args = parser.parse_args(argv)
    try:
        endpoint, token = validate_connection(os.environ.get("DESK_SERVICE_URL", ""),
                                              os.environ.get("DESKS_OPERATOR_TOKEN", ""))
        base = endpoint.removesuffix("/api/status")
        with httpx.Client(timeout=180, follow_redirects=False) as client:
            headers = {"Authorization": "Bearer " + token}
            if args.command == "status":
                response = client.get(endpoint, headers=headers)
            else:
                path = {"preflight": "/api/round/preflight", "test-alerts": "/api/alerts/test",
                        "start": "/api/round/start"}[args.command]
                response = client.post(base + path, json={}, headers=headers)
            response.raise_for_status()
            result = response.json()
            print(json.dumps(result, indent=2))
            if args.command == "preflight" and result.get("ready") is not True:
                return 2
        return 0
    except Exception:
        parser.exit(1, "Desk operator request failed; inspect authenticated status before retrying.\n")


if __name__ == "__main__":
    raise SystemExit(main())
