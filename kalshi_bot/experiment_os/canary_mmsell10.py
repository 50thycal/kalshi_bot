"""The mmsell10 live-canary package: contract, risk envelope, gates, arming.

This module is DATA plus two functions. It places no orders, arms nothing on
import, and is not wired into the trading worker or the read-only ops channel.
Nothing here runs until an operator executes `scripts/mmsell10_canary.py
--execute` against a writable connection, and even then step 2 (`arm`) is a
separate, separately-approved call.

WHY A SUCCESSOR VERSION EXISTS AT ALL
-------------------------------------
The brief asked for a canary on `mmsell-price-ceiling` at its *current* version
and epoch. Experiment OS refuses that, for two independent structural reasons —
both read off production, both reproduced as tests in
`tests/test_mmsell10_canary_package.py`:

1. **v1 carries no pre-registered risk envelope.** `arm_live_canary` requires
   `version.risk_json`; v1 has none, and v1 froze at 2026-08-16T14:14:43.720928Z.
   The flush guard refuses every edit to a frozen version, because the approved
   canary envelope is part of the scientific contract, not configuration bolted
   on afterwards.
2. **v1 declares two arms — `mmsell9` and `mmsell10`.** `arm_live_canary`
   requires the live and twin tag maps to equal the declared arm set *exactly*,
   so a canary on v1 would have to put mmsell9 on real money too. mmsell9 is the
   arm whose observed paper economics are NEGATIVE (-0.596c/trade at n=581); the
   brief's own selection reasoning excludes it.

A changed arm set is a new Version by the system's own rule (`add_arm` says so on
a frozen version), and a risk envelope can only be pre-registered on one. So v2
is not a workaround: it is what "register this canary" means here.

WHAT v2 COSTS, STATED PLAINLY
-----------------------------
Evidence windows floor at `max(epoch start, gate evidence start)`. v2/e1 opens
now, so **v2's evidence starts at zero** and the 2026-08-23 PASS on v1/e1 cannot
be inherited. That is the honest price of a contract that can carry a risk
envelope and a single arm.

The operator's decision of 2026-08-28 is to register v1's bar with **no evidence
floor** (n = 0), so the promotion gate can clear on a thin fresh sample rather
than waiting out a rebuilt one. `PROMOTION_GATE_SPEC` states what that means and
what to read at arming time before approving the arm.

WHAT IS **NOT** CHANGING
------------------------
mmsell10's market universe, `lo=5 / hi=10 / maxyes=7`, entry timing, sizing
logic, settlement behaviour, fee model, order type and risk semantics are
carried across verbatim. In particular **no crypto exclusion is added**: that
would be a different universe and could not inherit this arm's evidence at all.
Crypto is a reported monitoring slice (`scripts/mmsell_canary_slices.py`), never
a stopping criterion, because it is not pre-registered as one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import service
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentDeployment, ExperimentEpoch, ExperimentGate, ExperimentVersion
from .read import arms_for, get_experiment, latest_version

EXPERIMENT_KEY = "mmsell-price-ceiling"
ARM_KEY = "mmsell10"
PAPER_TAG = "mmsell10"

#: Fresh live/twin tags. Three constraints shaped them, all load-bearing:
#:   * FRESH — no `paper_trades` history and no active deployment arm, or
#:     `arm_live_canary` refuses (the 2026-08-15 lesson: mmsell10 armed on a tag
#:     holding 87 open paper positions never placed a single live order);
#:   * PREFIX-SAFE — `LIVE_STRATEGIES` matches by PREFIX, so a tag beginning
#:     `mmsell10` would be captured by an allowlist entry naming the paper parent.
#:     `C` is this canary's generation marker, as `L` was the last one's;
#:   * the twin tag fits `paper_trades.strategy` (24 chars) and is refused real
#:     orders by `LiveExecutor._allowed`, which rejects every configured twin tag
#:     outright rather than relying on the prefix match.
LIVE_TAG = "Cmmsell10"
#: NOT `_pt`. The twin tag is DERIVED at runtime as `<live_tag><suffix>` from
#: `LIVE_PAPER_TWIN_SUFFIX`, which production carries as `_pt3` (the generation
#: cut at the 2026-08-11 fee re-baseline). Registering `Cmmsell10_pt` would have
#: put a tag in Experiment OS that the twin book never trades under: the twin's
#: paper rows would carry `Cmmsell10_pt3`, which resolves to no active deployment
#: arm, and under NEW_ONLY the write path refuses an unregistered tag — a canary
#: armed with a twin that cannot record anything. Read off production
#: 2026-08-28; `test_the_twin_tag_matches_what_the_runtime_derives` pins it.
TWIN_TAG = "Cmmsell10_pt3"

LIVE_DEPLOYMENT_KEY = "mmsell-ceiling-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-ceiling-twin-1"
#: v2/e1's paper deployment, and the replacement v1/e1 deployment that keeps
#: mmsell9 admissible after the legacy two-arm deployment is closed out.
V2_PAPER_DEPLOYMENT_KEY = "mmsell-ceiling-paper-2"
V1_MMSELL9_DEPLOYMENT_KEY = "mmsell-ceiling-paper-mmsell9-1"
LEGACY_PAPER_DEPLOYMENT_KEY = "mmsell-ceiling-paper-legacy-1"

#: The book's runtime spec. `size=1` is the per-book live contract cap and is
#: deliberately here rather than in `MAX_ORDER_SIZE`: a book param is covered by
#: the config-drift material (`book_params`), so a later change to the clip is
#: DETECTED, while the process-wide setting is not watched by the detector at all.
#: It also scopes the cap to this book instead of every book sharing the process.
BOOK_PARAMS = "lo=5,hi=10,maxyes=7,size=1"

#: The `mmsell_variants` entry that CREATES the live book. A live mmsell book is
#: an ordinary variant entry — `Lmmsell8` and `Lmmsell10` both are — so without
#: this the canary's `LIVE_STRATEGIES=Cmmsell10` would name a book that does not
#: exist: no orders, and `book_params[Cmmsell10]` absent against a declared
#: value, which `enforcement.runtime_config_check` records as
#: EXPERIMENT_CONFIG_DRIFT and which takes the keep gate to BLOCKED_INTEGRITY.
#:
#: The TWIN needs no entry of its own. `MmSellTracker._twin_books` builds it as
#: `dict(parent)` with the tag replaced, and `live_paper_twin_pairs` derives the
#: pairing from `LIVE_STRATEGIES` + `LIVE_PAPER_TWIN_SUFFIX` while
#: `live_paper_twin_auto` is on — which is exactly why `material_config` records
#: the twin's `book_params` as None rather than as a spec.
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"

PROMOTION_GATE_KEY = "paper_to_live_canary"
KEEP_GATE_KEY = "live_canary_keep"


# ---------------------------------------------------------------------------
# The Stage-1 risk envelope
# ---------------------------------------------------------------------------

#: Every number here is a RUNTIME setting except where marked. The envelope is
#: pre-registered on the version (`risk_json`), so it freezes with the contract
#: and a later retune is a recorded change rather than an edit.
#:
#: Sizing arithmetic, since it is not obvious: the book BUYS NO at
#: `100 - yes_ask`, and `maxyes=7` caps the yes side at 7c — so a contract costs
#: 93c to 99c. `order_quantity` floors `max_order_dollars / price`, so a $1.00
#: per-order cap yields exactly one contract at every price the ceiling admits.
#: One clip is therefore ~$0.93-$0.99 and the worst case for a single market is
#: the loss of one clip.
RISK_ENVELOPE: dict = {
    "stage": "canary_stage_1",
    "contracts_per_order": 1,
    "max_order_dollars": 1.00,
    "max_market_exposure_usd": 1.00,
    "max_event_rungs": 3,
    "max_event_exposure_usd": 3.00,
    "max_open_positions": 20,
    "max_book_exposure_usd": 19.80,
    "max_events_per_settlement_date": 5,
    "settlement_date_concentration_pct": 25,
    "daily_realized_loss_stop_usd": 5.00,
    "total_canary_loss_budget_usd": 15.00,
    "order_timeout_seconds": 14_400,
    "entry_price_offset_cents": 0,
    "exit_policy": "hold to settlement; no TP/SL — structural for mmsell, not a "
                   "setting (docs/MMSELL_EXIT_STUDY.md)",
    "settings": {
        "LIVE_MAX_ORDER_DOLLARS": "1.0",
        "MAX_MARKET_EXPOSURE": "1.0",
        "MAX_DAILY_LOSS": "5.0",
        "LIVE_KILL_ON_DAILY_LOSS": "true",
        "MMSELL_LIVE_MAX_OPEN_POSITIONS": "20",
        "MMSELL_LIVE_PRICE_OFFSET_CENTS": "0",
        "MMSELL_EVENT_RUNG_CAP_ENABLED": "true",
        "MMSELL_EVENT_RUNG_CAP": "3",
        "MMSELL_SETTLEMENT_CAP_ENABLED": "true",
        "MMSELL_SETTLEMENT_CAP_PCT": "0.25",
        "MMSELL_SETTLEMENT_EVENT_CAP": "5",
        "LIVE_ORDER_TIMEOUT_SECONDS": "14400",
        "LIVE_PAPER_TWIN_SUFFIX": "_pt3",
        "MMSELL_PREFILTER_ENABLED": "false",
    },
    #: Settings this envelope deliberately does NOT touch, and why. Each was in an
    #: earlier draft and was removed after reading production on 2026-08-28.
    "left_alone": {
        "MAX_TOTAL_EXPOSURE": "portfolio-wide; see portfolio_breaker below. "
                              "Production carries 100, so the canary's own ~$19.80 "
                              "ceiling binds first with ample headroom over the "
                              "~$17 of legacy stood-down holdings.",
        "MAX_ORDER_SIZE": "process-wide, and NOT watched by the config-drift "
                          "detector. The clip is set per book as `size=1` in "
                          "BOOK_PARAMS instead, where a later change is detected.",
        "LIVE_EXIT_MODE": "production carries tp_sl, which is correct for the "
                          "YES/weather books and is what their held positions "
                          "exit under. mmsell holds to settlement structurally — "
                          "the TP/SL knobs are documented as YES-side only — so "
                          "forcing it to `settlement` would change another "
                          "family's exits and buy this canary nothing.",
    },
    "enforced_by": {
        "contracts_per_order": "the book's own `size=1` (drift-checked) through "
                               "live/sizing.order_quantity, under the "
                               "LIVE_MAX_ORDER_DOLLARS belt",
        "max_market_exposure_usd": "LiveExecutor._market_exposure (gate:exposure)",
        "max_event_rungs": "MmSellTracker event-rung cap (skip_event_rung_cap)",
        "max_open_positions": "repo.count_live_book_open (gate:open_cap)",
        "max_book_exposure_usd": "the open-position cap times one clip — this "
                                 "book's own ceiling, and the only exposure "
                                 "limit this envelope sets",
        "max_events_per_settlement_date": "MmSellTracker settlement caps "
                                          "(skip_event_cap / skip_settlement_cap)",
        "daily_realized_loss_stop_usd": "LiveExecutor._daily_loss_hit "
                                        "(gate:daily_loss)",
        "total_canary_loss_budget_usd": "NOT a runtime breaker — the "
                                        "live_canary_keep gate's "
                                        "live_realized_pnl_usd clause, actioned "
                                        "by an operator stand-down",
        "order_timeout_seconds": "LiveExecutor timeout-cancel of resting orders",
    },
    "stand_down": (
        "Emptying LIVE_STRATEGIES stops NEW entries on the next cycle; resting "
        "orders drain within a cycle; HELD positions keep exiting and settling "
        "normally, and remain real money. Enforcement records "
        "EXPERIMENT_EXECUTION_STOOD_DOWN (informational, non-blocking) rather "
        "than config drift. The twin stands down with live, because twin pairs "
        "are derived from LIVE_STRATEGIES — which preserves the one-to-one "
        "property the comparison depends on."
    ),
    "portfolio_breaker": (
        "MAX_TOTAL_EXPOSURE is deliberately NOT part of this envelope, by "
        "operator decision 2026-08-28: the canary limits the positions it opens, "
        "not the money other books already hold. It is left at whatever value "
        "production carries and is neither tightened nor loosened here — "
        "tightening it around ~$17 of legacy stood-down holdings would size a "
        "SHARED breaker to one book's needs, and loosening it would weaken a "
        "live safeguard to make this canary easier to arm.\n\n"
        "It still applies as a shared backstop: LiveExecutor._total_exposure_hit "
        "is portfolio-wide, so if total open exposure ever reaches the cap this "
        "canary is refused NEW entries (gate:total_exposure) alongside every "
        "other book. That is a fail-safe, not a defect — it blocks entries only, "
        "and exits, closeouts and reconciliation always run. The canary's own "
        "ceiling is max_book_exposure_usd, which binds first at these caps."
    ),
}


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

#: v1's promotion gate, carried across VERBATIM — same metric, same bar, and (by
#: operator decision 2026-08-28) **no evidence floor**. `arm: "*"` resolves to the
#: single declared arm on v2.
#:
#: A 300-trade floor was proposed and DECLINED in favour of n = 0, which is v1's
#: literal registered contract: the legacy manifest records no explicit n and
#: deliberately invented none. Registering a floor now would have been a new
#: pre-registration choice made by this session rather than a restatement of the
#: operator's, and the operator chose the restatement.
#:
#: The consequence, stated plainly because it is the whole risk of n = 0: v2/e1's
#: evidence starts at zero and this gate can PASS on a thin fresh sample. It is
#: not literally satisfiable on one trade — `realizable_cents_per_trade` is
#: MISSING until a trusted fill-calibration cell covers the book's price mix, and
#: MISSING is BLOCKED_DATA rather than a pass — but it can clear in hours rather
#: than days. Read the arming-time value against `fill_model_coverage_pct` and
#: the sample it rests on before approving the arm; the gate will not do that for
#: you. `register_successor_version(promotion_sample_floor=N)` adds a floor if
#: that judgement later changes, and doing so is a new Version, not an edit.
PROMOTION_GATE_SPEC: dict = {
    "description": (
        "v1's registered bar, unchanged and unfloored: the calibrated live-fill "
        "projection must be positive. This is NOT a claim that observed paper "
        "P&L is positive — on v1 it was +0.232c/trade against a +1.286c/trade "
        "projection."
    ),
    "pass_all": [
        {"metric": "realizable_cents_per_trade", "arm": "*", "op": ">", "value": 0},
    ],
}

#: The pre-registered keep/stop contract, registered BEFORE arming so no
#: threshold can be chosen after seeing a result.
#:
#: It separates the four outcomes the brief names, using the evaluator's own
#: verdict order (BLOCKED_* > HORIZON_EXHAUSTED > early-safety FAIL > sample HOLD
#: > FAIL > PASS > HOLD):
#:
#:   1. INSUFFICIENT EVIDENCE -> HOLD. Keep running inside the envelope. This is
#:      the `sample` floor, and it is deliberately NOT the floor the safety
#:      clauses use: one number for both would make a catastrophe at a fifth of
#:      the promotion sample sit at HOLD while real money kept trading.
#:   2. EXECUTION / ACCOUNTING FAILURE -> FAIL via an early-safety clause with
#:      its own `min_evidence`. Stand down and investigate.
#:   3. STRATEGY LOSS -> FAIL on the pre-registered loss budget.
#:   4. SUCCESSFUL EVIDENCE -> PASS, which authorizes NOTHING. A promotion is a
#:      separate operator act through its own gate.
#:
#: Two conditions the brief lists are handled STRUCTURALLY rather than by a
#: clause, and are better served that way:
#:   * unexpected parameter drift — `runtime_config_check` records
#:     EXPERIMENT_CONFIG_DRIFT and the evaluator returns BLOCKED_INTEGRITY, so a
#:     drifted book cannot render any verdict at all, let alone a passing one;
#:   * stale or missing twin evidence — every twin metric returns MISSING (never
#:     zero) and the evaluator returns BLOCKED_DATA.
#:
#: And one is handled by the envelope rather than an invented number: "excessive
#: tail losses". At a one-contract clip a settled market can lose at most one clip
#: (~$0.99), so a tail-COUNT threshold adds nothing the loss budget does not
#: already bound — 15 net-losing clips IS the budget. What is registered instead
#: is a STRUCTURAL severity clause: a single settled market losing more than
#: $1.00 means sizing, the hold-to-settlement assumption or the accounting is
#: wrong, and that is a stand-down regardless of the running total.
KEEP_GATE_SPEC: dict = {
    "description": (
        "Keep/stop for the mmsell10 Stage-1 canary. Every clause addresses "
        "deployment_kind='live' explicitly; the twin comparisons resolve through "
        "the registered twin_of link, never a tag suffix."
    ),
    "sample": {
        ARM_KEY: {
            "metric": "live_settled_contracts",
            "deployment_kind": "live",
            "op": ">=",
            "value": 150,
        }
    },
    # The horizon addresses its scope as explicitly as every other clause. Left
    # implicit it defaults to `arm: none / kind: paper`, which on a live-only
    # epoch resolves to an empty paper scope and takes the whole gate to
    # BLOCKED_DATA — the exact defect that has `mmsell-scheduled-settle-live`
    # unjudgeable today, reproduced here by an unaddressed horizon.
    "max_evidence_horizon": {
        "metric": "live_settled_contracts", "value": 600,
        "arms": [ARM_KEY], "deployment_kind": "live",
    },
    "fail_any": [
        # --- 3. strategy loss: the pre-registered budget -------------------
        {
            "metric": "live_realized_pnl_usd", "arm": ARM_KEY,
            "deployment_kind": "live", "op": "<=", "value": -15.0,
            "min_evidence": {
                "metric": "live_settled_contracts", "op": ">=", "value": 20,
            },
        },
        # --- 2. execution / accounting failure -----------------------------
        # Matched markets: same ticker, same window, both sides settled. A gap
        # there cannot be fill rate or adverse selection — we got the trade. It
        # can only be entry price, fee model or settlement logic, i.e. OUR
        # arithmetic, which invalidates paper gates on every book and not just
        # this one. 0.5c is the repository's registered ALIGNED tolerance;
        # Lmmsell10 measured ~0.40c inside it. Both signs trip: live beating its
        # own twin on the same markets is exactly as unexplained as losing to it.
        {
            "metric": "twin_live_paired_gap_cents", "arm": ARM_KEY,
            "deployment_kind": "live", "op": ">", "value": 0.5,
            "min_evidence": {
                "metric": "live_settled_contracts", "op": ">=", "value": 30,
            },
        },
        {
            "metric": "twin_live_paired_gap_cents", "arm": ARM_KEY,
            "deployment_kind": "live", "op": "<", "value": -0.5,
            "min_evidence": {
                "metric": "live_settled_contracts", "op": ">=", "value": 30,
            },
        },
        # A single settled market cannot lose more than one clip under this
        # envelope. If one does, the envelope is not being applied.
        {
            "metric": "live_max_realized_loss_usd", "arm": ARM_KEY,
            "deployment_kind": "live", "op": ">", "value": 1.0,
            "min_evidence": {
                "metric": "live_settled_contracts", "op": ">=", "value": 1,
            },
        },
        # Win-rate divergence far beyond anything execution explains. NOT the
        # 1.0pp figure below: that is a PROMOTION bar, and reusing a promotion
        # tolerance as a stand-down trigger would stop the canary for being
        # ordinary. 5.0pp is proposed as a BLOCKING DECISION in the PR.
        {
            "metric": "twin_live_winrate_gap_pp", "arm": ARM_KEY,
            "deployment_kind": "live", "op": ">", "value": 5.0,
            "min_evidence": {
                "metric": "live_settled_contracts", "op": ">=", "value": 50,
            },
        },
    ],
    "hold_if": [
        # --- 1. not yet interpretable: keep running, never PASS ------------
        # Decision overlap. A live book trading a fraction of its twin's
        # candidates is not evidence either way — the gap is usually CAPACITY
        # (gate:open_cap), which live_blocked_entries' per-gate breakdown reads
        # off directly. So low overlap blocks a PASS rather than tripping a stop.
        {
            "metric": "twin_mirror_coverage_pct", "arm": ARM_KEY,
            "deployment_kind": "live", "op": "<", "value": 50.0,
        },
        # Fill rate. Below this the book is barely executing, so the economics
        # are a small non-random subsample of its own candidate stream.
        {
            "metric": "live_fill_rate_pct", "arm": ARM_KEY,
            "deployment_kind": "live", "op": "<", "value": 25.0,
        },
    ],
    "pass_all": [
        # --- 4. successful evidence: eligible for HUMAN review -------------
        {
            "metric": "live_cents_per_contract", "arm": ARM_KEY,
            "deployment_kind": "live", "op": ">", "value": 0.0,
        },
        # The registered twin/live win-rate tolerance from the sibling live
        # canary's contract, in the PROMOTION role it was written for.
        {
            "metric": "twin_live_winrate_gap_pp", "arm": ARM_KEY,
            "deployment_kind": "live", "op": "<=", "value": 1.0,
        },
    ],
}

#: The thresholds that had no repository precedent, and what the operator decided
#: on 2026-08-28. Recorded here rather than only in a PR body because the reason a
#: number is what it is outlives the pull request, and because a reader is owed the
#: difference between "this is the registered precedent" and "a person chose this
#: once, on this date, with nothing to appeal to".
OPERATOR_DECISIONS: dict[str, str] = {
    "successor Version (accepted)":
        "v2 is registered and the v1/e1 PASS is not inherited. The alternative — "
        "arming both arms on v1 — would have put the negative-paper arm on real "
        "money to satisfy a structural check.",
    "promotion sample floor = 0 (no floor)":
        "v1's literal contract. A 300-trade floor was proposed and declined. See "
        "PROMOTION_GATE_SPEC for what n = 0 means at arming time.",
    "win-rate stand-down 5.0pp":
        "No precedent existed for a stand-down trigger; the registered 1.0pp is a "
        "promotion bar and is used as one.",
    "decision-overlap hold 50%, fill-rate hold 25%":
        "No precedent. Lmmsell10 observed a 61.2% fill rate; overlap was never "
        "recorded separately.",
    "total canary loss budget $15, daily stop $5":
        "Chosen from the envelope rather than from evidence: 15 clips of ~$1.",
    "portfolio exposure: not set by this envelope":
        "The canary limits the positions it opens, not money other books already "
        "hold. MAX_TOTAL_EXPOSURE is left at its production value — see "
        "RISK_ENVELOPE['portfolio_breaker'].",
    "naming latitude":
        "The operator granted latitude to rename as needed. The tags and keys "
        "above are unchanged because none collides; if `register` ever refuses a "
        "deployment key or tag as taken, bump the trailing generation number "
        "(`-2`, `_pt2`) rather than reusing a historical name.",
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def material_config(*, live_tag: str = LIVE_TAG, twin_tag: str = TWIN_TAG) -> dict:
    """The deployment's `config_json`, whose `material` block is what
    `enforcement.runtime_config_check` recomputes from live Settings at boot.

    `book_params` names BOTH tags on purpose. The twin book is built as
    `dict(parent)` with only the tag replaced, so it has no independent
    parameters and cannot drift on its own — but recording both means a future
    refactor that gave the twin its own spec would be caught rather than
    silently permitted."""
    return {
        "material": {
            "live_strategies_contains": [live_tag],
            "twin_pairs": {live_tag: twin_tag},
            "book_params": {live_tag: BOOK_PARAMS, twin_tag: None},
        },
        "risk_envelope": RISK_ENVELOPE,
    }


def variants_with_live_book(current: str) -> str:
    """`current` (the running `mmsell_variants`) with this canary's book appended.

    DERIVED rather than written down, because the value is one ~800-character
    string holding EVERY mmsell book: hand-composing it to add one entry is how a
    running book gets dropped by a typo, and dropping a book stops it silently.
    Appending is also order-safe — `mmsell_variant_list` parses on `;` and keeps
    the first spec for a repeated tag, so a re-run cannot shadow an existing book.

    Idempotent: re-appending an already-present `Cmmsell10:` entry is a no-op, so
    running this against a value that already carries the book returns it
    unchanged rather than growing it. A DIFFERENT `Cmmsell10:` spec is refused
    outright — silently overwriting it would be an undetected parameter change to
    a registered book."""
    tokens = [t.strip() for t in current.split(";") if t.strip()]
    for token in tokens:
        tag, _, body = token.partition(":")
        if tag.strip() != LIVE_TAG:
            continue
        if body.strip() != BOOK_PARAMS:
            raise service.ExperimentOsError(
                f"{LIVE_TAG} is already defined as {body.strip()!r}, which is not "
                f"this canary's registered {BOOK_PARAMS!r}. Reconcile the running "
                "config against the deployment's book_params before activating; "
                "overwriting it here would be an undetected parameter change."
            )
        return ";".join(tokens)
    return ";".join([*tokens, LIVE_BOOK_SPEC])


def activation_env(settings) -> dict[str, str]:
    """The EXACT Railway variables step 4 of the plan sets, and nothing else.

    Step 4 — the runtime allowlist — is the only step at which an order can reach
    Kalshi, and it is deliberately not something `arm()` can do. This function
    exists so the operator performing it pastes a *derived* value rather than
    composing one: `MMSELL_VARIANTS` depends on what production is running, and
    the six mmsell safeguards are pinned explicitly so the envelope is true of the
    process rather than merely equal to today's code defaults.

    It only builds the mapping. `scripts/mmsell10_canary.py activate` prints it;
    nothing in this package applies it."""
    env: dict[str, str] = {
        "MMSELL_VARIANTS": variants_with_live_book(settings.mmsell_variants),
    }
    env.update(RISK_ENVELOPE["settings"])
    # Last, so the mapping reads in the order it takes effect: the book exists and
    # its caps are pinned before the switch that lets it spend anything.
    env["LIVE_STRATEGIES"] = LIVE_TAG
    return env


#: Every variable `activation_env` can name. `tests/test_ops_channel_receipt_reads.py`
#: asserts each is settable through the ops channel — the #266 defect class, caught
#: in CI instead of halfway through an activation.
ACTIVATION_VARS: frozenset[str] = frozenset(
    {"MMSELL_VARIANTS", "LIVE_STRATEGIES", *RISK_ENVELOPE["settings"]}
)


def register_successor_version(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Create v2 (single arm + risk envelope), register its gates, freeze it, open
    v2/e1 on the ACTIVE snapshot, and hand the `mmsell10` tag over to it.

    The tag handover is the subtle part and is done in this one call so it cannot
    be half-applied. `mmsell10` is currently carried by v1/e1's two-arm legacy
    deployment; a second ACTIVE deployment arm on the same tag makes it AMBIGUOUS
    and the enforcement resolver refuses it outright, which would stop the paper
    book. So at one instant: the legacy deployment ends, a v1/e1 replacement
    picks up `mmsell9` alone (keeping that book admissible and its evidence
    resolving), and v2/e1's paper deployment takes `mmsell10`.

    Ending a deployment does not orphan its evidence — metric scopes resolve tags
    over every deployment in the epoch, ended or not. Only the ENFORCEMENT
    resolver looks at `ended_at`, which is exactly the ambiguity being cleared.

    Arms nothing. Places no orders. Returns the objects for inspection.
    """
    at = now or _now()
    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        raise service.ExperimentOsError(f"experiment {EXPERIMENT_KEY!r} not found")
    if experiment.state != LifecycleState.PAPER.value:
        raise service.ExperimentOsError(
            f"{EXPERIMENT_KEY} is {experiment.state}, not PAPER — this package "
            "registers a PAPER→LIVE_CANARY successor contract"
        )
    prev = latest_version(session, experiment)
    if prev is not None and prev.risk_json and _declared_arms(session, prev) == {ARM_KEY}:
        raise service.ExperimentOsError(
            f"version {prev.version} already carries a single-arm risk envelope — "
            "this package has already been registered"
        )

    promo = dict(PROMOTION_GATE_SPEC)
    if promotion_sample_floor is not None:
        promo["sample"] = {
            ARM_KEY: {"metric": "settled_trades", "op": ">=",
                      "value": int(promotion_sample_floor)}
        }

    version = service.create_experiment_version(
        session, experiment,
        hypothesis=(
            "Capping the entry price at maxyes=7 keeps the fillable cheap cells "
            "and the realizable edge. Unchanged from v1 — this version narrows "
            "the CONTRACT, not the question."
        ),
        universe_selector=(
            "cheap band lo=5,hi=10 with an entry-price ceiling maxyes=7 — "
            "mmsell10's universe verbatim. No crypto exclusion: excluding a "
            "market class would be a different universe and could not inherit "
            "this arm's evidence."
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule="one contract per order under a $1.00 per-order dollar cap",
        execution_style="maker",
        independent_variable="entry-price ceiling (maxyes)",
        control_required=False,
        control_exemption_reason=(
            "gated on absolute realizable per-trade via the live-calibrated fill "
            "model, as v1 was; the execution control is the registered paper "
            "TWIN, which is armed at the same instant, not a second live arm"
        ),
        risk=RISK_ENVELOPE,
        docs={"thesis": "docs/MMSELL_VARIANTS_THESIS.md",
              "plan": "docs/MMSELL10_CANARY_PLAN.md",
              "studies": ["docs/MMSELL_FILL_MODEL.md",
                          "docs/MMSELL_QUOTE_PARITY.md"]},
        change_reason=(
            "Two structural requirements of the sanctioned canary path that a "
            "frozen v1 cannot satisfy: (a) arm_live_canary requires a "
            "pre-registered risk envelope on the version and v1 has none, and a "
            "frozen version cannot be edited; (b) arm_live_canary requires the "
            "live/twin tag maps to equal the declared arm set exactly, and v1 "
            "declares mmsell9 alongside mmsell10 — arming v1 would put the "
            "negative-paper arm on real money. The hypothesis, universe, "
            "parameters (lo=5,hi=10,maxyes=7), entry/exit semantics, fee model "
            "and promotion bar are carried across unchanged."
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="entry-price ceiling only (lo=5,hi=10,maxyes=7)",
        params={"lo": 5, "hi": 10, "maxyes": 7}, strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=promo, from_state=LifecycleState.PAPER,
        to_state=LifecycleState.LIVE_CANARY, registered_at=at,
        notes="v1's bar verbatim; the sample floor is the one addition.",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes=("pre-registered before arming; every clause names "
               "deployment_kind='live' explicitly"),
    )
    service.freeze_version(session, version, now=at)
    # Evidence begins at the contract boundary, not at some later first look. The
    # evaluator floors every window at max(epoch start, gate evidence start), so
    # this is what makes v2's samples start at zero rather than silently pooling
    # v1/e1 rows gathered under a superseded taxonomy and a two-arm contract.
    service.mark_gate_evidence_started(session, promotion_gate, at=at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)

    epoch = service.open_epoch(
        session, version,
        reason=(
            "v2's operating interval, pinned to the platform snapshot active at "
            "registration. Evidence restarts here: the v1/e1 sample was gathered "
            "under a superseded MARKET_TAXONOMY revision and under a two-arm "
            "contract with no risk envelope."
        ),
        started_at=at,
    )

    # --- the tag handover, at this same instant --------------------------------
    legacy = session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == LEGACY_PAPER_DEPLOYMENT_KEY
        )
    )
    if legacy is not None and legacy.ended_at is None:
        service.end_deployment(session, legacy, ended_at=at)
        service.register_deployment(
            session, session.get(ExperimentEpoch, legacy.epoch_id),
            deployment_key=V1_MMSELL9_DEPLOYMENT_KEY,
            stage=LifecycleState.PAPER, kind="paper",
            arms={"mmsell9": "mmsell9"}, started_at=at,
            notes=("carries mmsell9 alone after the two-arm legacy deployment "
                   "closed, so the tag stays admissible and unambiguous while "
                   "mmsell10 moves to v2"),
        )
    v2_paper = service.register_deployment(
        session, epoch,
        deployment_key=V2_PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER, kind="paper",
        arms={ARM_KEY: PAPER_TAG}, started_at=at,
        notes="the mmsell10 paper book, unchanged, now operating under v2/e1",
    )
    return {
        "version": version,
        "arm_key": ARM_KEY,
        "promotion_gate": promotion_gate,
        "keep_gate": keep_gate,
        "epoch": epoch,
        "paper_deployment": v2_paper,
        "registered_at": at,
    }


def arm(
    session,
    *,
    approved_by: str,
    actor: str = "operator",
    started_at: datetime | None = None,
    reason: str | None = None,
) -> dict:
    """Arm the canary through the ONE sanctioned path.

    `service.arm_live_canary` does the work and enforces every structural rule:
    the promotion gate is re-evaluated synchronously (a recorded PASS is not a
    capability token), the paper epoch closes, a fresh I2 live epoch opens, and
    the live deployment and its twin are registered at the identical instant with
    a first-class `twin_of` link on fresh, unused tags.

    THIS FUNCTION EXPANDS REAL-MONEY EXPOSURE. It still places no order by itself
    — the runtime allowlist (`LIVE_STRATEGIES`) is a separate switch — but it is
    the act that makes the canary armable, and it requires explicit operator
    approval recorded in `approved_by`.
    """
    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        raise service.ExperimentOsError(f"experiment {EXPERIMENT_KEY!r} not found")
    version = latest_version(session, experiment)
    gate = _gate(session, version, PROMOTION_GATE_KEY)
    live, twin, epoch = service.arm_live_canary(
        session, experiment,
        gate=gate,
        approved_by=approved_by,
        live_key=LIVE_DEPLOYMENT_KEY,
        twin_key=TWIN_DEPLOYMENT_KEY,
        live_tags={ARM_KEY: LIVE_TAG},
        twin_tags={ARM_KEY: TWIN_TAG},
        config=material_config(),
        started_at=started_at,
        actor=actor,
        reason=reason or (
            f"mmsell10 Stage-1 live canary armed on {LIVE_TAG} with twin "
            f"{TWIN_TAG} at one boundary; envelope pre-registered on v"
            f"{version.version}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


def _declared_arms(session, version: ExperimentVersion) -> set[str]:
    return {a.arm_key for a in arms_for(session, version)}


def _gate(session, version: ExperimentVersion, gate_key: str) -> ExperimentGate:
    gate = session.scalar(
        select(ExperimentGate).where(
            ExperimentGate.version_id == version.id,
            ExperimentGate.gate_key == gate_key,
        )
    )
    if gate is None:
        raise service.ExperimentOsError(
            f"gate {gate_key!r} is not registered on version {version.version} — "
            "run register_successor_version first"
        )
    return gate


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "EXPERIMENT_KEY", "KEEP_GATE_KEY",
    "KEEP_GATE_SPEC", "LIVE_BOOK_SPEC", "LIVE_TAG", "LIVE_DEPLOYMENT_KEY",
    "OPERATOR_DECISIONS", "PROMOTION_GATE_KEY", "PROMOTION_GATE_SPEC",
    "RISK_ENVELOPE", "TWIN_TAG", "TWIN_DEPLOYMENT_KEY", "activation_env", "arm",
    "material_config", "register_successor_version", "variants_with_live_book",
]
