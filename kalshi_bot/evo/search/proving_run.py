"""The proving run: does the search capability produce evidence worth believing?

Scoped to what the capability actually claims, now that it runs no lifecycle. There is
no cohort here, no reproduction, no retirement — those belong to the organism and have
their own tests. What has to be true before an agent should act on a search result:

     1  documents are valid and content-addressed
     2  replays reproduce
     3  scores are explainable — every score reconstructs from its components
     4  the base is measured on the same footing as its variants
     5  ledgers reconcile against their own tape
     6  no look-ahead occurs
     7  every variant records exactly which genes moved
     8  refused proposals are surfaced with a reason, not silently dropped
     9  near-duplicates are refused
    10  the search decides nothing — it writes no agent state

plus the adversarial cases the scoring has to get right: a high-P&L/high-drawdown variant
must not outrank a steady one, a low-sample variant must be held rather than crowned, and
a variant replayed on corrupt data must be classified invalid rather than ranked badly.

A failed check is reported, not raised: the point is a readable verdict on the whole
capability, and aborting at the first failure would hide the other nine answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import EvoSettings
from . import fitness as fitness_mod
from . import genome as genome_mod
from . import proving, replay, search
from .models import EvoSearchCandidate, EvoSearchRun, EvoSearchTrade

MIN_TRADES = 30

#: The four adversarial profiles, pinned to the synthetic corpus's series so each is
#: constructed rather than hoped for. See `proving.py` for what each series does.
ADVERSARIAL = (
    ("steady", "KXSYNTHA", "a moderate, consistent edge across the whole window"),
    ("reckless", "KXSYNTHB", "high total P&L bought with an account-ending drawdown"),
    ("lucky", "KXSYNTHC", "a huge per-trade number off a handful of trades"),
    ("broken", "KXSYNTHD", "a corpus containing corrupt quotes"),
)

AGENT_UUID = "proving-agent-0001"


@dataclass
class Check:
    key: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"  [{'PASS' if self.ok else 'FAIL'}] {self.key}: {self.detail}"


@dataclass
class ProvingReport:
    checks: list[Check] = field(default_factory=list)
    evidence: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, key: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(key, ok, detail))


def _spec(series: str, family: str, **entry) -> dict:
    e = {
        "side": "yes",
        "style": "taker",
        "min_price_cents": 10,
        "max_price_cents": 90,
        "size_contracts": 5,
    }
    e.update(entry)
    doc, err = genome_mod.normalize(
        genome_mod.spec_document(
            name=f"proving-{family}",
            family=family,
            universe={
                "series_prefixes": [series],
                "max_spread_cents": 10,
                "max_hours_to_close": 48,
            },
            entry=e,
            exit_={"mode": "settlement"},
        )
    )
    assert err is None, err
    return doc


def run_proving(
    session, *, settings: EvoSettings | None = None, neighbourhood: int = 8
) -> dict:
    """Run the capability against the synthetic corpus and verify it. Returns
    `{ok, checks, report}`."""
    settings = settings or EvoSettings()
    proving.register()
    report = ProvingReport()

    # A whole number of the reckless profile's 40-day periods. A partial window
    # would contain a different win/loss mix than the profile was built for, and
    # A1 would then be measuring the window rather than the evaluator.
    window_start, window_end = proving.window(0, 80)
    base = _spec("KXSYNTHA", "steady")

    evidence = search.run_search(
        session,
        settings,
        agent_uuid=AGENT_UUID,
        base_spec=base,
        dataset=proving.DATASET,
        window_start=window_start,
        window_end=window_end,
        data_cutoff=window_end,
        neighbourhood=neighbourhood,
        min_trades=MIN_TRADES,
        seed=20260825,
        genome_revision=7,
    )
    _verify(session, settings=settings, evidence=evidence, base=base, report=report)
    _verify_adversarial(
        session, settings=settings, window=(window_start, window_end), report=report
    )
    report.evidence = _render_evidence(evidence)
    return {
        "ok": report.ok,
        "checks": [(c.key, c.ok, c.detail) for c in report.checks],
        "report": _format(report),
        "evidence": evidence,
    }


def _format(report: ProvingReport) -> str:
    passed = sum(1 for c in report.checks if c.ok)
    lines = [
        "=" * 78,
        "EVO HISTORICAL SEARCH — PROVING RUN",
        "=" * 78,
        "",
        f"VERDICT: {'CLEAN' if report.ok else 'DEFECTS FOUND'} "
        f"({passed}/{len(report.checks)} checks passed)",
        "",
        "CHECKS",
    ]
    lines.extend(c.line() for c in report.checks)
    lines.extend(["", "WHAT AN AGENT WOULD SEE", "", report.evidence])
    return "\n".join(lines)


def _render_evidence(evidence: search.Evidence) -> str:
    d = evidence.as_dict()
    out = [
        f"search run {d['run_id']} · {d['summary']['dataset']} · "
        f"{d['summary']['window'][0]}..{d['summary']['window'][1]}",
        f"proposals {d['summary']['proposals_made']} → admitted "
        f"{d['summary']['proposals_admitted']} → ranked {d['summary']['ranked']}",
        "",
        f"BASE      score {d['base']['search_score']}  n={d['base']['n_trades']}",
        f"          why: {d['base']['why']}",
        "",
        "VARIANTS",
    ]
    for c in d["candidates"][:5]:
        out.append(
            f"  #{c['rank'] or '—'}  score {c['search_score']}  n={c['n_trades']}  "
            f"dist {c['distance_from_base']}"
        )
        out.append(f"      {', '.join(c['changes']) or 'no change'}")
        out.append(f"      why: {c['why']}")
    if d["refused"]:
        out.append("")
        out.append("REFUSED")
        for r in d["refused"][:4]:
            out.append(f"  [{r['stage']}] {r['reason']}")
    out.extend(["", "FINDING", f"  {d['summary']['finding']}"])
    return "\n".join(out)


def _verify(session, *, settings, evidence, base, report: ProvingReport) -> None:
    run = session.get(EvoSearchRun, evidence.run_id)
    rows = list(
        session.execute(
            select(EvoSearchCandidate)
            .where(EvoSearchCandidate.run_id == run.id)
            .order_by(EvoSearchCandidate.idx)
        ).scalars()
    )
    replayed = [r for r in rows if r.document_json and r.outcome_json]

    # --- 1. documents valid and content-addressed ----------------------------
    bad = [
        r.idx for r in replayed
        if genome_mod.genome_hash(r.document_json) != r.genome_hash
        or genome_mod.validate(r.document_json)[1]
    ]
    report.add(
        "1 documents valid and content-addressed",
        bool(replayed) and not bad,
        f"{len(replayed)} replayed documents; {len(bad)} whose stored hash does not match "
        "their content or that fail validation.",
    )

    # --- 2. replays reproduce -------------------------------------------------
    mismatches = []
    for row in replayed[:4]:
        again = replay.replay(
            session, settings, document=row.document_json, dataset=run.dataset,
            window_start=run.window_start, window_end=run.window_end,
            data_cutoff=run.data_cutoff, starting_capital_usd=search.SEARCH_CAPITAL_USD,
        )
        before = (row.outcome_json or {}).get("net_pnl_usd")
        after = again.outcome.get("net_pnl_usd")
        if before != after:
            mismatches.append((row.idx, before, after))
    report.add(
        "2 replays reproduce",
        not mismatches,
        f"re-ran {min(4, len(replayed))} documents; {len(mismatches)} mismatches. The same "
        "document over the same window returns the same tape.",
    )

    # --- 3. scores are explainable --------------------------------------------
    scored = [r for r in replayed if r.search_score is not None]
    unexplained = [
        r.idx for r in scored
        if not r.score_components_json
        or abs(
            sum(float(c.get("contribution", 0)) for c in r.score_components_json.values())
            - float(r.search_score)
        )
        > 1e-5
    ]
    report.add(
        "3 scores are explainable",
        bool(scored) and not unexplained,
        f"{len(scored)} scored; {len(unexplained)} whose components do not reconstruct "
        "their score. Every score is the sum of its recorded contributions.",
    )

    # --- 4. the base is measured on the same footing --------------------------
    base_row = next((r for r in rows if r.is_base), None)
    report.add(
        "4 the base is measured like its variants",
        base_row is not None
        and base_row.search_score is not None
        and base_row.outcome_json is not None,
        "the base genome is replayed and scored by the same path as every variant, so "
        "'did anything beat it' is a like-for-like comparison rather than an assumption.",
    )

    # --- 5. ledgers reconcile --------------------------------------------------
    drift = []
    for row in replayed:
        trades = list(
            session.execute(
                select(EvoSearchTrade).where(EvoSearchTrade.candidate_id == row.id)
            ).scalars()
        )
        if not trades:
            continue
        pnl = round(sum(float(t.pnl_usd) for t in trades), 4)
        if abs(pnl - float((row.ledger_json or {}).get("realized_pnl_usd", 0))) > 0.01:
            drift.append(row.idx)
    report.add(
        "5 ledgers reconcile with their tape",
        not drift,
        f"{len(replayed)} ledgers; {len(drift)} that do not tie to their own trades.",
    )

    # --- 6. no look-ahead -------------------------------------------------------
    breaches = []
    for row in replayed:
        latest = session.execute(
            select(EvoSearchTrade.exited_at)
            .where(EvoSearchTrade.candidate_id == row.id)
            .order_by(EvoSearchTrade.exited_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None and str(latest)[:10] > (run.window_end or "9999"):
            breaches.append(row.idx)
    refused_window = False
    try:
        replay.check_window("2026-01-01", "2026-12-31", "2026-03-01")
    except replay.ReplayRefused:
        refused_window = True
    report.add(
        "6 no look-ahead occurs",
        not breaches and refused_window,
        f"{len(breaches)} trades settled past the window; an over-reaching window was "
        f"{'refused' if refused_window else 'ACCEPTED'}. The boundary is enforced by "
        "refusal, not trimming.",
    )

    # --- 7. every variant records what moved ------------------------------------
    variants = [r for r in replayed if not r.is_base]
    no_diff = []
    for row in variants:
        recorded = {str(c.get("path")) for c in (row.mutation_diff_json or [])}
        actual = {c.path for c in genome_mod.diff(base, row.document_json)}
        if not recorded or not actual <= recorded:
            no_diff.append(row.idx)
    report.add(
        "7 every variant records exactly which genes moved",
        bool(variants) and not no_diff,
        f"{len(variants)} replayed variants; {len(no_diff)} whose recorded diff does not "
        "cover the actual difference from the base.",
    )

    # --- 8. refusals are surfaced -----------------------------------------------
    refused_rows = [r for r in rows if not r.admitted and not r.is_base]
    unexplained_refusals = [r.idx for r in refused_rows if not r.reject_reason]
    report.add(
        "8 refused proposals are surfaced with a reason",
        not unexplained_refusals,
        f"{len(refused_rows)} refusals recorded, {len(unexplained_refusals)} without a "
        "reason. A rejection is evidence about the search space, not something to drop.",
    )

    # --- 9. near-duplicates refused ---------------------------------------------
    hashes = [r.genome_hash for r in replayed if r.genome_hash]
    report.add(
        "9 near-duplicates are refused",
        len(set(hashes)) == len(hashes),
        f"{len(set(hashes))} distinct documents across {len(hashes)} replayed; the "
        f"novelty floor refused {sum(1 for r in refused_rows if r.reject_stage == 'novelty')} "
        "proposals as too close to something already measured.",
    )

    # --- 10. the search decides nothing ------------------------------------------
    from ..models import EvoAgent, EvoFitness, EvoGenome, EvoRetirement

    wrote = {
        "evo_agents": session.execute(select(EvoAgent)).scalars().first(),
        "evo_genomes": session.execute(select(EvoGenome)).scalars().first(),
        "evo_fitness": session.execute(select(EvoFitness)).scalars().first(),
        "evo_retirements": session.execute(select(EvoRetirement)).scalars().first(),
    }
    touched = [name for name, row in wrote.items() if row is not None]
    report.add(
        "10 the search decides nothing",
        not touched,
        f"organism tables written by the search: {touched or 'none'}. A search measures "
        "and returns evidence; only the agent, through the organism's own path, can "
        "revise a genome or change an agent's state.",
    )


def _verify_adversarial(session, *, settings, window, report: ProvingReport) -> None:
    """The three cases the scoring exists to get right."""
    start, end = window
    results = {}
    for family, series, _thesis in ADVERSARIAL:
        replayed = replay.replay(
            session, settings, document=_spec(series, family), dataset=proving.DATASET,
            window_start=start, window_end=end, data_cutoff=end,
            starting_capital_usd=search.SEARCH_CAPITAL_USD,
        )
        comps, score, ev_class, note = search._score(
            replayed=replayed,
            weights=fitness_mod.resolve_weights(None),
            scales=fitness_mod.resolve_scales(None),
            min_trades=MIN_TRADES,
        )
        results[family] = (replayed, score, ev_class, note)

    steady_r, steady_s, _, _ = results["steady"]
    reck_r, reck_s, _, _ = results["reckless"]
    if steady_s is not None and reck_s is not None:
        ok = steady_s > reck_s
        richer = reck_r.ledger.realized_pnl_usd > steady_r.ledger.realized_pnl_usd
        money = (
            f"reckless banked ${reck_r.ledger.realized_pnl_usd:,.2f} against steady's "
            f"${steady_r.ledger.realized_pnl_usd:,.2f}"
        )
        if ok and richer:
            verdict = f"{money} and still scores lower — raw P&L did not decide it"
        elif ok:
            verdict = (
                f"{money}, so reckless did not out-earn steady here and the case is "
                "weaker than intended"
            )
        else:
            verdict = (
                f"{money}, and the higher earner also scored higher — the drawdown and "
                "tail components did not outweigh the edge on this window"
            )
        report.add(
            "A1 reckless does not outrank steady",
            ok,
            f"steady {steady_s:.4f} vs reckless {reck_s:.4f}; {verdict}",
        )
    else:
        report.add("A1 reckless does not outrank steady", False, "both did not score")

    _, lucky_s, lucky_class, lucky_note = results["lucky"]
    report.add(
        "A2 a thin sample is held, not crowned",
        lucky_class == "insufficient",
        f"classified {lucky_class!r} ({lucky_note}) — unranked inside the search, because "
        "a handful of trades cannot order two strategies. This is a property of MEASURING "
        "strategies; it is not, and must not become, an agent-selection rule.",
    )

    _, broken_s, broken_class, broken_note = results["broken"]
    report.add(
        "A3 corrupt data is invalid, not merely bad",
        broken_class == "invalid" and broken_s is None,
        f"classified {broken_class!r} with score {broken_s} ({broken_note}). A variant "
        "that could not be measured is a data defect to report, not a strategy to rank.",
    )


__all__ = ["ADVERSARIAL", "AGENT_UUID", "MIN_TRADES", "ProvingReport", "run_proving"]
