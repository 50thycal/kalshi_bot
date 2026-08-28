"""Operator entry point for the mmsell10 Stage-1 live canary.

TWO SEPARATE ACTS, TWO SEPARATE APPROVALS, AND NEITHER OF THEM STARTS TRADING.

    register   create v2 (single arm + pre-registered risk envelope), register the
               promotion and keep/stop gates, freeze, open v2/e1 on the active
               platform snapshot, and hand the `mmsell10` tag over to it.
               Registers a CONTRACT. Places no order and arms nothing.

    arm        run `service.arm_live_canary` — transition to LIVE_CANARY and
               register the live deployment and its paper twin at one instant on
               fresh tags. This is the act that EXPANDS REAL-MONEY CAPABILITY and
               it requires `--approved-by`.

Even after `arm`, no order can reach Kalshi until the runtime allowlist is set
separately (`BOT_MODE=live`, `KILL_SWITCH=false`, `LIVE_ENABLED=true`, and the
live tag in `LIVE_STRATEGIES`). That switch is a Live Ops act through the `env`
channel, deliberately not automated here: a script that could both arm the
canary and open the allowlist would be one command away from unreviewed
exposure.

    # inspect, writing nothing (the default):
    DATABASE_URL=postgresql://... python scripts/mmsell10_canary.py register
    # execute, on a WRITABLE connection:
    DATABASE_URL=postgresql://... python scripts/mmsell10_canary.py register --execute
    DATABASE_URL=postgresql://... python scripts/mmsell10_canary.py arm --execute \
        --approved-by "<operator>"

This script is deliberately NOT in the ops runner's allowlist. That channel is
read-only against Postgres by design and must stay that way; registering and
arming are writes and belong on an operator's own writable connection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalshi_bot.experiment_os import canary_mmsell10 as pkg  # noqa: E402
from kalshi_bot.experiment_os import read  # noqa: E402


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kalshi_bot.experiment_os.models  # noqa: F401 — register tables
    import kalshi_bot.experiment_os.service  # noqa: F401 — install the flush guard

    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        raise SystemExit("DATABASE_URL is required (a WRITABLE connection)")
    if "DATABASE_URL_RO" in os.environ and url == os.environ["DATABASE_URL_RO"]:
        raise SystemExit(
            "DATABASE_URL is the read-only connection — registration and arming "
            "are writes and must not be attempted through the read path"
        )
    return sessionmaker(bind=create_engine(url))()


#: Prose fields printed under their own headings rather than in the value table.
_PROSE_KEYS = ("settings", "enforced_by", "stand_down", "portfolio_breaker")


def _describe_envelope() -> None:
    print("=== Stage-1 risk envelope (pre-registered on the version) ===")
    for key, value in pkg.RISK_ENVELOPE.items():
        if key in _PROSE_KEYS:
            continue
        print(f"  {key:38s} {value}")
    print("\n  runtime settings this envelope sets:")
    for key, value in pkg.RISK_ENVELOPE["settings"].items():
        print(f"    {key:38s} {value}")
    print(f"\n  stand-down: {pkg.RISK_ENVELOPE['stand_down']}")
    print(f"\n  portfolio breaker: {pkg.RISK_ENVELOPE['portfolio_breaker']}")
    print("\n=== thresholds with no repository precedent, and what was decided ===")
    for name, why in pkg.OPERATOR_DECISIONS.items():
        print(f"  - {name}\n      {why}")


def cmd_register(args) -> int:
    _describe_envelope()
    print("\n=== gates that would be registered ===")
    print("  promotion:", json.dumps(pkg.PROMOTION_GATE_SPEC, indent=2)[:2000])
    print("  keep/stop:", json.dumps(pkg.KEEP_GATE_SPEC, indent=2)[:4000])
    if not args.execute:
        print("\nDRY RUN — nothing written. Re-run with --execute on a writable "
              "connection to register.")
        return 0
    session = _session()
    try:
        out = pkg.register_successor_version(
            session, actor=args.actor,
            promotion_sample_floor=args.promotion_sample_floor,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    ver, epoch = out["version"], out["epoch"]
    print(f"\nREGISTERED  v{ver.version} frozen {ver.frozen_at}  "
          f"epoch e{epoch.epoch_number} started {epoch.started_at}")
    print(f"  arm: {out['arm_key']}   paper deployment: "
          f"{out['paper_deployment'].deployment_key}")
    print("  NOTHING IS ARMED. Real-money capability requires `arm --execute`, "
          "and trading additionally requires the runtime allowlist.")
    return 0


def cmd_arm(args) -> int:
    if not args.approved_by:
        raise SystemExit("--approved-by is required: arming expands real-money "
                         "capability and the approval is recorded")
    print("=== ARM — this expands real-money capability ===")
    print(f"  live tag {pkg.LIVE_TAG}  twin tag {pkg.TWIN_TAG}  "
          f"(fresh; refused if either carries prior paper rows)")
    _describe_envelope()
    if not args.execute:
        print("\nDRY RUN — nothing written.")
        return 0
    session = _session()
    try:
        res = pkg.arm(session, approved_by=args.approved_by, actor=args.actor)
        session.commit()
    except Exception:
        session.rollback()
        raise
    live, twin, epoch = res["live"], res["twin"], res["epoch"]
    print(f"\nARMED  live={live.deployment_key} twin={twin.deployment_key} "
          f"boundary={live.started_at} epoch=e{epoch.epoch_number} "
          f"({epoch.impact_class})")
    print("  The runtime allowlist is still whatever it was. No order can reach "
          "Kalshi until LIVE_STRATEGIES names the live tag.")
    return 0


def cmd_status(args) -> int:
    session = _session()
    exp = read.get_experiment(session, pkg.EXPERIMENT_KEY)
    if exp is None:
        print(f"{pkg.EXPERIMENT_KEY}: not registered")
        return 1
    ver = read.latest_version(session, exp)
    print(f"{exp.key}: state={exp.state} latest=v{ver.version if ver else '-'} "
          f"frozen={getattr(ver, 'frozen_at', None)}")
    if ver is not None:
        print(f"  arms: {sorted(a.arm_key for a in read.arms_for(session, ver))}")
        print(f"  risk envelope registered: {bool(ver.risk_json)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--actor", default="operator")
    ap.add_argument("--execute", action="store_true",
                    help="actually write; omit for a dry run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="create v2 + gates + epoch (no arming)")
    p_reg.add_argument("--promotion-sample-floor", type=int, default=None,
                       help="add a settled-trade floor to the promotion gate; "
                            "omitted, the gate is registered UNFLOORED (n=0), "
                            "which is v1's literal contract and the operator's "
                            "2026-08-28 decision")
    p_reg.set_defaults(fn=cmd_register)

    p_arm = sub.add_parser("arm", help="arm the live canary and its twin")
    p_arm.add_argument("--approved-by", default=None)
    p_arm.set_defaults(fn=cmd_arm)

    sub.add_parser("status", help="what is registered right now").set_defaults(
        fn=cmd_status)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
