"""The legacy migration manifest — every pre-Experiment-OS book, classified (PR 2).

This file IS the migration classification (spec §22): a hand-authored, reviewed
declaration of what each legacy strategy is, sourced from `docs/BOOK_REGISTRY.md`,
the thesis docs, and config — never scraped or inferred at runtime, because a parser
guessing at registry prose is exactly how invented metadata happens. Change it by PR,
like the registry itself.

Authoring rules (enforced by the importer + tests):

  * Verdicts, gates, and numbers are condensed from the registry row VERBATIM in
    substance — nothing is recomputed, nothing rounded into a nicer story.
  * Dates: the registry records most boundaries to the day; those are stored at
    00:00Z with day precision noted where it matters. MEASURED instants
    (fee deploy 2026-08-11T15:00Z, taxonomy 2026-08-13T18:09:40Z, A5 pairing fix
    2026-08-14T14:31:12Z) are stored exactly. Unknown stays absent → NULL.
  * `integrity` is stated per experiment (spec §23) and never defaulted upward:
    B = identity/rules reconstructed, some historical dependency uncertain
    (all active books — their historical platform boundaries predate this system);
    C = performance context that cannot be certified for a promotion gate;
    D = a lump of tags with no reconstructable per-book contract.
  * ACTIVE books get the §22.2/22.3 treatment (version, arms, gates, migration
    epoch, grandfathered deployment). RETIRED books get the §22.5 minimum (identity,
    thesis ref, verdict, kill reason, evidence) — detailed arm/epoch reconstruction
    is deliberately NOT performed for dead books.
  * `covers_tags` / `covers_tag_prefixes` declare which `paper_trades.strategy` /
    `live_orders.strategy` values this experiment accounts for — the migration
    report's coverage math runs on these, so a tag missing here shows up loudly as
    UNMAPPED instead of being silently absorbed.

Baseline platform snapshot: BASELINE_REVISIONS describes the semantics actually
deployed at migration time. Activation boundaries are measured where a measurement
exists and explicitly unknown (`boundary_unknown`) where it does not — never the
import or merge timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc

MANIFEST_VERSION = "2026-08-15.1"

# Measured platform boundaries (sources cited inline below).
FEE_BOUNDARY = datetime(2026, 8, 11, 15, 0, 0, tzinfo=UTC)
TAXONOMY_BOUNDARY = datetime(2026, 8, 13, 18, 9, 40, tzinfo=UTC)
A5_PAIRING_BOUNDARY = datetime(2026, 8, 14, 14, 31, 12, tzinfo=UTC)


def _day(y: int, m: int, d: int) -> datetime:
    """A registry date known to the DAY — stored at 00:00Z (precision documented)."""
    return datetime(y, m, d, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Baseline platform revisions (spec §16; the FIRST complete snapshot)
# ---------------------------------------------------------------------------

BASELINE_REVISIONS: list[dict] = [
    dict(
        component="FEE_MODEL",
        version="maker_rate_2026_08_11",
        description=(
            "Kalshi taker fee formula, ceiled to a whole cent per order, for taker "
            "entries; maker entries billed at the measured maker rate "
            "(kalshi_fee(..., maker=True)) since the 2026-08-11 deploy — correcting "
            "the prior simulator, which billed resting maker entries at the taker "
            "rate (~1.000c/contract vs the ~0.003c actually charged, measured n=342 "
            "live fills). Size of the correction: +0.85 to +0.89c/contract in the "
            "5-10c yes band."
        ),
        reason="paper maker books were overcharged ~0.87c/ct; see BOOK_REGISTRY 'FEE RE-BASELINE'",
        normalization_available=True,  # per-row stored fees make history exactly re-fee-able
        backward_compatibility=(
            "I1 EXACTLY_NORMALIZABLE: mmsell_fill_model re-fees every row to one "
            "model (fee stored per trade). Relative gates unaffected; absolute "
            "pre-boundary bars re-read +0.87c."
        ),
        activated_at=FEE_BOUNDARY,
        boundary_source=(
            "measured deploy boundary 2026-08-11 15:00 UTC, as pinned by the "
            "mmsellA4 gate reading in docs/BOOK_REGISTRY.md"
        ),
    ),
    dict(
        component="FILL_MODEL",
        version="assumed_fill_plus_mmsell3_calibration",
        description=(
            "Composite, as actually deployed: (1) the paper engine assumes a "
            "resting maker order always fills at its price (takers fill at the "
            "ask); (2) promotion gating corrects that optimism through the "
            "live-calibrated realizable projection — YES entry price -> (P[fill], "
            "realizable P&L | fill), calibrated on the live mmsell3 ground truth "
            "(n=359 settled, 2026-07-13..19) and projected through each paper "
            "book's own price mix (docs/MMSELL_FILL_MODEL.md; the 'realizable' "
            "column with a coverage figure). The depth-proxy queue fill model was "
            "measured and REJECTED — PR #218 closed the paper-side queue route — "
            "so it is recorded history, not active platform truth."
        ),
        reason="maker adverse selection is the entire paper->live gap (~2c/contract)",
        normalization_available=None,  # projection corrects paper; not an exact per-row transform
        backward_compatibility=(
            "realizable projection applies across maker books because fillability "
            "is driven by the price cell, not the variant label (measured)"
        ),
        boundary_unknown=True,
        boundary_source="composite semantics; no single activation instant exists",
    ),
    dict(
        component="MARKET_TAXONOMY",
        version="coverage_2026_08_13",
        description=(
            "Market-type taxonomy at 99.6% classified coverage of candidate flow "
            "(46 series added 2026-08-13; purely additive — none reclassified). "
            "Books selecting via mtype=/mode=/xmtype= were offered ~half their "
            "universe before this boundary."
        ),
        reason="an unclassified series is admitted by no allowlist",
        normalization_available=False,  # universe change: no offset converts populations
        backward_compatibility=(
            "I2 SAMPLE_BOUNDARY for type-selecting books: pre-boundary trades are "
            "dropped, not adjusted (BOOK_REGISTRY 'UNIVERSE BOUNDARIES')"
        ),
        activated_at=TAXONOMY_BOUNDARY,
        boundary_source=(
            "measured 2026-08-13T18:09:40Z; enforced in code as COHORT_START in "
            "scripts/mmsell_fill_model.py"
        ),
    ),
    dict(
        component="EXECUTION_ENGINE",
        version="live_executor_2026_08",
        description=(
            "LiveExecutor resting maker orders with strategy-agnostic open-order "
            "dedup (live_open_order_exists), kill switch + orderly drain, live "
            "mirror firing from the paper-open branch (which is why live replicas "
            "need fresh tags — a tag with pre-arming paper positions hands live "
            "tickers it can never trade). Maker offset A/B machinery present but "
            "DISARMED (MMSELL_LIVE_OFFSET_AB_ARMS='' — re-arming silently splits "
            "any live mmsell book across the arms by ticker hash)."
        ),
        reason="current live execution semantics at migration",
        boundary_unknown=True,
        boundary_source="accreted across deploys; no single activation instant",
    ),
    dict(
        component="SETTLEMENT_ENGINE",
        version="hold_to_settlement_2026_08",
        description=(
            "Shared paper engine settles positions at market settlement; the "
            "no-timeout set (freeze/theta/tfav/pin15/mmsell) holds to settlement. "
            "Weather settles from captured NWS settlement data."
        ),
        reason="current settlement semantics at migration",
        boundary_unknown=True,
        boundary_source="accreted across deploys; no single activation instant",
    ),
    dict(
        component="RISK_ENGINE",
        version="caps_2026_08",
        description=(
            "Per-book open-position caps and order caps, settlement-date "
            "concentration cap (mmsell_settlement_meta, applied at query time), "
            "correlated-event and per-event rung caps, daily-loss trip, KILL_SWITCH "
            "env override."
        ),
        reason="current risk controls at migration",
        boundary_unknown=True,
        boundary_source="accreted across deploys; no single activation instant",
    ),
    dict(
        component="DATA_PROVENANCE",
        version="live_vs_backfill_2026_08",
        description=(
            "Live-collected tables (weather_*, mmsell_* ticks, ladder snapshots) "
            "are never silently mixed with backfill_* REST-archive tables; regime "
            "settled history is captured FORWARD against Kalshi's ~70-day retention "
            "wall (measured 2026-08-03)."
        ),
        reason="provenance separation is a standing analysis rule",
        boundary_unknown=True,
        boundary_source="standing rule; no single activation instant",
    ),
    dict(
        component="KALSHI_API_SCHEMA",
        version="advanced_tier_2026_08_12",
        description=(
            "Kalshi REST API as parsed by kalshi_bot.kalshi; ADVANCED rate tier "
            "since 2026-08-12 (20 -> 30 reads/sec); ~70-day settled-history "
            "retention (measured 2026-08-03); sports close_time carries a "
            "far-future fallback so hours_to_expiration is the only forward "
            "resolution estimate in-play."
        ),
        reason="current API contract at migration",
        boundary_unknown=True,
        boundary_source="tier grant day (2026-08-12) known; instant not recorded",
    ),
    dict(
        component="METRICS_ENGINE",
        version="pnl_scripts_2026_08",
        description=(
            "PnL/digest/fill-model scripts as the measurement layer; "
            "mmsell_fill_model normalizes every P&L to one fee model (fee stored "
            "per row) and enforces cohort floors (COHORT_START) with matched-window "
            "control reads."
        ),
        reason="current metric semantics at migration",
        boundary_unknown=True,
        boundary_source="accreted across deploys; no single activation instant",
    ),
    dict(
        component="EXPERIMENT_ENGINE",
        version="foundation_pr1",
        description=(
            "Experiment OS foundation: lifecycle state machine, structured gates, "
            "platform registry, append-only audit (enforcement mode OFF)."
        ),
        reason="the experiment engine itself is a platform component",
        pr_ref="PR #221",
        boundary_unknown=True,
        boundary_source=(
            "activates at the first deploy applying migration b4c5d6e7f8a9; "
            "establish from deploy logs if ever needed"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Legacy experiments
# ---------------------------------------------------------------------------
#
# Entry shape:
#   key, title, family, origin, state, legacy_class, integrity, hypothesis, docs,
#   covers_tags, covers_tag_prefixes, verdict (audit-row reason), retired_at,
#   paused_from, predecessor_key, notes,
#   evidence: [ {label, evidence_class, source, summary, window_start, window_end} ]
#   active: {  # only for books that still operate — the §22.2/22.3 reconstruction
#     version: {contract fields for create_experiment_version},
#     arms: [ {arm_key, role, description, params, tag} ],
#     gates: [ {gate_key, kind, from_state, to_state, spec, registered_at,
#               evidence_started_at, notes,
#               result: {verdict, computed_at, sample, metrics, evidence_ref,
#                        explanation}} ],
#     deployments: [ {key, stage, kind, tags: {arm_key: tag}, config,
#                     started_at, ended_at, twin_of_key, twin_started_at_from,
#                     notes} ],
#   }

LEGACY_EXPERIMENTS: list[dict] = [
    # ======================================================================
    # ACTIVE — live canaries
    # ======================================================================
    dict(
        key="theta4-fat-tail",
        title="theta4 — fat-tail (x2.0) revival of the theta crypto tail-sell",
        family="theta",
        origin="operator",
        state="LIVE_CANARY",
        legacy_class="ACTIVE_LIVE",
        integrity="B",
        hypothesis=(
            "With the tail widened x2.0 and edge=6c, selling the cheap band in the "
            "final 35 minutes of hourly crypto ladders is profitable after real "
            "maker execution."
        ),
        docs={
            "thesis": "docs/THETA_THESIS.md",
            "studies": ["docs/THETA_FILL_MODEL.md", "docs/THETA_LIVE_PLAN.md"],
        },
        covers_tags=["theta4"],
        covers_tag_prefixes=["theta4_pt"],  # twin epochs: theta4_pt .. theta4_pt3
        predecessor_key="theta-tail-sell",
        notes=(
            "LIVE since 2026-07-30 as the Stage 1 pilot (LIVE_STRATEGIES includes "
            "theta4); current twin epoch theta4_pt3 (fresh _pt3 twins cut at the "
            "2026-08-11 fee re-baseline). Uses the identical maker-sell convention "
            "as mmsell, so the realizable (fill-model) read gates alongside paper "
            "(borrowed-calibration first read: +0.51c realizable at 28% coverage "
            "vs +38.62c optimistic)."
        ),
        evidence=[
            dict(
                label="paper gate cleared 2026-07-28",
                evidence_class="context_only",
                source="paper_trades.strategy='theta4'",
                summary={
                    "n_at_clear": 95,
                    "n_at_note": 114,
                    "usd_per_trade": 0.392,
                    "usd_total": 44.70,
                },
                window_end=_day(2026, 7, 28),
            ),
        ],
        active=dict(
            version=dict(
                universe_selector="hourly crypto ladders, cheap band, final 35 minutes",
                entry_rule="model-anchored tail-sell with x2.0 fat-tail widening, edge=6c",
                exit_rule="hold to settlement",
                execution_style="maker",
                independent_variable=(
                    "fat-tail multiplier x2.0 vs the shelved theta family's model tails"
                ),
                control_required=False,
                control_exemption_reason=(
                    "single-arm live pilot: the paper twin is the execution control; "
                    "the family control (theta) is shelved collect-only"
                ),
                docs={"thesis": "docs/THETA_THESIS.md"},
            ),
            arms=[
                dict(
                    arm_key="theta4",
                    role="treatment",
                    description="fat-tail x2.0, edge=6c, cheap band / final 35 min",
                    params={"tail_multiplier": 2.0, "edge_cents": 6},
                    tag="theta4",
                ),
            ],
            gates=[
                dict(
                    gate_key="live_canary_keep",
                    kind="promotion",
                    from_state="LIVE_CANARY",
                    to_state="PRODUCTION",
                    spec={
                        "sample": {
                            "theta4": {"metric": "settled_trades", "op": ">=", "value": 80}
                        },
                        "pass_all": [
                            {
                                "metric": "pnl_cents_per_trade",
                                "arm": "theta4",
                                "op": ">",
                                "value": 0,
                            },
                            {
                                "metric": "realized_tail_hit_ratio_vs_modeled",
                                "arm": "theta4",
                                "op": "<=",
                                "value": 1.25,
                            },
                        ],
                    },
                    registered_at=_day(2026, 7, 30),
                    evidence_started_at=_day(2026, 7, 30),
                    notes=(
                        "registered bar was '> 0' against the pre-2026-08-11 "
                        "simulator that overcharged maker entries ~0.87c/ct: on "
                        "pre-boundary trades read as '> +0.87c' (BOOK_REGISTRY FEE "
                        "RE-BASELINE); post-boundary trades use '> 0' as written. "
                        "Fail both clauses -> theta family fully dead. Also gate on "
                        "the theta fill model's realizable c/trade, not paper alone."
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="theta4-live-1",
                    stage="LIVE_CANARY",
                    kind="live",
                    tags={"theta4": "theta4"},
                    config={"live_strategies_entry": "theta4", "stage": "Stage 1 pilot"},
                    started_at=_day(2026, 7, 30),
                    notes="registry date is day-precision (LIVE since 2026-07-30)",
                ),
                dict(
                    key="theta4-twin-pt3",
                    stage="LIVE_CANARY",
                    kind="paper_twin",
                    twin_of_key="theta4-live-1",
                    tags={"theta4": "theta4_pt3"},
                    config={"parameterized_to": "live knobs (twin harness)"},
                    started_at=_day(2026, 8, 11),
                    twin_started_at_from="theta4_pt3",  # exact instant in live_paper_twins
                    notes=(
                        "_pt3 epoch cut at the 2026-08-11 fee re-baseline so the "
                        "live comparison sits entirely on one fee model"
                    ),
                ),
            ],
        ),
    ),
    dict(
        key="mmsell-scheduled-settle-live",
        title="Lmmsell8 vs Lmmsell10 — scheduled-settle fill thesis, live",
        family="maker",
        origin="operator",
        state="LIVE_CANARY",
        legacy_class="ACTIVE_LIVE",
        integrity="B",
        hypothesis=(
            "Scheduled-settle series (no in-play informed flow) suffer less maker "
            "adverse selection, so mmsell8's allowlist realizes MORE of its paper "
            "edge live than the unfiltered price-ceiling book — a FILL claim paper "
            "cannot evaluate (the fill model projects both to ~+2.17c because it "
            "sees entry price only)."
        ),
        docs={
            "thesis": "docs/LIVE_PAPER_TWIN.md",  # §5b
            "studies": ["docs/MMSELL_VARIANTS_THESIS.md"],
        },
        covers_tags=["Lmmsell8", "Lmmsell10"],
        covers_tag_prefixes=["Lmmsell8_pt", "Lmmsell10_pt"],
        predecessor_key="mmsell-variants-2026-07",
        notes=(
            "Byte-identical replicas of mmsell8/mmsell10 armed live INSTEAD of "
            "their parents (2026-08-15) so live starts with a clean position book: "
            "the live mirror fires from the paper-open branch, and a tag with "
            "pre-arming paper positions hands live tickers it can never trade "
            "(measured 2026-08-15: mmsell10 had 87 such positions vs mmsell8's 3 — "
            "throttling the CONTROL ~29x harder than the treatment). Parents keep "
            "running as paper and remain the long-run control series. The pair IS "
            "the experiment."
        ),
        active=dict(
            version=dict(
                universe_selector=(
                    "treatment: lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY (mmsell8's "
                    "scheduled-settle allowlist); control: lo=5,hi=10,maxyes=7 "
                    "(mmsell10's price ceiling)"
                ),
                entry_rule="rest a buy-NO maker order at the no-bid (cheap-tail sell)",
                exit_rule="hold to settlement",
                sizing_rule="$2 / 2-contract clips, both arms, armed the same instant",
                execution_style="maker",
                independent_variable=(
                    "the scheduled-settle series allowlist (mmsell8's) — everything "
                    "else armed identically"
                ),
                held_constant=[
                    "clip size ($2 / 2 contracts)",
                    "arming instant",
                    "band mechanics and engine",
                    "risk envelope",
                    "platform snapshot",
                ],
                control_required=True,
                docs={"thesis": "docs/LIVE_PAPER_TWIN.md"},
            ),
            arms=[
                dict(
                    arm_key="scheduled_settle",
                    role="treatment",
                    description="replica of mmsell8 (scheduled-settle allowlist)",
                    params={"lo": 5, "hi": 12, "only": "BTCD+ETH+ASG+HRDERBY"},
                    tag="Lmmsell8",
                ),
                dict(
                    arm_key="price_ceiling",
                    role="control",
                    description="replica of mmsell10 (price ceiling, no type filter)",
                    params={"lo": 5, "hi": 10, "maxyes": 7},
                    tag="Lmmsell10",
                ),
            ],
            gates=[
                dict(
                    gate_key="live_canary_keep",
                    kind="promotion",
                    from_state="LIVE_CANARY",
                    to_state="PRODUCTION",
                    spec={
                        "sample": {
                            "scheduled_settle": {
                                "metric": "settled_contracts", "op": ">=", "value": 150
                            },
                            "price_ceiling": {
                                "metric": "settled_contracts", "op": ">=", "value": 150
                            },
                        },
                        "pass_all": [
                            {
                                "metric": "twin_live_winrate_gap_pp",
                                "arm": "scheduled_settle",
                                "op": "<=",
                                "value": 1.0,
                            },
                            {
                                "metric": "delta.live_cents_per_contract",
                                "treatment": "scheduled_settle",
                                "control": "price_ceiling",
                                "op": ">=",
                                "value": 1.0,
                            },
                        ],
                    },
                    registered_at=_day(2026, 8, 15),
                    evidence_started_at=_day(2026, 8, 15),
                    notes=(
                        "KEEP Lmmsell8 only if live win% tracks its twin within 1pp "
                        "AND live c/contract beats Lmmsell10 by >= +1.0c. If both "
                        "books show the same live-vs-twin gap, the scheduled-settle "
                        "thesis is dead and mmsell8 is mmsell10 with a narrower "
                        "ticker list. Read per-contract, not volume (parents' "
                        "legacy lockout makes early n incomparable)."
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="lmmsell-live-1",
                    stage="LIVE_CANARY",
                    kind="live",
                    tags={"scheduled_settle": "Lmmsell8", "price_ceiling": "Lmmsell10"},
                    config={
                        "live_strategies_entry": "theta4,Lmmsell8,Lmmsell10",
                        "clip": "$2 / 2 contracts",
                    },
                    started_at=_day(2026, 8, 15),
                    twin_started_at_from="Lmmsell10_pt3",  # arming instant == twin start
                    notes="armed 2026-08-15; exact instant resolved from the twin epoch row",
                ),
                dict(
                    key="lmmsell-twin-pt3",
                    stage="LIVE_CANARY",
                    kind="paper_twin",
                    twin_of_key="lmmsell-live-1",
                    tags={
                        "scheduled_settle": "Lmmsell8_pt3",
                        "price_ceiling": "Lmmsell10_pt3",
                    },
                    config={"parameterized_to": "live knobs (twin harness)"},
                    started_at=_day(2026, 8, 15),
                    twin_started_at_from="Lmmsell10_pt3",
                ),
            ],
        ),
    ),
    # ======================================================================
    # ACTIVE — paper books
    # ======================================================================
    dict(
        key="freeze-dark-window-pin",
        title="FREEZE — exchange-closure pin on commodity-hub grain/soft markets",
        family="structural",
        origin="operator",
        state="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="B",
        hypothesis=(
            "While a contract's settlement source is dark (CBOT grains / ICE "
            "softs), its favorite is underpriced relative to otherwise-comparable "
            "open-window favorites — the outcome is mechanically decided while "
            "retail keeps quoting the decided side cents from certainty."
        ),
        docs={"thesis": "docs/FREEZE_THESIS.md"},
        covers_tags=["freeze1", "freeze2", "freeze3", "freeze4"],
        notes=(
            "Built 2026-08-13 after scripts/kalshi_freeze_study.py cleared all "
            "five pre-registered probe gates on 2026-08-12 (P1 +16.10c/ct on "
            "39,429 post-pin trades; P2 +15.50c over the favorite control; P5 zero "
            "wrong pins). The backtest is NOT the verdict: its headline sits near "
            "a known lookahead-artifact magnitude and is one commodity's flow — "
            "the forward paper test is the point of the book."
        ),
        evidence=[
            dict(
                label="probe: kalshi_freeze_study 2026-08-12 (all five gates PASS)",
                evidence_class="certified_legacy_evidence",
                source="scripts/kalshi_freeze_study.py over backfill REST history",
                summary={
                    "pooled_cents_per_contract": 16.10,
                    "delta_vs_favorite_control_cents": 15.50,
                    "post_pin_trades": 39429,
                    "wrong_pins": 0,
                },
                window_end=_day(2026, 8, 12),
            ),
        ],
        active=dict(
            version=dict(
                universe_selector=(
                    "Kalshi commodity hub, groups grain+soft only (Pyth-continuous "
                    "metals/energy are never dark)"
                ),
                entry_rule=(
                    "taker-buy the favorite at ask when the remaining window is "
                    "fully source-dark and the discount clears the arm's bar"
                ),
                exit_rule="hold to settlement (no exits)",
                sizing_rule="sized to resting depth and the per-book order cap",
                execution_style="taker",
                independent_variable="source dark vs open condition at entry",
                held_constant=[
                    "market universe (grain/soft commodity hub)",
                    "favorite bar",
                    "taker entry at ask",
                    "hold to settlement",
                    "sizing rule",
                    "fee model",
                ],
                control_required=True,
                sample={"minimum_n": 150, "unit": "settled trades per arm"},
                docs={"thesis": "docs/FREEZE_THESIS.md"},
            ),
            arms=[
                dict(
                    arm_key="freeze1", role="treatment",
                    description="dark window, discount >= 3c (the thesis)",
                    params={"window": "dark", "min_discount_cents": 3}, tag="freeze1",
                ),
                dict(
                    arm_key="freeze2", role="secondary",
                    description="dark window, discount >= 8c (deep-discount slice)",
                    params={"window": "dark", "min_discount_cents": 8}, tag="freeze2",
                ),
                dict(
                    arm_key="freeze3", role="control",
                    description="OPEN window, same discount bar — THE CONTROL",
                    params={"window": "open", "min_discount_cents": 3}, tag="freeze3",
                ),
                dict(
                    arm_key="freeze4", role="secondary",
                    description="dark window, entry price capped at 95c",
                    params={
                        "window": "dark", "min_discount_cents": 3,
                        "max_price_cents": 95,
                    },
                    tag="freeze4",
                ),
            ],
            gates=[
                dict(
                    gate_key="paper_to_live_canary",
                    kind="promotion",
                    from_state="PAPER",
                    to_state="LIVE_CANARY",
                    spec={
                        "sample": {
                            "freeze1": {"metric": "settled_trades", "op": ">=", "value": 150},
                            "freeze3": {"metric": "settled_trades", "op": ">=", "value": 150},
                        },
                        "pass_all": [
                            {
                                "metric": "pnl_cents_per_trade",
                                "arm": "freeze1", "op": ">", "value": 0,
                            },
                            {
                                "metric": "delta.pnl_cents_per_trade",
                                "treatment": "freeze1", "control": "freeze3",
                                "op": ">=", "value": 3.0,
                            },
                        ],
                        "fail_any": [
                            {
                                "metric": "delta.pnl_cents_per_trade",
                                "treatment": "freeze1", "control": "freeze3",
                                "op": "<=", "value": 0,
                            },
                        ],
                    },
                    registered_at=_day(2026, 8, 13),
                    evidence_started_at=_day(2026, 8, 13),
                    notes=(
                        "Gate on freeze1 MINUS freeze3, never freeze1 alone: every "
                        "arm is a favorite-buy (the shape tfav died in at -3.6c), "
                        "so the open-window control is the whole experiment. KILL "
                        "the family if freeze1 <= freeze3. freeze2/freeze4 are read "
                        "only against freeze1 to size bands; they cannot promote."
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="freeze-paper-legacy-1",
                    stage="PAPER",
                    kind="paper",
                    tags={
                        "freeze1": "freeze1", "freeze2": "freeze2",
                        "freeze3": "freeze3", "freeze4": "freeze4",
                    },
                    config={"engine": "shared paper engine, no-timeout set"},
                    started_at=_day(2026, 8, 13),
                ),
            ],
        ),
    ),
    dict(
        key="mmsell-wide-control",
        title="mmsell — the wide-band control book (measuring stick)",
        family="maker",
        origin="operator",
        state="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="B",
        hypothesis=(
            "Reference book: favorite-longshot maker-sell across the wide cheap "
            "band (mostly sports). Not a promotion candidate — the baseline every "
            "band/type experiment is defined against."
        ),
        docs={
            "thesis": "docs/MMSELL_LIVE_PLAN.md",
            "studies": [
                "docs/MMSELL_LIVE_POSTMORTEM.md",
                "docs/MMSELL_FILL_MODEL.md",
                "docs/edge_research.md",
            ],
        },
        covers_tags=["mmsell"],
        notes=(
            "[2026-08-12] KEPT DELIBERATELY: the scan funnel's in_band counter is "
            "scoped to this book and every band/type experiment was defined "
            "against it. It is not a live candidate and never was (-1.80c/trade, "
            "~-0.93c after the fee correction) — deleting it would silently rebase "
            "the telemetry. Descendant history: mmsell3 passed its paper gate, "
            "went LIVE 2026-07-13, closed 2026-07-19 at n=359, +0.18c/trade "
            "realized ~ breakeven; divergence root-caused as maker adverse "
            "selection (docs/MMSELL_FILL_MODEL.md)."
        ),
        active=dict(
            version=dict(
                universe_selector="wide cheap band (lo=5,hi=20 heritage), mostly sports",
                entry_rule="rest a buy-NO maker order at the no-bid",
                exit_rule="hold to settlement",
                execution_style="maker",
                independent_variable=(
                    "none — this book is the reference; experiments vary against it"
                ),
                control_required=False,
                control_exemption_reason=(
                    "this book IS the control/reference series for the mmsell "
                    "family and the scan-funnel telemetry"
                ),
                docs={"thesis": "docs/MMSELL_LIVE_PLAN.md"},
            ),
            arms=[
                dict(
                    arm_key="mmsell", role="control",
                    description="wide-band reference book",
                    params=None, tag="mmsell",
                ),
            ],
            gates=[],  # deliberately none: not a promotion candidate
            deployments=[
                dict(
                    key="mmsell-control-paper-legacy-1",
                    stage="PAPER",
                    kind="paper",
                    tags={"mmsell": "mmsell"},
                    config=None,
                    started_at=None,  # long-running; original start not certified
                    notes="grandfathered reference book; original start predates registry",
                ),
            ],
        ),
    ),
    dict(
        key="mmsell-variants-2026-07",
        title="mmsell5–8 — by-sport / by-market-type decomposition variants",
        family="maker",
        origin="operator",
        state="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="B",
        hypothesis=(
            "Slices of the cheap-tail maker-sell chosen from the live+paper "
            "by-sport / by-market-type decomposition (type-allowlist, ultra-cheap, "
            "short-dated, scheduled-settle) retain the fillable edge."
        ),
        docs={
            "thesis": "docs/MMSELL_VARIANTS_THESIS.md",
            "studies": ["docs/MMSELL_FILL_MODEL.md"],
        },
        covers_tags=["mmsell5", "mmsell6", "mmsell7", "mmsell8"],
        notes=(
            "Built 2026-07-15. Gate on REALIZABLE (live-calibrated) per-trade — "
            "mmsell fill model, never blended paper (blended paper overstates edge "
            "by the maker adverse-selection gap; mmsell6 was a blended-paper "
            "promote candidate and is a MIRAGE under the fill model, +1.63c opt -> "
            "-0.43c realizable). [2026-08-12] mmsell4 RETIRED from this cohort "
            "(+0.04c realizable — see experiment mmsell4-skip-list); mmsell6/7 "
            "KEPT but UNDECIDED at +0.64c/+0.59c fee-corrected. mmsell8's "
            "scheduled-settle thesis is now ALSO under live test as "
            "mmsell-scheduled-settle-live."
        ),
        active=dict(
            version=dict(
                universe_selector=(
                    "per-variant slices of the cheap band (see "
                    "docs/MMSELL_VARIANTS_THESIS.md for per-variant definitions)"
                ),
                entry_rule="rest a buy-NO maker order at the no-bid",
                exit_rule="hold to settlement",
                execution_style="maker",
                independent_variable="the variant's slice filter (per-variant)",
                control_required=False,
                control_exemption_reason=(
                    "gated on absolute realizable per-trade via the live-calibrated "
                    "fill model, not an in-experiment control arm"
                ),
                docs={"thesis": "docs/MMSELL_VARIANTS_THESIS.md"},
            ),
            arms=[
                dict(arm_key="mmsell5", role="secondary",
                     description="decomposition variant (see thesis doc)",
                     params=None, tag="mmsell5"),
                dict(arm_key="mmsell6", role="secondary",
                     description="decomposition variant (MIRAGE under the fill model)",
                     params=None, tag="mmsell6"),
                dict(arm_key="mmsell7", role="secondary",
                     description="decomposition variant (see thesis doc)",
                     params=None, tag="mmsell7"),
                dict(arm_key="mmsell8", role="secondary",
                     description="scheduled-settle allowlist variant",
                     params=None, tag="mmsell8"),
            ],
            gates=[
                dict(
                    gate_key="paper_to_live_canary",
                    kind="promotion",
                    from_state="PAPER",
                    to_state="LIVE_CANARY",
                    spec={
                        "sample": {
                            "mmsell5": {"metric": "settled_trades", "op": ">=", "value": 100},
                            "mmsell6": {"metric": "settled_trades", "op": ">=", "value": 150},
                            "mmsell7": {"metric": "settled_trades", "op": ">=", "value": 150},
                            "mmsell8": {"metric": "settled_trades", "op": ">=", "value": 100},
                        },
                        "pass_all": [
                            {
                                "metric": "realizable_cents_per_trade",
                                "op": ">", "value": 0,
                            },
                        ],
                    },
                    registered_at=_day(2026, 7, 15),
                    evidence_started_at=_day(2026, 7, 15),
                    notes=(
                        "realizable = mmsell_fill_model's live-calibrated column; "
                        "full per-variant gates in docs/MMSELL_VARIANTS_THESIS.md"
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="mmsell-variants-paper-legacy-1",
                    stage="PAPER",
                    kind="paper",
                    tags={
                        "mmsell5": "mmsell5", "mmsell6": "mmsell6",
                        "mmsell7": "mmsell7", "mmsell8": "mmsell8",
                    },
                    config=None,
                    started_at=_day(2026, 7, 15),
                ),
            ],
        ),
    ),
    dict(
        key="mmsell-price-ceiling",
        title="mmsell9/mmsell10 — the 2x2 cheap-cell and price-ceiling books",
        family="maker",
        origin="operator",
        state="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="B",
        hypothesis=(
            "From the live 2x2 (price x type, n=232): cheap (yes<=7c) x non-winner "
            "is the +EV cell and rich h2h-winners are the drag — so capping entry "
            "price keeps the fillable cheap cells and the realizable edge."
        ),
        docs={
            "thesis": "docs/MMSELL_VARIANTS_THESIS.md",
            "studies": ["docs/MMSELL_FILL_MODEL.md"],
        },
        covers_tags=["mmsell9", "mmsell10"],
        notes=(
            "Built 2026-07-18 (2nd cohort). Under the fill model mmsell10 is the "
            "one true live candidate (100% coverage, +3.80c opt -> +1.40c "
            "realizable). [2026-08-12] mmsell11 RETIRED from this cohort (see "
            "experiment mmsell11-no-late-entry). mmsell10's live validation now "
            "runs as Lmmsell10, the control arm of mmsell-scheduled-settle-live."
        ),
        active=dict(
            version=dict(
                universe_selector="cheap band; mmsell9 = sweet-spot cell, mmsell10 = maxyes ceiling",
                entry_rule="rest a buy-NO maker order at the no-bid",
                exit_rule="hold to settlement",
                execution_style="maker",
                independent_variable="entry-price ceiling (maxyes) vs cell selection",
                control_required=False,
                control_exemption_reason=(
                    "gated on absolute realizable per-trade via the live-calibrated "
                    "fill model, not an in-experiment control arm"
                ),
                docs={"thesis": "docs/MMSELL_VARIANTS_THESIS.md"},
            ),
            arms=[
                dict(arm_key="mmsell9", role="secondary",
                     description="sweet-spot cell (cheap x non-winner)",
                     params=None, tag="mmsell9"),
                dict(arm_key="mmsell10", role="secondary",
                     description="entry-price ceiling only (lo=5,hi=10,maxyes=7)",
                     params={"lo": 5, "hi": 10, "maxyes": 7}, tag="mmsell10"),
            ],
            gates=[
                dict(
                    gate_key="paper_to_live_canary",
                    kind="promotion",
                    from_state="PAPER",
                    to_state="LIVE_CANARY",
                    spec={
                        "pass_all": [
                            {
                                "metric": "realizable_cents_per_trade",
                                "op": ">", "value": 0,
                            },
                        ],
                    },
                    registered_at=_day(2026, 7, 18),
                    evidence_started_at=_day(2026, 7, 18),
                    notes=(
                        "sample floor per docs/MMSELL_VARIANTS_THESIS.md (2nd "
                        "cohort); registry records no explicit n on this row, so "
                        "none is invented here"
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="mmsell-ceiling-paper-legacy-1",
                    stage="PAPER",
                    kind="paper",
                    tags={"mmsell9": "mmsell9", "mmsell10": "mmsell10"},
                    config=None,
                    started_at=_day(2026, 7, 18),
                ),
            ],
        ),
    ),
    dict(
        key="mmsell-anchor-vol-entry",
        title="mmsellA4 — volatility ENTRY gate on the mmsell10 base",
        family="maker",
        origin="operator",
        state="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="B",
        hypothesis=(
            "Skipping the mmsell10 entry when the yes-mid range over the last 6 "
            "recorded candidate ticks >= 6c avoids the active-tape losses (backtest: "
            "calm tape +2.85..+5.25c at 100% win, active tape -39c, n=13-17)."
        ),
        docs={"thesis": "docs/MMSELL_ANCHOR_SET.md"},
        covers_tags=["mmsellA4"],
        notes=(
            "[2026-08-13 GATE READING] both halves measurable and both PASS on the "
            "post-fee-fix window (>= 2026-08-11 15:00 UTC, the measured deploy "
            "boundary): +4.05c/trade at n=128 vs the mmsell10 control's +2.75c at "
            "n=162 (+1.30c, clears >=+1.0c); rejection rate 23.6% (clears >=15%). "
            "Held short of KEEP deliberately: n=128 on ~2 days, relative gate with "
            "a moving control — re-read at n>=300 before promoting."
        ),
        active=dict(
            version=dict(
                universe_selector="mmsell10 base entry (lo=5,hi=10,maxyes=7)",
                entry_rule=(
                    "mmsell10 entry, SKIPPED when yes-mid range over the last 6 "
                    "candidate ticks >= 6c (gate does not fire on <3 ticks)"
                ),
                exit_rule="hold to settlement",
                execution_style="maker",
                independent_variable="the volatility entry gate (only difference vs mmsell10)",
                control_required=True,
                control_exemption_reason=(
                    "control is the mmsell10 BOOK (same entry, no vol gate) — a "
                    "cross-experiment control read over the same window; no "
                    "in-version control arm exists"
                ),
                docs={"thesis": "docs/MMSELL_ANCHOR_SET.md"},
            ),
            arms=[
                dict(
                    arm_key="vol_entry_gate", role="treatment",
                    description="mmsell10 entry behind the 6-tick/6c calm-tape gate",
                    params={"range_ticks": 6, "range_cents_max": 6}, tag="mmsellA4",
                ),
            ],
            gates=[
                dict(
                    gate_key="paper_keep",
                    kind="promotion",
                    from_state="PAPER",
                    to_state="LIVE_CANARY",
                    spec={
                        "sample": {
                            "vol_entry_gate": {
                                "metric": "settled_trades", "op": ">=", "value": 100
                            }
                        },
                        "pass_all": [
                            {
                                "metric": "delta.mean_cents_per_trade_vs_mmsell10",
                                "arm": "vol_entry_gate", "op": ">=", "value": 1.0,
                            },
                            {
                                "metric": "candidate_rejection_rate_pct",
                                "arm": "vol_entry_gate", "op": ">=", "value": 15.0,
                            },
                        ],
                        "fail_any": [
                            {
                                "metric": "delta.mean_cents_per_trade_vs_mmsell10",
                                "arm": "vol_entry_gate", "op": "<=", "value": 0,
                            },
                            {
                                "metric": "candidate_rejection_rate_pct",
                                "arm": "vol_entry_gate", "op": "<", "value": 5.0,
                            },
                        ],
                    },
                    registered_at=_day(2026, 7, 30),
                    evidence_started_at=FEE_BOUNDARY,
                    notes=(
                        "rejection <5% = gate inert (book is a duplicate of "
                        "mmsell10). Rejection measured by entry-count differential "
                        "(skipped_vol_gate is counted but never persisted) — valid "
                        "only while A4 and mmsell10 differ ONLY by the gate. "
                        "Evidence floor = the measured 2026-08-11 15:00 UTC fee "
                        "boundary, the floor the registry's own reading uses."
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="mmsellA4-paper-legacy-1",
                    stage="PAPER",
                    kind="paper",
                    tags={"vol_entry_gate": "mmsellA4"},
                    config=None,
                    started_at=_day(2026, 7, 30),
                ),
            ],
        ),
    ),
    dict(
        key="mmsell-anchor-strangle",
        title="mmsellA5 — two-sided short strangle on cheap event tails",
        family="maker",
        origin="operator",
        state="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="B",
        hypothesis=(
            "Selling BOTH mutually-exclusive cheap tails of one event (cheap YES "
            "on a high strike + cheap NO on a low strike) collects two premiums "
            "against at most one loss — one settlement can never lose both legs — "
            "improving the payoff SHAPE rather than trading mean for variance."
        ),
        docs={
            "thesis": "docs/MMSELL_ANCHOR_SET.md",
            "studies": ["docs/MMSELL_ROADMAP.md"],
        },
        covers_tags=["mmsellA5"],
        notes=(
            "n=0 until the 2026-08-03 gate fix (pairing gate read integer-cent "
            "quote keys the events payload omits). Second bug found 2026-08-14 "
            "once NFL supply exposed it: _event_has_both_tails certified the EVENT "
            "not a matched PAIR — only 27% of events (192/715) had actually taken "
            "both sides; fixed by capping one leg per side per event. PAIRING "
            "BOUNDARY 2026-08-14T14:31:12Z (PR #213 merge — provisional): "
            "pre-boundary volume is real P&L but NOT clean pair evidence; pair "
            "reads toward n>=82 must filter created_at >= that instant. With "
            "stops, vol gates, late adds and per-event caps all measured dead, "
            "the strangle is the only STRUCTURAL tail reducer left. Caveat: "
            "both-tails-cheap selects for low-volatility events, so A5 and A4 are "
            "probably not independent edges. Fee re-baseline note: the 93.9% "
            "break-even bound was computed with the ~1c taker fee; with the maker "
            "fee it is 93.1%, so the registered bar is conservative."
        ),
        active=dict(
            version=dict(
                universe_selector=(
                    "events with BOTH tails under the cap (mirror band); a lone "
                    "tail is refused (would duplicate mmsell10)"
                ),
                entry_rule=(
                    "sell both mutually-exclusive cheap tails of one event; max "
                    "one leg per side per event"
                ),
                exit_rule="hold to settlement",
                execution_style="maker",
                independent_variable="two-sided paired entry vs single-tail selling",
                control_required=False,
                control_exemption_reason=(
                    "gated against the pre-registered 93.9% break-even pair win "
                    "rate bound, not a control book"
                ),
                docs={"thesis": "docs/MMSELL_ANCHOR_SET.md"},
            ),
            arms=[
                dict(
                    arm_key="strangle", role="treatment",
                    description="both-tails cheap strangle, one leg per side per event",
                    params={"pairing": "one leg per side per event"}, tag="mmsellA5",
                ),
            ],
            gates=[
                dict(
                    gate_key="paper_keep",
                    kind="promotion",
                    from_state="PAPER",
                    to_state="LIVE_CANARY",
                    spec={
                        "sample": {
                            "strangle": {"metric": "clean_pairs", "op": ">=", "value": 82}
                        },
                        "pass_all": [
                            {
                                "metric": "pair_win_rate_95lb_pct",
                                "arm": "strangle", "op": ">", "value": 93.9,
                            },
                        ],
                        "fail_any": [
                            {
                                "metric": "pair_win_rate_95lb_pct",
                                "arm": "strangle", "op": "<=", "value": 93.9,
                            },
                        ],
                    },
                    registered_at=_day(2026, 7, 30),
                    evidence_started_at=A5_PAIRING_BOUNDARY,
                    notes=(
                        "KILL also if the paired entry rate cannot reach 82 pairs "
                        "in a quarter (flow kill — not expressible as a metric "
                        "clause yet). Evidence floor = the 2026-08-14T14:31:12Z "
                        "pairing boundary (PR #213, provisional): no offset "
                        "converts a one-sided-legs sample into a paired one."
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="mmsellA5-paper-legacy-1",
                    stage="PAPER",
                    kind="paper",
                    tags={"strangle": "mmsellA5"},
                    config=None,
                    started_at=_day(2026, 7, 30),
                ),
            ],
        ),
    ),
    dict(
        key="mmsell-type-tight",
        title="Tmmsell survivors — tight band sliced by contract structure",
        family="maker",
        origin="operator",
        state="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="B",
        hypothesis=(
            "Within the tight band (lo=5,hi=10,maxyes=7), selecting on CONTRACT "
            "STRUCTURE (market-type taxonomy) adds edge over the unfiltered "
            "price-ceiling book."
        ),
        docs={
            "thesis": "docs/MMSELL_TYPE_BOOKS.md",
            "studies": ["docs/MMSELL_MARKET_TYPES.md"],
        },
        covers_tags=["Tmmsell1", "Tmmsell2", "Tmmsell5", "Tmmsell6"],
        notes=(
            "Built 2026-08-03; Tmmsell3/Tmmsell4 RETIRED 2026-08-12 (see "
            "experiment mmsell-type-tight-retired). COHORT RESTARTED at the "
            "2026-08-13T18:09:40Z taxonomy boundary: until then each book was "
            "offered ~half its intended universe, so n restarts at 0 and "
            "pre-boundary trades are DROPPED, not adjusted. The tags deliberately "
            "did not change (selection rule byte-identical either side). The 46 "
            "newly classified series were never in the census, so post-boundary "
            "flow is the family's first genuinely out-of-sample test. The family "
            "result so far: every T book that reached n beat the control, none by "
            "the gate's margin — the price band was doing the work."
        ),
        active=dict(
            version=dict(
                universe_selector="tight band (lo=5,hi=10,maxyes=7) x market-type allowlists",
                entry_rule="rest a buy-NO maker order at the no-bid",
                exit_rule="hold to settlement",
                execution_style="maker",
                independent_variable="the contract-structure (market-type) filter",
                control_required=True,
                control_exemption_reason=(
                    "control is the mmsell10 BOOK (same band, no type filter) — "
                    "cross-experiment control read over the same window"
                ),
                docs={"thesis": "docs/MMSELL_TYPE_BOOKS.md"},
            ),
            arms=[
                dict(arm_key="Tmmsell1", role="secondary",
                     description="price_strike types", params=None, tag="Tmmsell1"),
                dict(arm_key="Tmmsell2", role="secondary",
                     description="mention types", params=None, tag="Tmmsell2"),
                dict(arm_key="Tmmsell5", role="secondary",
                     description="the no-clock book", params=None, tag="Tmmsell5"),
                dict(arm_key="Tmmsell6", role="secondary",
                     description="both-band survivors (positive edge in BOTH bands)",
                     params=None, tag="Tmmsell6"),
            ],
            gates=[
                dict(
                    gate_key="paper_keep",
                    kind="promotion",
                    from_state="PAPER",
                    to_state="LIVE_CANARY",
                    spec={
                        "sample": {
                            "Tmmsell1": {"metric": "settled_trades", "op": ">=", "value": 100},
                            "Tmmsell2": {"metric": "settled_trades", "op": ">=", "value": 100},
                            "Tmmsell5": {"metric": "settled_trades", "op": ">=", "value": 100},
                            "Tmmsell6": {"metric": "settled_trades", "op": ">=", "value": 100},
                        },
                        "pass_all": [
                            {"metric": "pnl_cents_per_trade", "op": ">", "value": 0},
                            {
                                "metric": "delta.pnl_cents_per_trade_vs_mmsell10",
                                "op": ">=", "value": 1.0,
                            },
                            {
                                "metric": "realizable_cents_per_trade",
                                "op": ">", "value": 0,
                            },
                        ],
                    },
                    registered_at=_day(2026, 8, 3),
                    evidence_started_at=TAXONOMY_BOUNDARY,
                    notes=(
                        "[2026-08-12 caveat] do NOT promote a T book on the "
                        "realizable clause alone: the fill model projects entry "
                        "price only, so every tight-band book INCLUDING the "
                        "control lands at roughly the same number — the clause "
                        "does no discriminating work. Fee re-read: pre-2026-08-11 "
                        "absolute clauses read '> +0.87c'. Evidence floor = the "
                        "2026-08-13T18:09:40Z cohort restart (pre-boundary trades "
                        "dropped)."
                    ),
                ),
            ],
            deployments=[
                dict(
                    key="tmmsell-paper-legacy-1",
                    stage="PAPER",
                    kind="paper",
                    tags={
                        "Tmmsell1": "Tmmsell1", "Tmmsell2": "Tmmsell2",
                        "Tmmsell5": "Tmmsell5", "Tmmsell6": "Tmmsell6",
                    },
                    config=None,
                    started_at=_day(2026, 8, 3),
                ),
            ],
        ),
    ),
    # ======================================================================
    # PAUSED — shelved but not killed
    # ======================================================================
    dict(
        key="theta-tail-sell",
        title="theta family — model-anchored crypto tail-sell (control + revisions)",
        family="theta",
        origin="operator",
        state="PAUSED",
        paused_from="PAPER",
        legacy_class="ACTIVE_PAPER",
        integrity="C",
        hypothesis=(
            "A model-anchored tail-sell on hourly crypto ladders earns the theta "
            "of overpriced cheap tails."
        ),
        docs={"thesis": "docs/THETA_THESIS.md"},
        covers_tags=["theta", "theta1", "theta2", "theta3"],
        verdict=(
            "SHELVED 2026-07-09 — failed 'positive AND calibrated at n>=60'; "
            "collectors kept running, entries off. Not killed: the fat-tail "
            "successor (theta4-fat-tail) carries the family forward."
        ),
        notes=(
            "Imported PAUSED (from PAPER): entries are off but the book was "
            "deliberately shelved, not retired — its data collectors still run. "
            "Reconstruction is deliberately minimal (integrity C): no version "
            "contract is certified for these shelved tags."
        ),
        evidence=[
            dict(
                label="shelve verdict 2026-07-09",
                evidence_class="context_only",
                source="paper_trades.strategy IN ('theta','theta1','theta2','theta3')",
                summary={"gate": "positive AND calibrated at n>=60", "outcome": "failed"},
                window_end=_day(2026, 7, 9),
            ),
        ],
    ),
    # ======================================================================
    # RETIRED — minimal §22.5 stubs (identity, verdict, evidence; no
    # arm/epoch reconstruction for dead books)
    # ======================================================================
    dict(
        key="pin15",
        title="15-min crypto endgame observation-pin",
        family="observation_pin",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis=(
            "taker-buy the drift-favored side 2-3 min before close, held to the "
            "60s-average settle"
        ),
        docs={
            "thesis": "docs/PIN15_THESIS.md",
            "verdict": "docs/RESEARCH_JOURNAL.md (2026-07-16)",
        },
        covers_tags=["pin15"],
        verdict=(
            "Gate FAILED at n=405: the target T~120-180s window nets only "
            "+0.27c/trade (< the +1.5c bar); the whole book's cumulative loss "
            "traces to the T 60-120s sub-window (-53c/trade, n=37). "
            "pin15_enabled=False since 2026-07-16."
        ),
        retired_at=_day(2026, 7, 16),
        evidence=[
            dict(
                label="paper book lifetime (as recorded at the kill)",
                evidence_class="context_only",
                source="paper_trades.strategy='pin15'",
                summary={
                    "n": 405,
                    "target_window_cents_per_trade": 0.27,
                    "bar_cents_per_trade": 1.5,
                    "t60_120s_cents_per_trade": -53.0,
                    "t60_120s_n": 37,
                },
                window_end=_day(2026, 7, 16),
            ),
        ],
    ),
    dict(
        key="mmsell-scan-depth",
        title="mmsell10d/mmsell10e — scan-depth experiment (225/300 events deep)",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis=(
            "The maker edge in the thinner tail of the volume-ranked board "
            "matches the top 150 events."
        ),
        docs={"thesis": "docs/MMSELL_SCAN_DEPTH.md"},
        covers_tags=["mmsell10d", "mmsell10e"],
        verdict=(
            "RETIRED 2026-08-14 — MEASURED AND FAILED. Monotonic decay by depth: "
            "150 -> +1.34c, 225 (mmsell10d) -> +0.70c (n=243, -0.64c vs control), "
            "300 (mmsell10e) -> -1.05c (n=133, -1.94c vs control). mmsell10d "
            "failed the KEEP clause and ALSO lost on total dollars (243 x +0.70c "
            "= +$1.70 vs the control's 177 x +1.34c = +$2.37); mmsell10e retired "
            "below its n on the absolute clause. Mechanism: the volume-rank cut "
            "selects for LIQUID events — exactly what a resting maker needs. Rate "
            "limits were NOT the constraint (429s zero since the advanced grant). "
            "Also CLOSES the inline quote pre-filter (docs/MMSELL_QUOTE_PARITY.md). "
            "Revive only on a mechanically different (taker) entry."
        ),
        retired_at=_day(2026, 8, 14),
        evidence=[
            dict(
                label="depth ladder verdict (each book's own window vs mmsell10)",
                evidence_class="context_only",
                source="paper_trades.strategy IN ('mmsell10d','mmsell10e')",
                summary={
                    "depth150_cents": 1.34,
                    "depth225_cents": 0.70, "depth225_n": 243,
                    "depth300_cents": -1.05, "depth300_n": 133,
                },
                window_end=_day(2026, 8, 14),
            ),
        ],
    ),
    dict(
        key="mmsell-first-cohort",
        title="mmsell1/2/3 — first-cohort band variants",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis="band variants of the cheap-tail sell (lo=5,hi=20 / lo=10,hi=20 / lo=5,hi=10)",
        docs={
            "thesis": "docs/MMSELL_VARIANTS_THESIS.md",
            "verdict": "docs/MMSELL_LIVE_POSTMORTEM.md",
        },
        covers_tags=["mmsell1", "mmsell2", "mmsell3"],
        verdict=(
            "RETIRED 2026-08-12 — breakeven, superseded by mmsell10. mmsell3 is "
            "the one book with live ground truth: real money 2026-07-13..19, "
            "+0.18c/trade at n=359 (91.1% win) — breakeven, not edge. Fee-corrected "
            "realizable +0.02c/contract; mmsell1/mmsell2 sit at 49.6%/19.5% fill "
            "coverage (most trades at prices with no live fill evidence). Revive "
            "only on a mechanically different entry."
        ),
        retired_at=_day(2026, 8, 12),
        evidence=[
            dict(
                label="mmsell3 live ground truth (2026-07-13..19)",
                evidence_class="context_only",
                source="live_orders/fills strategy='mmsell3'; docs/MMSELL_LIVE_POSTMORTEM.md",
                summary={"n": 359, "cents_per_trade": 0.18, "win_pct": 91.1},
                window_start=_day(2026, 7, 13),
                window_end=_day(2026, 7, 19),
            ),
        ],
    ),
    dict(
        key="mmsell4-skip-list",
        title="mmsell4 — the skip-list (clean-book) variant",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis="excluding the skip-list slice separates a cleaner book from its parent",
        docs={"thesis": "docs/MMSELL_VARIANTS_THESIS.md"},
        covers_tags=["mmsell4"],
        verdict=(
            "RETIRED 2026-08-12 at +0.04c realizable — the skip-list variant "
            "never separated from its parent."
        ),
        retired_at=_day(2026, 8, 12),
        evidence=[
            dict(
                label="fee-corrected realizable at retirement",
                evidence_class="context_only",
                source="paper_trades.strategy='mmsell4' via mmsell_fill_model",
                summary={"realizable_cents_per_trade": 0.04},
                window_end=_day(2026, 8, 12),
            ),
        ],
    ),
    dict(
        key="mmsell11-no-late-entry",
        title="mmsell11 — no-late-entry filter (htcmin=6)",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis="excluding late entries (htcmin=6) improves per-trade economics",
        docs={"thesis": "docs/MMSELL_VARIANTS_THESIS.md"},
        covers_tags=["mmsell11"],
        verdict=(
            "RETIRED 2026-08-12 at +0.04c realizable: the no-late-entry filter "
            "cost flow without moving per-trade — the MIRAGE verdict the registry "
            "row predicted (+2.38c opt -> -0.86c realizable), confirmed against "
            "the corrected fee model."
        ),
        retired_at=_day(2026, 8, 12),
    ),
    dict(
        key="mmsell-maker-offset-ab",
        title="mmsell10a/mmsell10b — maker offset queue A/B (live)",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis=(
            "Paying +1 cent improves maker queue placement enough to improve "
            "realizable economics, not merely fill rate."
        ),
        docs={
            "thesis": "docs/MMSELL_OFFSET_AB.md",
            "studies": ["docs/LIVE_QUEUE_POSITION.md"],
        },
        covers_tags=["mmsell10a", "mmsell10b"],
        covers_tag_prefixes=["mmsell10a_pt", "mmsell10b_pt"],
        verdict=(
            "RETIRED FROM LIVE 2026-08-14 (live 2026-08-04..14). KILL the 1c "
            "offset: gap = twin c/ct - live c/ct; measured gap_a -0.06c (twin "
            "+0.78 vs live +0.84, n=420 — live BEAT its twin) vs gap_b +3.45c "
            "(twin +0.16 vs live -3.29, n=384); gap_b >= gap_a by 3.51c. The "
            "offset worked mechanically and that killed it: fill rate 58.2% vs "
            "55.3%, front-of-queue 77.0% vs 35.9%, live win% -4.8pp vs its twin — "
            "bought adverse selection. Honesty notes: mmsell10b killed at n=384 "
            "against its n>=400 bar (operator ended live mmsell trading; nothing "
            "may later be promoted on a claim the bar was cleared); the "
            "between-arm P&L race was never decidable (needs ~30,391 "
            "contracts/arm). mmsell10a was PROFITABLE when retired (+0.84c/ct, "
            "n=420) — ended by operator decision to clear the deck, not failure; "
            "revive as the maker control if a future live maker test needs one. "
            "Do not re-arm MMSELL_LIVE_OFFSET_AB_ARMS."
        ),
        retired_at=_day(2026, 8, 14),
        evidence=[
            dict(
                label="live A/B outcome (2026-08-04..14)",
                evidence_class="context_only",
                source=(
                    "live_orders/fills strategies mmsell10a/mmsell10b + twins via "
                    "live_paper_twins; scripts/mmsell_offset_ab.py; "
                    "live_order_queue_ticks"
                ),
                summary={
                    "gap_cents": {"mmsell10a": -0.06, "mmsell10b": 3.45},
                    "delta_gap_cents": 3.51,
                    "n": {"mmsell10a": 420, "mmsell10b": 384},
                    "fill_rate_pct": {"mmsell10a": 55.3, "mmsell10b": 58.2},
                    "front_of_queue_pct": {"mmsell10a": 35.9, "mmsell10b": 77.0},
                },
                window_start=_day(2026, 8, 4),
                window_end=_day(2026, 8, 14),
            ),
        ],
    ),
    dict(
        key="mmsell-anchor-stops",
        title="mmsellA1/A2/A3 — confirmed yes-BID stop-loss level sweep",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis=(
            "A confirmed catastrophic stop (yes-BID >= 12/20/30c for K=2 cycles) "
            "on the mmsell10 base truncates the tail without giving up the mean."
        ),
        docs={
            "thesis": "docs/MMSELL_ANCHOR_SET.md",
            "studies": ["docs/MMSELL_CRYPTO_STUDY.md", "docs/MMSELL_ROADMAP.md"],
        },
        covers_tags=["mmsellA1", "mmsellA2", "mmsellA3"],
        verdict=(
            "RETIRED 2026-08-12 — the pre-registered gate FAILED on the half that "
            "mattered: read as status IN ('settled','closed_sl'), all three stop "
            "levels are strongly negative AND the stop makes the 5th-pctile tail "
            "WORSE than holding (fires on 52% of positions; A1 -4.16c/trade vs "
            "the mmsell10 control's +3.14c; p5 -19.0 vs +5.0). The crypto "
            "backtest that motivated these did not transfer (htc<1h continuous "
            "paths vs htc>=1h sports that JUMP). Relative gate — the fee "
            "correction does not touch it. Revive only with a stop that cannot "
            "fire on a non-informative quote, plus fresh pre-registration."
        ),
        retired_at=_day(2026, 8, 12),
    ),
    dict(
        key="mmsell-type-wide",
        title="Wmmsell1–8 — wide band sliced by contract structure",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis=(
            "Within the WIDE band (lo=5,hi=40, no maxyes), selecting on contract "
            "structure isolates the profitable types."
        ),
        docs={
            "thesis": "docs/MMSELL_TYPE_BOOKS.md",
            "studies": ["docs/MMSELL_MARKET_TYPES.md"],
        },
        covers_tags=[
            "Wmmsell1", "Wmmsell2", "Wmmsell3", "Wmmsell4",
            "Wmmsell5", "Wmmsell6", "Wmmsell7", "Wmmsell8",
        ],
        verdict=(
            "RETIRED 2026-08-12 as UNMEASURABLE — not disproven. (1) The control "
            "(mmsell) LOSES money (-1.80c/trade, ~-0.93c fee-corrected) and "
            "beating a losing control is not an edge; (2) fill coverage 19-41%: "
            "the wide band's 10-40c entries have NO live fill evidence, so the "
            "realizable number is an estimate over a minority of each book. The "
            "tight-band Tmmsell family tests the same hypothesis at 99-100% "
            "coverage — the axis survives, only the band dies. REVIVE if live "
            "maker fills in the 10-40c band are ever obtained; that evidence, not "
            "more paper, is the blocker."
        ),
        retired_at=_day(2026, 8, 12),
    ),
    dict(
        key="mmsell-type-tight-retired",
        title="Tmmsell3/Tmmsell4 — retired members of the tight type family",
        family="maker",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis=(
            "T3 (top-3 in-play types by tight-band edge) and T4 (all minus the 5 "
            "negative types) beat the unfiltered tight-band control."
        ),
        docs={"thesis": "docs/MMSELL_TYPE_BOOKS.md"},
        covers_tags=["Tmmsell3", "Tmmsell4"],
        verdict=(
            "RETIRED 2026-08-12 — MEASURED AND FAILED (unlike the wide band's "
            "UNMEASURABLE verdict, which licenses a revival and this does not): "
            "both reached n and beat mmsell10 by far less than the gate's +1.0c. "
            "The relative gate is untouched by the fee correction. The family "
            "lesson: once you are already selling <=7c tails, contract structure "
            "adds ~nothing — the price band was doing the work. Revival requires "
            "clearing +1.0c in a DIFFERENT regime."
        ),
        retired_at=_day(2026, 8, 12),
    ),
    dict(
        key="weather-consensus",
        title="weather_con — synoptic-temperature consensus pick",
        family="forecast",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis="a multi-source temperature consensus beats the market's bucket pricing",
        docs={"verdict": "docs/RESEARCH_JOURNAL.md"},
        covers_tags=["weather_con"],
        verdict=(
            "RETIRED 2026-08-12 at n=775: -3.50c/trade, -$27.14, 34.7% win. The "
            "last weather book still entering. Weather books are TAKERS, so the "
            "maker-fee correction does not flatter this — the number is real. The "
            "consensus DATA flags stay ON (they feed weather_forecast_outcomes, "
            "the validation dataset, which is not a book)."
        ),
        retired_at=_day(2026, 8, 12),
        evidence=[
            dict(
                label="lifetime at retirement",
                evidence_class="context_only",
                source="paper_trades.strategy='weather_con'",
                summary={"n": 775, "cents_per_trade": -3.50, "usd_total": -27.14,
                         "win_pct": 34.7},
                window_end=_day(2026, 8, 12),
            ),
        ],
    ),
    dict(
        key="weather-consensus-city",
        title="weather_concity — consensus restricted to the by-city edge cells",
        family="forecast",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        predecessor_key="weather-consensus",
        hypothesis=(
            "con's loss is diluted by bad cities; restricting to the three "
            "by-city edge cells turns it positive"
        ),
        docs={"verdict": "docs/RESEARCH_JOURNAL.md"},
        covers_tags=["weather_concity"],
        verdict=(
            "RETIRED 2026-08-12 — ANSWERED its question in the negative: measured "
            "at n=203 on the same picks, concity -8.29c/trade against con's "
            "-3.50c. Restricting to the historical winners made it more than "
            "twice as bad — what per-city selection looks like when the per-city "
            "ranking was noise. A standing caution about slicing a losing book by "
            "a dimension chosen from its own history."
        ),
        retired_at=_day(2026, 8, 12),
    ),
    dict(
        key="weather-legacy-cells",
        title="legacy weather cells (directional, favband, ...)",
        family="forecast",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="D",
        hypothesis=None,  # a lump of pruned cells; no single reconstructable contract
        docs={"verdict": "docs/RESEARCH_JOURNAL.md"},
        covers_tags=[],
        covers_tag_prefixes=["weather_"],
        verdict=(
            "Pruned 2026-07 — net -EV; 0 open. The registry carries these as one "
            "lump row ('weather_* other cells'), so they are preserved here at "
            "integrity D: per-cell contracts are NOT reconstructable and their "
            "P&L remains historical operations data, never clean experimental "
            "evidence."
        ),
    ),
    dict(
        key="tfav",
        title="tfav — model-underpriced hourly-crypto favorites",
        family="favorite_buy",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis="buy model-underpriced hourly-crypto favorites",
        docs={"verdict": "docs/RESEARCH_JOURNAL.md"},
        covers_tags=["tfav"],
        verdict=(
            "KILLED 2026-07-09 at -3.6c/trade, n>=210. Re-enable only under fresh "
            "pre-registration. (The FREEZE family's control arm exists precisely "
            "because every freeze arm is a favorite-buy — the shape this died in.)"
        ),
        retired_at=_day(2026, 7, 9),
    ),
    dict(
        key="wcprop",
        title="wcprop — World Cup winner-ladder lag",
        family="structural",
        origin="operator",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis="the WC winner ladder reprices with a lag after a decisive result",
        docs={
            "verdict": "docs/RESEARCH_JOURNAL.md",
            "thesis": "docs/IDEA_MODEL_20260704.md",
        },
        covers_tags=["wcprop"],
        verdict="KILLED — no repricing lag found; the WC window closed.",
    ),
    dict(
        key="xgame",
        title="xgame — cross-venue in-play lead-lag",
        family="cross_venue",
        origin="idea_model",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        integrity="C",
        hypothesis="Polymarket leads, Kalshi lags on matched in-play game markets",
        docs={"thesis": "docs/IDEA_MODEL_20260704.md"},
        covers_tags=["xgame"],
        verdict=(
            "Book KILLED — P3 symmetric: both venues track the shared feed, no "
            "exploitable lag. The tape collector remains (game_market_matches / "
            "game_tape_snapshots are research data, not a book)."
        ),
    ),
]
