"""mmsell reviewed universe — the contract for `Rmmsell1` / `Rmmsell2`.

Registers one PAPER experiment with two arms and one pre-registered keep gate. Arms nothing,
trades nothing, touches no real money: under NEW_ONLY a tag no active deployment arm carries
cannot trade, so registering this contract is what lets these two books start.

WHY THIS EXPERIMENT EXISTS. Every mmsell book to date trades a universe defined by STRUCTURE —
a band, a price ceiling, a market type, a series substring. None trades a universe defined by
UNDERSTANDING. Twenty-four series have now been through a four-check operator review (category,
settlement rules, contracts-per-outcome, historical P&L) and are signed in
`kalshi_bot/registry/series_manifest.json`. Both arms here trade exactly that set, pinned
literally into their own `onlyx=` specs, and differ only in whether the book may hold more than
one position on a single contest.

WHAT IS GATED HERE, AND WHAT IS NOT. This contract gates ONE question: inside a universe we
understand, does the contest cap buy daily stability? That question has an INTERNAL control
(`Rmmsell1`), shares one epoch and one platform snapshot with its treatment, and is therefore
poolable by construction.

The question that motivated the review — does a reviewed universe beat an unreviewed one? — is
**deliberately NOT gated here**, and saying so is the point. Its only available control is
`mmsell10`, which already carries an active deployment arm of `mmsell-price-ceiling-capacity`.
Naming it an EXTERNAL control is precisely what has `mmsell-anchor-vol-entry` in
BLOCKED_PLATFORM: a cross-snapshot delta pools incomparable evidence. So the universe comparison
is recorded as an OBSERVATIONAL read that authorizes nothing, and a future Version that wants to
gate it must open a third arm — an unreviewed-universe control on a fresh tag, sharing this
epoch — rather than reaching for a book already spoken for.

WHY THE CAP ARM USES THE CORRECTED CONTEST KEY. Three of the twenty-four reviewed series are
subject-split (`KXFEDMENTION`, `KXRAIN`, `KXTRUMPSAY`): their last ticker token names a distinct
SUBJECT, not a rung of one ladder. On the shipped key a cap of 1 collapses `KXTRUMPSAY`'s 37
distinct words into 10 weekly buckets and `KXRAIN`'s 221 markets into 27 days, refusing entries
that share no outcome at all. In this universe the corrected key is not a refinement — it is the
difference between a cap that measures correlation and one that measures the calendar.
`docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md` has the measurement.

WHAT THE SELECTION COSTS US, STATED BEFORE ANY ARM TRADES. These series were signed partly on
their own historical P&L, so some of whatever `Rmmsell1` shows is in-sample by construction. The
gate below is unaffected: both arms share the selection, so it cancels in every delta the gate
reads. The observational universe read is affected, and is recorded as such.

A KNOWN BLIND SPOT, recorded rather than fixed. The cap cannot see correlation that crosses a
SERIES boundary. `contest_key_of` groups within a series prefix and never claims to group across
them, and the `KXTRUMPSAY` family is a live example: a weekly and a monthly contract on the same
word resolve on one utterance. `KXTRUMPSAYMONTH` is barred from the manifest, which removes that
particular pairing from this universe, but the mechanism is general and this contract does not
claim to bound it.

Full pre-registration: docs/MMSELL_REVIEWED_TAPE.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-reviewed-universe"

CONTROL_ARM = "reviewed_uncapped"
CAPPED_ARM = "reviewed_capped"

CONTROL_TAG = "Rmmsell1"
CAPPED_TAG = "Rmmsell2"

PROBE_DEPLOYMENT_KEY = "mmsell-reviewed-probe-1"
PAPER_DEPLOYMENT_KEY = "mmsell-reviewed-paper-1"

KEEP_GATE_KEY = "paper_keep"

#: Shared entry parameters — `mmsell10`'s band and ceiling, so the observational read against it
#: differs in universe rather than in how either book prices.
BASE_BOOK_PARAMS = "lo=5,hi=10,maxyes=7"

#: The cap the treatment opts into, on the CORRECTED key. See the module docstring.
CAP_PARAMS = "contestcap=1,contestkey=split"

#: This package declares NO literal series list. The universe is generated from the manifest by
#: `scripts/reviewed_tape_spec.py` and pinned into the env var an operator sets, and it is
#: recorded onto the deployment config at registration time by `_universe()` below — so the
#: contract records the universe THAT EXISTED WHEN THE ARMS OPENED rather than re-reading a file
#: that will grow under it. Widening the manifest later cannot retroactively change what this
#: epoch says these books traded.
#:
#: A second copy of the list here would be a third place for it to drift.


def _universe() -> list[str]:
    from kalshi_bot import registry

    return sorted(registry.reviewed_series())


def book_specs() -> dict[str, str]:
    """The two `MMSELL_VARIANTS` bodies, as the deployment records them."""
    allow = f"onlyx={'+'.join(_universe())}"
    return {CONTROL_TAG: f"{BASE_BOOK_PARAMS},{allow}",
            CAPPED_TAG: f"{BASE_BOOK_PARAMS},{CAP_PARAMS},{allow}"}


#: The pre-registered sample floor, in SETTLEMENT DAYS rather than trades — the same unit and the
#: same value `mmsell-correlation-cap` uses, and for the same reason: a 5th percentile over a
#: short window interpolates between the two worst days, so one bad slate moves it bodily.
#:
#: Days rather than trades matters MORE here than it did there. This universe is 24 series out of
#: 364 known, so the trade rate is well below `mmsell10`'s and the capped arm's is lower again. A
#: trade-count floor would make the gate unreachable for months; a calendar floor does not move.
SAMPLE_FLOOR_DAYS = 60

#: The stability bar on `daily_pnl_stability` = mean(daily P&L) / sd(daily P&L). Absolute on a
#: scale-free statistic, deliberately: a relative bar would drift with the control. Set to the
#: same +0.05 `mmsell-correlation-cap` pre-registered, so the two cap reads are commensurable —
#: NOT fitted to anything observed in this universe, because nothing has been.
STABILITY_BAR = 0.05

#: The c/trade floor. A FLOOR, never a promotion criterion. A cap is a drawdown control and
#: trades total return for smoothness by construction; this exists only to catch one that
#: destroys the edge while smoothing it.
EDGE_FLOOR_CENTS = -0.5

ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (CONTROL_ARM, ArmRole.CONTROL,
     "The reviewed universe, uncapped. `mmsell10`'s band and ceiling with an EXACT series "
     "allowlist (`onlyx=`) naming the operator-signed set, and no contest cap — every existing "
     "cap (position, settlement-date, event rung) applies exactly as today.",
     {"tag": CONTROL_TAG, "book": BASE_BOOK_PARAMS, "contestcap": None,
      "universe": "operator-reviewed series, exact match"}),
    (CAPPED_ARM, ArmRole.TREATMENT,
     "The same universe under one open position per CONTEST, on the CORRECTED (subject-split) "
     "key. That is the only difference from the control.",
     {"tag": CAPPED_TAG, "book": f"{BASE_BOOK_PARAMS},{CAP_PARAMS}", "contestcap": 1,
      "contestkey": "split", "universe": "operator-reviewed series, exact match"}),
)

#: What makes this a CAP experiment inside a fixed universe rather than a universe experiment.
#: Frozen: changing any of these is a new Version, not a tweak.
HELD_CONSTANT: list[str] = [
    "both arms trade the SAME series list, pinned literally into each book's own `onlyx=` "
    "spec. The universe is not an arm here and cannot drift under the comparison: a sign-off "
    "landing in the manifest tomorrow does not reach a running book",
    "entry band and price ceiling are identical on both arms (lo=5, hi=10, maxyes=7)",
    "no arm carries a stop, volatility gate or strangle leg; those are the anchor set's "
    "experiment and would confound this one",
    "no arm selects on market TYPE. The Tmmsell family measured that axis to a verdict; "
    "re-introducing it here would make the cap and the type filter inseparable",
    "neither arm names a review TIER (`universe=`). The explicit list already states exactly "
    "what a tier approximates, and setting both would narrow the books twice",
    "the cap counts OPEN positions and never closed ones, so it bounds carried risk rather "
    "than acting as an entry-timing rule",
    "which contract is kept within a capped contest is FIRST ARRIVAL, never a ranking",
    "the GLOBAL mmsell_contest_cap_enabled stays off for the life of this version. The "
    "treatment opts in per book, so no other book's selection moves while this runs",
    "both tags are PAPER and appear in no live arm set, so no live mirror fires for either",
]

#: The screen that produced the universe, recorded as the instrument it is. This IS the probe:
#: a retrospective four-check review of each candidate series against its own settled tape, run
#: by an operator over five batches and merged as evidence before any arm here traded. It is the
#: same species of instrument as `mmsell-correlation-cap`'s counterfactual — a read of history
#: that fixed a design decision — and it carries the same authority, which is none.
PROBE_RULE: dict = {
    "instrument": "operator four-check review per candidate series: (1) market-type category "
                  "against SERIES_TYPES, (2) settlement rules read verbatim from Kalshi "
                  "(scripts/series_rules_audit.py), (3) contracts-per-outcome "
                  "(scripts/series_concentration.py, corrected contest key), (4) historical "
                  "paper P&L and edge (scripts/mmsell_series_pnl.py)",
    "unit_of_observation": "one candidate series, scored on its own settled paper tape",
    "window": "all-time mmsell paper history, twins excluded, as of 2026-09-10",
    "recorded_result": "24 of the 138 manifest rows signed across five batches; the rest left "
                       "unreviewed, held, or explicitly refused. Two series were BARRED or held "
                       "on this evidence rather than admitted: KXTRUMPSAYMONTH (edge -7.5 on "
                       "117 trades, identical settlement rules to a signed series over roughly "
                       "four times the resolution window) and KXTRUMPSAYCOMPANY (same "
                       "structure, too little own history to judge).",
    "known_confound": "a series was signed partly on its OWN historical P&L, so this screen is "
                      "fitted to the same tape it selected from. That is why it authorizes "
                      "nothing and why both arms below share the selection — it cancels in "
                      "every delta the gate reads, and it does NOT cancel in the observational "
                      "universe read.",
    "authority": "NONE. A retrospective screen on paper fills cannot establish that a reviewed "
                 "universe earns more going forward. It fixes WHICH SERIES the arms trade, "
                 "before either arm trades one.",
}

#: The universe comparison, recorded as the instrument it is so nobody later reads it as a
#: result this contract certified.
OBSERVATIONAL_READ: dict = {
    "question": "does a reviewed universe beat an unreviewed one?",
    "instrument": f"{CONTROL_TAG} read against the existing paper book `mmsell10`, which runs "
                  "the identical band and ceiling over the unreviewed universe",
    "authority": "NONE. `mmsell10` carries an active deployment arm of another experiment, so "
                 "this is a CROSS-SNAPSHOT delta and is not poolable. It is a read for a human, "
                 "not evidence a gate may consume, and no gate on this Version references it.",
    "confound": "the 24 series were signed partly on their own historical P&L (check 4 of the "
                "review), so part of any advantage is in-sample by construction. Only the "
                "forward tape from each arm's opening instant speaks to it at all.",
    "how_to_gate_it_properly": "a future Version opening a THIRD arm — an unreviewed-universe "
                               "control on a fresh tag, in this experiment's own epoch. Never "
                               "by naming `mmsell10` an external control.",
}

#: Paper keep/kill. There is deliberately NO promotion gate: nothing here is a live candidate,
#: and registering a PAPER -> LIVE_CANARY gate now would pre-authorize a transition for which no
#: evidence exists. A promotion is a new gate on a new Version.
KEEP_GATE_SPEC: dict = {
    "description": (
        "A concentration cap is a DRAWDOWN control: it trades total return for smoothness by "
        "construction and must be judged as one. Keep the capped arm only if it buys materially "
        "more daily stability than the uncapped control on the SAME universe, without "
        "destroying the edge. Read on the daily series, not on cents per trade — at this book's "
        "per-trade variance a c/trade test of the effect size in question is unreachable, and "
        "this universe trades less than the one that finding was measured on."
    ),
    "sample": {
        CONTROL_ARM: {"metric": "settled_days", "op": ">=", "value": SAMPLE_FLOOR_DAYS},
        CAPPED_ARM: {"metric": "settled_days", "op": ">=", "value": SAMPLE_FLOOR_DAYS},
    },
    "pass_all": [
        {"metric": "delta.daily_pnl_stability", "treatment": CAPPED_ARM,
         "control": CONTROL_ARM, "op": ">=", "value": STABILITY_BAR},
        {"metric": "delta.pnl_cents_per_trade", "treatment": CAPPED_ARM,
         "control": CONTROL_ARM, "op": ">=", "value": EDGE_FLOOR_CENTS},
    ],
    "fail_any": [
        # The cap bought no smoothness inside a universe we understand. That kills the mechanic
        # for this universe: if capping the contest does not steady the daily series HERE, where
        # the contest key is known to be right for every series in the set, the clustering the
        # thesis rests on was luck.
        {"metric": "delta.daily_pnl_stability", "treatment": CAPPED_ARM,
         "control": CONTROL_ARM, "op": "<", "value": 0},
        # Smoothness bought at too high a price in edge.
        {"metric": "delta.pnl_cents_per_trade", "treatment": CAPPED_ARM,
         "control": CONTROL_ARM, "op": "<", "value": EDGE_FLOOR_CENTS},
    ],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Create the experiment, freeze v1 with its two arms and the keep gate, open e1 on the
    ACTIVE platform snapshot, then register the PAPER deployment that gives both books their
    tags.

    The PROBE deployment is TAGLESS: it records the four-check operator review that produced the
    universe, which is a read of already-settled history and places no order. The gate's bars are
    NOT fitted by it — they are carried over unchanged from `mmsell-correlation-cap` so the two
    cap reads are commensurable.

    `promotion_sample_floor` is the knob the experiment-command transport always passes. Here it
    may only RAISE the 60-day floor: an envelope may make a pre-registered bar stricter, never
    weaker. It is measured in SETTLEMENT DAYS on this contract, not trades.

    Idempotence is refusal, not a no-op: a second run raises rather than quietly creating a
    parallel contract."""
    floor = SAMPLE_FLOOR_DAYS if promotion_sample_floor is None else int(promotion_sample_floor)
    if floor < SAMPLE_FLOOR_DAYS:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is below the reviewed floor {SAMPLE_FLOOR_DAYS} "
            "SETTLEMENT DAYS — an envelope may make a pre-registered bar stricter, never weaker"
        )
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package registers it once; "
            "a changed contract is a new Version"
        )

    universe = _universe()
    if not universe:
        raise service.ExperimentOsError(
            "no reviewed series in the manifest — there is no universe to register a contract "
            "over, and two books trading nothing would read as an inactive experiment"
        )
    specs = book_specs()

    experiment = service.create_experiment(
        session,
        key=EXPERIMENT_KEY,
        origin="operator",
        title="mmsell reviewed universe — does the contest cap help where we understand the book",
        family="risk_concentration",
        hypothesis=(
            "Inside a universe of series a human has read and signed off on, capping open "
            "positions at one per CONTEST — on the corrected subject-split key — materially "
            "steadies the daily P&L series without destroying the edge."
        ),
        mechanism=(
            "One occasion resolves every contract written on it at once, so positions the book "
            "counts as independent are one position at N x size. The reviewed set was chosen "
            "partly for low concentration, which means this is the HARDER place for a cap to "
            "show value — and therefore the more informative one. Where a cap still pays after "
            "the worst ladders have been screened out, it is bounding real correlation rather "
            "than compensating for a universe nobody looked at."
        ),
        counterparty=(
            "nobody — this is not an edge claim. The counterparty is our own risk model, which "
            "prices diversification it does not have."
        ),
        falsification=(
            "The cap does not improve daily stability over the uncapped control on the same "
            "universe at 60 settlement days, OR it improves it only by giving up more than "
            "0.5c/trade of edge. Either kills the mechanic for this universe."
        ),
        universe=(
            f"EXACTLY the {len(universe)} series carrying an operator review in "
            "kalshi_bot/registry/series_manifest.json as of registration, pinned literally into "
            "each book's `onlyx=` spec: " + ", ".join(universe)
        ),
        docs={"thesis": "docs/MMSELL_REVIEWED_TAPE.md",
              "review": "docs/MMSELL_SERIES_APPROVAL_REVIEW.md",
              "manifest": "kalshi_bot/registry/series_manifest.json",
              "spec_tool": "scripts/reviewed_tape_spec.py"},
        notes=(
            "The universe question that motivated the review is NOT gated on this contract — "
            "see OBSERVATIONAL_READ. Its only available control already carries another "
            "experiment's arm, and gating a cross-snapshot delta is the failure mode that has "
            "mmsell-anchor-vol-entry blocked."
        ),
        actor=actor,
        now=at,
    )

    version = service.create_experiment_version(
        session, experiment,
        hypothesis=experiment.hypothesis,
        mechanism=experiment.mechanism,
        counterparty=experiment.counterparty,
        falsification=experiment.falsification,
        universe_selector=experiment.universe,
        universe_exclusions=(
            "everything outside the reviewed list, by construction — `onlyx=` is an EXACT "
            "allowlist, not a substring one. That distinction is load-bearing here: "
            "`only=KXTRUMPSAY` would also admit KXTRUMPSAYCOMPANY and KXTRUMPSAYMONTH, which "
            "carry identical settlement rules over roughly four times the resolution window and "
            "measured -7.5 edge against the signed series' +6.0. KXTRUMPSAYMONTH is barred in "
            "the manifest and KXTRUMPSAYCOMPANY is held at in_review. The global "
            "mmsell_skip_series list (parlays, weather) applies to both arms unchanged."
        ),
        entry_rule=(
            "the control's entry: sell the cheap tail (buy NO at the no-bid) on any in-band "
            "candidate whose series is in the allowlist. The treatment additionally declines a "
            "candidate when it already holds one open position on that candidate's contest, "
            "keyed by kalshi_bot/mmsell/regimes.contest_key_of with split_subjects=True."
        ),
        exit_rule="hold to settlement; no stop, no take-profit. Identical on both arms.",
        sizing_rule="flat 1-contract clip on both arms; size is not an arm here.",
        execution_style="maker",
        independent_variable="whether the book may hold more than one position on one contest",
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": f"delta.daily_pnl_stability ({CAPPED_ARM} minus {CONTROL_ARM})",
            "secondary": ["daily_pnl_stability", "settled_days", "pnl_cents_per_trade",
                          "realized_pnl_usd", "settled_trades"],
            "note": ("c/trade is a FLOOR on this contract, never a promotion criterion — the "
                     "power limit docs/MMSELL_ROADMAP.md S1 records binds here at least as hard "
                     "as it does on mmsell-correlation-cap, since this universe trades less. "
                     "The fill model is not read: both arms are maker books in one band, where "
                     "it cannot discriminate."),
        },
        sample={"probe": PROBE_RULE,
                "observational": OBSERVATIONAL_READ,
                "paper_floor_settled_days": {CONTROL_ARM: floor, CAPPED_ARM: floor},
                "universe_at_registration": universe},
        costs={"model": "post-2026-08-11 maker fee model, identical on both arms, so it "
                        "cancels in every delta this gate reads"},
        provenance={"universe": "kalshi_bot/registry/series_manifest.json, rows whose state is "
                                "graduated AND which carry rules_reviewed_at; emitted by "
                                "scripts/reviewed_tape_spec.py",
                    "positions": "paper_trades joined to mmsell_settlement_meta for the "
                                 "series/event of each held market",
                    "contest_key": "kalshi_bot/mmsell/regimes.contest_key_of, split_subjects"},
        monitoring={"telemetry": "MmSellCycleSummary.skipped_contest_cap, persisted per cycle "
                                 "to system_events — an arm that declined nothing measured "
                                 "nothing",
                    "drift": "scripts/reviewed_tape_spec.py --check reports when the manifest "
                             "has moved past what these books were armed with. A non-zero exit "
                             "is a prompt to widen DELIBERATELY (new env value plus a recorded "
                             "epoch on both arms), never something that happens on its own"},
        docs={"thesis": "docs/MMSELL_REVIEWED_TAPE.md"},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    keep_spec = json.loads(json.dumps(KEEP_GATE_SPEC))
    for arm_key in (CONTROL_ARM, CAPPED_ARM):
        keep_spec["sample"][arm_key]["value"] = floor
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=keep_spec, registered_at=at,
        notes=("registered before either arm has traded a single market. The bars are carried "
               "over unchanged from mmsell-correlation-cap rather than fitted to anything "
               "observed in this universe, so the two cap reads are commensurable and neither "
               "was tuned to its own evidence."),
    )
    service.freeze_version(session, version, now=at)

    epoch = service.open_epoch(
        session, version,
        reason=("v1's first operating interval, pinned to the platform snapshot active at "
                "registration. Both arms open together in one epoch, which is what makes the "
                "control poolable with the treatment without an external reference."),
        started_at=at,
    )

    probe = service.register_deployment(
        session, epoch,
        deployment_key=PROBE_DEPLOYMENT_KEY,
        stage=LifecycleState.PROBE,
        kind=DeploymentKind.PROBE,
        arms={arm_key: None for arm_key, _, _, _ in ARMS},
        config={"instrument": PROBE_RULE["instrument"], "window": PROBE_RULE["window"],
                "universe": universe},
        started_at=at,
        notes=("TAGLESS BY CONSTRUCTION. The four-check review reads already-settled history; it "
               "places no order and carries no tag, so nothing on it can reach the exchange. "
               "Recorded as a deployment so the screen that chose these 24 series is part of "
               "the contract — including its own confound — rather than a claim in prose."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("the four-check review is recorded on v1, including that it was fitted partly "
                "to the same tape it selected from and therefore authorizes nothing beyond "
                "fixing which series the arms trade"),
        occurred_at=at, version=version, epoch=epoch,
    )

    paper = service.register_deployment(
        session, epoch,
        deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER,
        kind=DeploymentKind.PAPER,
        arms={CONTROL_ARM: CONTROL_TAG, CAPPED_ARM: CAPPED_TAG},
        config={"books": specs, "universe": universe,
                "universe_size": len(universe)},
        started_at=at,
        notes=("Two PAPER books, no real money, neither tag in any live arm set. Both tags are "
               "fresh, so neither inherits open positions or history from another experiment "
               "and both series start at the same instant — which is what the gate's "
               "same-window reads assume. The treatment opts in through its own `contestcap`, "
               "leaving the GLOBAL mmsell_contest_cap_enabled off and every other book's "
               "selection untouched. The universe is recorded here as it stood at registration: "
               "later sign-offs do not reach these books."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PAPER, actor=actor,
        reason=("the review fixed the universe and the pre-registration is merged "
                "(docs/MMSELL_REVIEWED_TAPE.md); the forward test is the evidence that decides, "
                "and a retrospective screen authorizes nothing. The gate's bars are carried "
                "over from mmsell-correlation-cap rather than fitted here, so neither cap read "
                "was tuned to its own evidence"),
        occurred_at=at, version=version, epoch=epoch,
    )

    return {
        "version": version,
        "epoch": epoch,
        "probe": probe,
        "paper": paper,
        "keep_gate": keep_gate,
        "universe": universe,
    }
