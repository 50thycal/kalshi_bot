#!/usr/bin/env python3
"""Operator-only ChatGPT exchange write smoke. Run inside desk-service."""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kalshi_bot.desks.config import DeskSettings
from kalshi_bot.desks.contracts import DeskError
from kalshi_bot.desks.exchange import KalshiDeskExchange
from kalshi_bot.desks.smoke import run_chatgpt_smoke
from kalshi_bot.desks.store import DeskStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or run the one-shot ChatGPT 1-cent IOC write-path smoke"
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--side", required=True, choices=("yes", "no"))
    parser.add_argument(
        "--execute",
        action="store_true",
        help="place the single live IOC; without this flag the command is read-only",
    )
    args = parser.parse_args(argv)
    exchange = None
    try:
        settings = DeskSettings()
        if not settings.live_enabled:
            raise DeskError("live_execution_disabled")
        if settings.account_mode != "isolated":
            raise DeskError("isolated_account_mode_required")
        exchange = KalshiDeskExchange(
            settings.kalshi_base_url,
            settings.chatgpt_kalshi_key_id.get_secret_value(),
            settings.chatgpt_kalshi_private_key.get_secret_value(),
            settings.chatgpt_subaccount,
        )
        store = DeskStore(settings.database_url.get_secret_value())
        result = run_chatgpt_smoke(
            store, exchange, args.ticker, args.side, execute=args.execute
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        report = result.get("report")
        if report and (
            report["status"] != "terminal"
            or Decimal(str(report["filled_quantity"])) != 0
        ):
            return 3
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, DeskError) else type(exc).__name__
        print(json.dumps({"ok": False, "error": code}), file=sys.stderr)
        return 2
    finally:
        if exchange is not None:
            exchange.close()


if __name__ == "__main__":
    raise SystemExit(main())
