"""The historical proving run: build a cohort, evolve it, then check the machinery.

This is not an attempt to evolve a profitable strategy, and its report deliberately does
not claim one. It answers the ten mechanical questions the Evo foundation has to pass
before anything it produces is worth believing:

    1  genomes are valid and immutable
    2  runs reproduce
    3  rankings are explainable
    4  parents and children are correct
    5  retirement works
    6  candidate ledgers reconcile
    7  no look-ahead occurs
    8  mutations have exact provenance
    9  diversity is observable
    10 the Control Tower can explain the cohort
    11 inert mutations are detected

plus the adversarial cases: a high-P&L/high-drawdown candidate must not outrank a steady
one, a low-sample lucky candidate must be held rather than crowned, and a data-broken
candidate must be classified invalid rather than quietly retired for a bad number.

Every check returns a verdict and its evidence. A failed check is reported, not raised —
the point of a proving run is to produce a readable verdict on the whole system, and
aborting at the first failure would hide the other nine answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import EvoSettings
from . import control_tower, proving, replay, service
from . import genome as genome_mod
from .models import (
    EvoCandidate,
    EvoCandidateLedger,
    EvoDecision,
    EvoFinding,
    EvoFitness,
    EvoGenomeVersion,
    EvoRun,
    EvoRunTrade,
)

STARTING_CAPITAL = 500.0
MIN_TRADES = 30

#: The four adversarial profiles, pinned to the synthetic corpus's series so each one is
#: constructed rather than hoped for. See `proving.py` for what each series does.
ADVERSARIAL = (
    ("steady", "KXSYNTHA", "a moderate, consistent edge across the whole window"),
    ("reckless", "KXSYNTHB", "high total P&L bought with an account-ending drawdown"),
    ("lucky", "KXSYNTHC", "a huge per-trade number off a handful of trades"),
    ("broken", "KXSYNTHD", "a corpus containing corrupt quotes"),
)


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
    tower: str = ""
    lineage: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, key: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(key, ok, detail))


# ---------------------------------------------------------------------------
# Founders
# ---------------------------------------------------------------------------


def founder_documents(cohort: int = 30) -> list[tuple[dict, str]]:
    """A diverse founder population, deterministic and reproducible.

    The first four are the adversarial profiles. The rest sweep the surface across
    families, sides, price bands, exit rules and sizes, so generation 0 has real
    variance to select on — a cohort seeded from one template would make every later
    diversity number meaningless."""
    docs: list[tuple[dict, str]] = []

    for family, series, thesis in ADVERSARIAL:
        docs.append(
            (
                genome_mod.spec_document(
                    name=f"founder-{family}",
                    family=family,
                    description=thesis,
                    universe={
                        "series_prefixes": [series],
                        "max_spread_cents": 10,
                        "max_hours_to_close": 48,
                    },
                    entry={
                        "side": "yes",
                        "style": "taker",
                        "min_price_cents": 10,
                        "max_price_cents": 90,
                        "size_contracts": 5,
                    },
                    exit_={"mode": "settlement"},
                    risk={"max_concurrent_positions": 10, "max_cost_per_position_usd": 50.0},
                ),
                thesis,
            )
        )

    # Sweep the remainder across the surface. Every combination is a different
    # hypothesis about where the edge lives, which is what generation 0 is for.
    #
    # Candidates are filtered against the same novelty floor the program enforces, so a
    # seed generator whose combinations happen to collide produces a smaller cohort
    # rather than a cohort that quietly contains near-duplicates. Generation 0's
    # diversity has to be real, or every diversity number measured later is against a
    # baseline that was already collapsed.
    bands = ((10, 45), (20, 60), (35, 75), (45, 90), (15, 80), (25, 55))
    exits = (
        {"mode": "settlement"},
        {"mode": "tp_sl", "take_profit_cents": 80, "stop_loss_cents": 25},
        {"mode": "timed", "max_hold_hours": 12.0},
        {"mode": "confirmed_stop", "stop_mid_cents": 30, "confirm_ticks": 3},
        {"mode": "volatility_exit", "vol_range_cents": 14, "vol_window_ticks": 6},
    )
    chosen = [d for d, _ in docs]
    index = 0
    for band in bands:
        for exit_rule in exits:
            for family, series, _thesis in ADVERSARIAL:
                if len(docs) >= cohort:
                    return docs[:cohort]
                side = "yes" if index % 2 == 0 else "no"
                doc = genome_mod.spec_document(
                    name=f"founder-{len(docs):02d}",
                    family=family,
                    description=(
                        f"{side} {band[0]}-{band[1]}c on {series}, exit "
                        f"{exit_rule['mode']}"
                    ),
                    universe={
                        "series_prefixes": [series],
                        "max_spread_cents": 8 + (index % 5),
                        "max_hours_to_close": 24.0 + 12.0 * (index % 3),
                    },
                    entry={
                        "side": side,
                        "style": "taker",
                        "min_price_cents": band[0],
                        "max_price_cents": band[1],
                        "size_contracts": 3 + (index % 4) * 2,
                    },
                    exit_=dict(exit_rule),
                    risk={
                        "max_concurrent_positions": 5 + (index % 6),
                        "max_cost_per_position_usd": 50.0,
                    },
                )
                index += 1
                norm, err = genome_mod.validate(doc)
                if err or norm is None:
                    continue
                nearest_d, _ = genome_mod.nearest(norm, chosen)
                if nearest_d < 0.02:
                    continue
                chosen.append(norm)
                docs.append((doc, f"founder sweep: {doc['description']}"))
    return docs[:cohort]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_proving(
    session,
    *,
    program_key: str = "proving-1",
    generations: int = 3,
    cohort: int = 30,
    settings: EvoSettings | None = None,
) -> dict:
    """Create, seed, evolve and verify. Returns `{ok, report, checks, program}`."""
    settings = settings or EvoSettings()
    proving.register()
    report = ProvingReport()

    program = service.create_program(
        session,
        key=program_key,
        name="Evo foundation historical proving run",
        objective=(
            "Prove the evolutionary loop itself — lineage, immutability, "
            "reproducibility, ranking, retirement and provenance — on a corpus whose "
            "answers are known in advance. Not an attempt to evolve a winner."
        ),
        dataset=proving.DATASET,
        cohort_target=cohort,
        starting_capital_usd=STARTING_CAPITAL,
        min_trades_for_evidence=MIN_TRADES,
        min_genome_distance=0.02,
        rng_seed=20260825,
    )

    docs = founder_documents(cohort)
    service.seed_founders(
        session,
        program=program,
        documents=[d for d, _ in docs],
        purposes=[p for _, p in docs],
        generation_number=0,
    )

    # Successive, non-overlapping windows: each generation is evaluated on history its
    # predecessors did not see, so a child is never scored on the evidence that ranked
    # its parent.
    windows = [proving.window(i * 40, (i + 1) * 40) for i in range(generations)]
    results = []
    for number, (start, end) in enumerate(windows):
        results.append(
            service.advance(
                session,
                settings,
                program=program,
                window_start=start,
                window_end=end,
                data_cutoff=end,
                number=number,
            )
        )

    _verify(session, program=program, settings=settings, results=results, report=report)

    data = control_tower.collect(session, program=program)
    report.tower = control_tower.render(data)
    report.lineage = control_tower.lineage_tree(session, program=program)

    return {
        "ok": report.ok,
        "checks": [(c.key, c.ok, c.detail) for c in report.checks],
        "report": _format(report),
        "program": program,
        "results": results,
    }


def _format(report: ProvingReport) -> str:
    passed = sum(1 for c in report.checks if c.ok)
    lines = [
        "=" * 78,
        "EVO FOUNDATION — HISTORICAL PROVING RUN",
        "=" * 78,
        "",
        f"VERDICT: {'CLEAN' if report.ok else 'DEFECTS FOUND'} "
        f"({passed}/{len(report.checks)} checks passed)",
        "",
        "CHECKS",
    ]
    lines.extend(c.line() for c in report.checks)
    lines.extend(["", report.tower, "", "LINEAGE", report.lineage])
    return "\n".join(lines)


def _verify(session, *, program, settings, results, report: ProvingReport) -> None:
    last = results[-1]
    generation = last.generation

    # --- 1. genomes are valid and immutable ----------------------------------
    genomes = list(
        session.execute(
            select(EvoGenomeVersion).where(EvoGenomeVersion.program_id == program.id)
        ).scalars()
    )
    tampered = [
        g for g in genomes if genome_mod.genome_hash(g.document_json or {}) != g.genome_hash
    ]
    invalid = [g for g in genomes if genome_mod.validate(g.document_json or {})[1]]
    report.add(
        "1 genomes valid and immutable",
        not tampered and not invalid,
        f"{len(genomes)} genomes; {len(tampered)} hash mismatches, {len(invalid)} invalid. "
        "Every stored document still hashes to its recorded content address, so no "
        "evaluated genome was edited in place.",
    )

    # --- 2. runs reproduce ---------------------------------------------------
    sample = list(
        session.execute(
            select(EvoRun)
            .where(EvoRun.generation_id == generation.id, EvoRun.status == "completed")
            .order_by(EvoRun.id)
            .limit(5)
        ).scalars()
    )
    mismatches = []
    for run in sample:
        genome = session.get(EvoGenomeVersion, run.genome_id)
        again = replay.replay(
            session,
            settings,
            document=genome.document_json,
            dataset=run.dataset,
            window_start=run.window_start,
            window_end=run.window_end,
            data_cutoff=generation.data_cutoff,
            starting_capital_usd=float(run.starting_capital_usd),
        )
        before = (run.reproducibility_json or {}).get("outcome_fingerprint")
        after = again.reproducibility["outcome_fingerprint"]
        if before != after:
            mismatches.append((run.id, before, after))
    report.add(
        "2 runs reproduce",
        not mismatches,
        f"re-ran {len(sample)} runs; {len(mismatches)} fingerprint mismatches. "
        "Identical genome over an identical window yields an identical trade tape.",
    )

    # --- 3. rankings are explainable -----------------------------------------
    rows = list(
        session.execute(
            select(EvoFitness).where(EvoFitness.generation_id == generation.id)
        ).scalars()
    )
    ranked = [r for r in rows if r.rank is not None]
    # Each contribution is persisted rounded to 6dp and the score to 6dp, so the sum can
    # legitimately drift from the score by a few parts in 10^6 across nine components.
    # The check is that a rank is *reconstructible*, not that floats are exact.
    tolerance = 1e-5
    unexplained = [
        r for r in ranked
        if not r.components_json
        or abs(
            sum(float(c.get("contribution", 0)) for c in r.components_json.values())
            - float(r.fitness or 0)
        )
        > tolerance
    ]
    report.add(
        "3 rankings are explainable",
        bool(ranked) and not unexplained,
        f"{len(ranked)} ranked candidates; {len(unexplained)} whose persisted components "
        "do not reconstruct their score. Every fitness equals the sum of its recorded "
        "component contributions.",
    )

    # --- 4. parents and children are correct ---------------------------------
    children = list(
        session.execute(
            select(EvoCandidate).where(
                EvoCandidate.program_id == program.id, EvoCandidate.origin == "mutation"
            )
        ).scalars()
    )
    bad_lineage = []
    for child in children:
        parent = session.execute(
            select(EvoCandidate).where(EvoCandidate.uuid == child.parent_uuid)
        ).scalars().first()
        child_genome = session.get(EvoGenomeVersion, child.current_genome_id)
        if parent is None or child_genome is None:
            bad_lineage.append((child.label, "missing parent or genome"))
            continue
        parent_genome = session.get(EvoGenomeVersion, child_genome.parent_genome_id)
        if parent_genome is None:
            bad_lineage.append((child.label, "child genome has no parent genome"))
            continue
        if parent_genome.genome_hash == child_genome.genome_hash:
            bad_lineage.append((child.label, "child genome identical to parent"))
        if child.generation_depth != parent.generation_depth + 1:
            bad_lineage.append((child.label, "generation depth is not parent + 1"))
    parents_alive = [
        c for c in children
        if (p := session.execute(
            select(EvoCandidate).where(EvoCandidate.uuid == c.parent_uuid)
        ).scalars().first()) is not None and p.current_genome_id == c.current_genome_id
    ]
    report.add(
        "4 parents and children are correct",
        not bad_lineage and not parents_alive,
        f"{len(children)} children; {len(bad_lineage)} lineage defects; "
        f"{len(parents_alive)} parents whose genome was overwritten by a child. "
        "Reproduction creates a child and leaves the parent's genome untouched.",
    )

    # --- 5. retirement works --------------------------------------------------
    retired = list(
        session.execute(
            select(EvoCandidate).where(
                EvoCandidate.program_id == program.id, EvoCandidate.state == "retired"
            )
        ).scalars()
    )
    retire_decisions = list(
        session.execute(
            select(EvoDecision).where(
                EvoDecision.program_id == program.id, EvoDecision.decision == "retire"
            )
        ).scalars()
    )
    unreasoned = [c for c in retired if not c.retirement_reason]
    # A retired candidate must not be replayed again in a later generation.
    retired_uuids = {c.uuid for c in retired}
    resurrected = [
        r.candidate_uuid
        for r in session.execute(
            select(EvoRun).where(
                EvoRun.program_id == program.id,
                EvoRun.generation_number == generation.number,
            )
        ).scalars()
        if r.candidate_uuid in retired_uuids
        and any(
            d.generation_number < generation.number
            for d in retire_decisions
            if d.candidate_uuid == r.candidate_uuid
        )
    ]
    report.add(
        "5 retirement works",
        bool(retired) and not unreasoned and not resurrected,
        f"{len(retired)} retired with {len(retire_decisions)} recorded decisions; "
        f"{len(unreasoned)} without a reason; {len(resurrected)} replayed after "
        "retirement. Retirement is a recorded decision and it stops the candidate.",
    )

    # --- 6. ledgers reconcile -------------------------------------------------
    ledgers = list(
        session.execute(
            select(EvoCandidateLedger).where(EvoCandidateLedger.program_id == program.id)
        ).scalars()
    )
    drift = []
    for led in ledgers:
        trades = list(
            session.execute(
                select(EvoRunTrade).where(EvoRunTrade.run_id == led.run_id)
            ).scalars()
        )
        pnl = round(sum(float(t.pnl_usd) for t in trades), 4)
        fees = round(sum(float(t.fees_usd) for t in trades), 4)
        contracts = sum(int(t.quantity) for t in trades)
        if (
            abs(pnl - float(led.realized_pnl_usd)) > 0.01
            or abs(fees - float(led.fees_usd)) > 0.01
            or contracts != led.contracts
            or abs(
                float(led.ending_capital_usd)
                - (float(led.starting_capital_usd) + float(led.realized_pnl_usd))
            )
            > 0.01
        ):
            drift.append(led.id)
    report.add(
        "6 candidate ledgers reconcile",
        bool(ledgers) and not drift,
        f"{len(ledgers)} ledgers; {len(drift)} that do not tie to their own trade tape. "
        "Every ledger's P&L, fees, contract count and ending capital are recomputable "
        "from its trades.",
    )

    # --- 7. no look-ahead -----------------------------------------------------
    cutoff_breaches = []
    for run in session.execute(
        select(EvoRun).where(EvoRun.program_id == program.id, EvoRun.status == "completed")
    ).scalars():
        window_end = (run.reproducibility_json or {}).get("window_end")
        if not window_end:
            continue
        latest = session.execute(
            select(EvoRunTrade.exited_at)
            .where(EvoRunTrade.run_id == run.id)
            .order_by(EvoRunTrade.exited_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None and str(latest)[:10] > window_end:
            cutoff_breaches.append((run.id, str(latest)[:10], window_end))
    refused = _refusal_probe(session, settings, program)
    report.add(
        "7 no look-ahead occurs",
        not cutoff_breaches and refused,
        f"{len(cutoff_breaches)} trades settled after their run's window end; a window "
        f"reaching past the generation cutoff was {'refused' if refused else 'ACCEPTED'}. "
        "The boundary is enforced by refusal, not by trimming.",
    )

    # --- 8. mutations have exact provenance -----------------------------------
    no_provenance = []
    for child in children:
        g = session.get(EvoGenomeVersion, child.current_genome_id)
        if g is None:
            continue
        parent_g = session.get(EvoGenomeVersion, g.parent_genome_id)
        if parent_g is None or not g.mutation_diff_json or g.proposal_id is None:
            no_provenance.append(child.label)
            continue
        # The recorded diff must be exactly what separates the two documents.
        actual = {c.path for c in genome_mod.diff(parent_g.document_json, g.document_json)}
        recorded = {str(c.get("path")) for c in g.mutation_diff_json}
        if actual != recorded:
            no_provenance.append(f"{child.label} (diff {recorded} != actual {actual})")
    report.add(
        "8 mutations have exact provenance",
        bool(children) and not no_provenance,
        f"{len(children)} children; {len(no_provenance)} whose recorded gene diff does "
        "not match the actual difference from the parent genome. Every mutation names "
        "its parent, its proposal, and exactly which genes moved.",
    )

    # --- 9. diversity is observable -------------------------------------------
    div = last.diversity or {}
    hashes = {g.genome_hash for g in genomes}
    report.add(
        "9 diversity is observable",
        bool(div) and div.get("mean_pairwise_distance") is not None
        and len(hashes) == len(genomes),
        f"mean pairwise distance {div.get('mean_pairwise_distance')}, "
        f"{div.get('distinct_genomes')} distinct of {div.get('n')} members, "
        f"{len(hashes)} unique hashes across {len(genomes)} genomes. Duplicates are "
        "refused at admission, so distinct==total is the expected state.",
    )

    # --- 10. the Control Tower explains ---------------------------------------
    data = control_tower.collect(session, program=program)
    rendered = control_tower.render(data)
    has_sections = all(
        token in rendered for token in ("EVO PROGRAM", "TOP", "DIVERSITY", "why:")
    )
    report.add(
        "10 Control Tower explains the cohort",
        has_sections and bool(data.get("entries")),
        f"rendered {len(rendered.splitlines())} lines covering "
        f"{len(data.get('entries') or [])} candidates, each with a component-level "
        "explanation of its rank.",
    )

    # Generation 0 is the only one where all four founders are guaranteed to have run:
    # by generation 1 the loop has already retired some of them, which is the system
    # working. Comparing them anywhere else would test the retirement policy, not the
    # evaluator.
    # --- 11. inert mutations are detected -------------------------------------
    inert = [c for r in results for c in (r.diversity_inert or [])]
    inert_findings = {
        f.candidate_uuid
        for f in session.execute(
            select(EvoFinding).where(
                EvoFinding.program_id == program.id,
                EvoFinding.kind == "inert_mutation",
            )
        ).scalars()
    }
    undetected = [c for c in inert if c.get("candidate_uuid") not in inert_findings]
    structural = [
        c for c in inert
        if c.get("changed") and all(p.startswith("risk.") for p in c["changed"])
    ]
    report.add(
        "11 inert mutations are detected",
        not undetected and not structural,
        f"{len(inert)} children replayed identically to their parent; "
        f"{len(undetected)} without a recorded finding; {len(structural)} caused by a "
        "gene the engine cannot express. A mutation that cannot change the replay is a "
        "non-experiment, and it has to be visible as one.",
    )

    _verify_adversarial(
        session, program=program, generation=results[0].generation, report=report
    )


def _refusal_probe(session, settings, program) -> bool:
    """A window past the cutoff must be refused outright."""
    try:
        replay.check_window("2026-01-01", "2026-12-31", "2026-03-01")
    except replay.ReplayRefused:
        return True
    return False


def _verify_adversarial(session, *, program, generation, report: ProvingReport) -> None:
    """The cases the evaluator exists to get right."""
    rows = {
        r.candidate_uuid: r
        for r in session.execute(
            select(EvoFitness).where(EvoFitness.generation_id == generation.id)
        ).scalars()
    }
    candidates = {
        c.uuid: c
        for c in session.execute(
            select(EvoCandidate).where(EvoCandidate.program_id == program.id)
        ).scalars()
    }

    def by_label(label: str):
        for uuid, candidate in candidates.items():
            if candidate.label == label:
                return rows.get(uuid), candidate
        return None, None

    # Founders are seeded in ADVERSARIAL order, so agent-001..004 are the four cases.
    steady_row, _ = by_label("agent-001")
    reckless_row, _ = by_label("agent-002")
    lucky_row, _ = by_label("agent-003")
    broken_row, _ = by_label("agent-004")

    if steady_row and reckless_row and steady_row.fitness and reckless_row.fitness:
        ledger_of = {
            led.run_id: led
            for led in session.execute(
                select(EvoCandidateLedger).where(
                    EvoCandidateLedger.generation_number == generation.number
                )
            ).scalars()
        }
        s_led = ledger_of.get(steady_row.run_id)
        r_led = ledger_of.get(reckless_row.run_id)
        richer = (
            s_led and r_led and float(r_led.realized_pnl_usd) > float(s_led.realized_pnl_usd)
        )
        ok = steady_row.fitness > reckless_row.fitness
        report.add(
            "A1 reckless does not outrank steady",
            bool(ok),
            (
                f"steady fitness {steady_row.fitness:.4f} vs reckless "
                f"{reckless_row.fitness:.4f}"
                + (
                    f"; reckless banked ${float(r_led.realized_pnl_usd):,.2f} against "
                    f"steady's ${float(s_led.realized_pnl_usd):,.2f} and still ranks "
                    "lower — raw P&L did not decide it"
                    if richer
                    else "; note reckless did not out-earn steady this window, so the "
                    "case is weaker than intended"
                )
            ),
        )
    else:
        report.add("A1 reckless does not outrank steady", False, "profiles not both ranked")

    if lucky_row:
        report.add(
            "A2 lucky is held, not crowned",
            lucky_row.evidence_class == "insufficient" and lucky_row.rank is None,
            f"n={lucky_row.n_trades} classified {lucky_row.evidence_class!r} with rank "
            f"{lucky_row.rank}; {lucky_row.notes}. A thin sample cannot win on "
            "performance however good the per-trade number looks.",
        )
    else:
        report.add("A2 lucky is held, not crowned", False, "lucky profile not found")

    if broken_row:
        broken_candidate = next(
            (c for c in candidates.values() if c.label == "agent-004"), None
        )
        escalated = session.execute(
            select(EvoDecision).where(
                EvoDecision.program_id == program.id,
                EvoDecision.candidate_uuid == broken_row.candidate_uuid,
                EvoDecision.decision == "escalate",
            )
        ).scalars().first()
        report.add(
            "A3 broken data is invalid, not retired",
            broken_row.evidence_class == "invalid"
            and broken_row.fitness is None
            and escalated is not None
            and (broken_candidate is None or broken_candidate.state != "retired"),
            f"classified {broken_row.evidence_class!r} with fitness {broken_row.fitness}; "
            f"escalation decision {'recorded' if escalated else 'MISSING'}; "
            f"state {broken_candidate.state if broken_candidate else '?'}. "
            "A candidate that could not be evaluated is a defect, not a bad strategy.",
        )
    else:
        report.add("A3 broken data is invalid, not retired", False, "broken profile not found")


__all__ = ["ADVERSARIAL", "ProvingReport", "founder_documents", "run_proving"]
