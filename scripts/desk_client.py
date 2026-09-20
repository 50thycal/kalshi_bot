"""Authenticated session bridge; credentials only via environment, never GitHub ops.

DESK_SERVICE_URL=https://... DESK_SESSION_TOKEN=... python scripts/desk_client.py status
python scripts/desk_client.py --desk chatgpt claim --worker-id scheduled-chatgpt
python scripts/desk_client.py --desk claude complete --file research-result.json

The JSON file for complete contains job_id, claim_token, model_id and payload.
Session scheduling must be supplied by a supported external runner; this CLI
does not turn an inactive chat into a persistent process.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desk", choices=("chatgpt", "claude"))
    parser.add_argument("command", choices=("status", "ready", "continue", "claim", "source", "complete", "publications", "decisions"))
    parser.add_argument("--worker-id")
    parser.add_argument("--file", type=Path)
    args = parser.parse_args(argv)
    url = os.environ.get("DESK_SERVICE_URL", "").rstrip("/")
    token = os.environ.get("DESK_SESSION_TOKEN", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        parser.error("DESK_SERVICE_URL must use HTTPS (loopback HTTP allowed for development)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not token:
        parser.error("use a plain service URL and DESK_SESSION_TOKEN environment value")
    if args.command != "status" and not args.desk:
        parser.error("--desk is required for a write")
    try:
        body = json.loads(args.file.read_text()) if args.file else {}
        if args.worker_id:
            body["worker_id"] = args.worker_id
        headers = {"Authorization": "Bearer " + token}
        with httpx.Client(timeout=180, follow_redirects=False) as client:
            if args.command == "status":
                response = client.get(url + "/api/status", headers=headers)
            else:
                response = client.post(f"{url}/api/desks/{args.desk}/{args.command}", json=body, headers=headers)
            response.raise_for_status()
            print(json.dumps(response.json(), indent=2))
    except Exception:
        parser.exit(1, "Desk request failed; check service status and role credentials.\n")


if __name__ == "__main__":
    main()
