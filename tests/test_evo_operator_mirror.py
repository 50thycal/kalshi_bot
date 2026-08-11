"""The tag-team mirror: agents can read the operator's research AND its live results.

Two research efforts share this repo — the operator's (thesis docs, BOOK_REGISTRY,
~200 PRs of studies) and the fleet's. Until now the fleet could see none of it: its
read channels were DB rows and the Kalshi API, while the operator's conclusions live
in docs/ and the operator's *performance* lives in paper_trades under book tags the
agents had no way to aggregate. Raw rows are not a scoreboard.

Two additions close the loop:

  read_doc           — bounded, chunked reads of the repo's docs/*.md. The docs ship
                       in the deployed image already (nixpacks copies the repo), so a
                       merged PR is readable by the fleet on the next deploy with no
                       sync pipeline at all.
  book_performance   — the per-book rollup (n, win%, total, per-trade, open count)
                       over the operator's paper_trades tags, i.e. the same numbers
                       the operator's own "PnL" command reads. Thesis without the
                       scoreboard invites cargo-culting a losing book; the scoreboard
                       without thesis invites copying numbers with no mechanism.

Safety unchanged in kind: read_doc is allowlisted to docs/*.md by construction (no
traversal, no other directories), budgeted like any data read, and chunk-capped;
book_performance is a typed aggregate over one table with bound-parameter filters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.evo import data_access, knowledge
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition
from kalshi_bot.evo.constitution import PERMITTED_ACTIONS
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import StaticMarketData
from kalshi_bot.models import PaperPosition, PaperTrade

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


# --- book_performance: the operator's scoreboard ----------------------------


def _seed_books(session):
    """Two operator books with known arithmetic, plus the noise the rollup must
    ignore: a legacy row and an unsettled row."""
    for i, (pnl, rv) in enumerate([(0.50, 0), (0.25, 0), (-0.92, 100)]):
        session.add(PaperTrade(
            market_ticker=f"KXW-{i}", strategy="mmsell10", status="settled",
            side="no", quantity=5, assumed_price=92, resolved_value=rv, pnl=pnl,
            created_at=NOW - timedelta(days=3, hours=i),
            closed_at=NOW - timedelta(days=2, hours=i),
        ))
    for i, pnl in enumerate([0.40, 0.30]):
        session.add(PaperTrade(
            market_ticker=f"KXT-{i}", strategy="theta4", status="settled",
            side="no", quantity=5, assumed_price=90, resolved_value=0, pnl=pnl,
            created_at=NOW - timedelta(days=1, hours=i),
            closed_at=NOW - timedelta(hours=i + 1),
        ))
    session.add(PaperTrade(  # legacy: a different accounting era, never mixed in
        market_ticker="KXW-LEG", strategy="mmsell10", status="settled", side="no",
        quantity=5, assumed_price=92, resolved_value=0, pnl=9.99, legacy=True,
        created_at=NOW - timedelta(days=30), closed_at=NOW - timedelta(days=29),
    ))
    session.add(PaperTrade(  # still open: no outcome yet, must not pollute win rate
        market_ticker="KXW-OPEN", strategy="mmsell10", status="open", side="no",
        quantity=5, assumed_price=92, created_at=NOW - timedelta(hours=2),
    ))
    session.add(PaperPosition(
        market_ticker="KXW-OPEN", strategy="mmsell10", side="no", quantity=5,
        avg_price=92, status="open", opened_at=NOW - timedelta(hours=2),
    ))
    session.flush()


def test_book_performance_is_the_pnl_rollup_not_raw_rows(evo_session):
    _seed_books(evo_session)
    result, err = data_access.inspect(evo_session, "book_performance")
    assert err is None
    by_tag = {r["strategy"]: r for r in result["rows"]}

    mm = by_tag["mmsell10"]
    assert mm["n_settled"] == 3
    assert mm["wins"] == 2
    assert mm["win_rate"] == 0.667
    assert mm["total_pnl_usd"] == -0.17
    assert mm["per_trade_usd"] == -0.0567
    assert mm["open_positions"] == 1

    th = by_tag["theta4"]
    assert th["n_settled"] == 2 and th["total_pnl_usd"] == 0.7


def test_the_rollup_excludes_legacy_and_unsettled(evo_session):
    """The +$9.99 legacy row would flip mmsell10's sign — mixing accounting eras is
    exactly the silent-provenance-mix the repo's conventions forbid."""
    _seed_books(evo_session)
    result, _ = data_access.inspect(evo_session, "book_performance")
    mm = next(r for r in result["rows"] if r["strategy"] == "mmsell10")
    assert mm["total_pnl_usd"] == -0.17  # not +9.82, and n stays 3


def test_book_performance_filters_by_tag_and_prefix(evo_session):
    _seed_books(evo_session)
    exact, _ = data_access.inspect(
        evo_session, "book_performance", filters={"strategy": "theta4"})
    assert [r["strategy"] for r in exact["rows"]] == ["theta4"]

    fam, _ = data_access.inspect(
        evo_session, "book_performance", filters={"strategy_prefix": "mmsell"})
    assert [r["strategy"] for r in fam["rows"]] == ["mmsell10"]

    bad, err = data_access.inspect(
        evo_session, "book_performance", filters={"pnl": "1"})
    assert bad is None and "strategy" in err


def test_book_performance_is_advertised_like_any_source():
    """Discoverability is the feature: it must appear in the same protocol list the
    agents already read sources from, not be a secret handshake."""
    assert "book_performance" in data_access.SOURCES
    assert "book_performance" in ACTION_PROTOCOL


# --- read_doc: the operator's research library ------------------------------


def test_the_doc_index_lists_the_research_library():
    index = knowledge.doc_index()
    names = {d["name"] for d in index}
    assert {"BOOK_REGISTRY", "RESEARCH_JOURNAL", "MMSELL_FILL_MODEL"} <= names
    registry = next(d for d in index if d["name"] == "BOOK_REGISTRY")
    assert "book" in registry["title"].lower()


def test_read_doc_returns_bounded_chunks_with_a_heading_map(evo_session):
    doc, err = knowledge.read_doc("RESEARCH_JOURNAL")
    assert err is None
    assert doc["chunk"] == 0 and doc["total_chunks"] > 1  # 1,834 lines never fits
    assert len(doc["text"]) <= knowledge.CHUNK_CHARS
    assert doc["headings"], "without a heading map the agent cannot navigate 1800 lines"

    later, err = knowledge.read_doc("RESEARCH_JOURNAL", chunk=1)
    assert err is None and later["chunk"] == 1
    assert later["text"] != doc["text"]


def test_read_doc_accepts_the_md_suffix_and_unknown_docs_name_the_library():
    with_suffix, err = knowledge.read_doc("BOOK_REGISTRY.md")
    assert err is None and with_suffix["doc"] == "BOOK_REGISTRY"

    missing, err = knowledge.read_doc("NOT_A_DOC")
    assert missing is None and "BOOK_REGISTRY" in err


def test_read_doc_cannot_escape_the_docs_directory():
    """The allowlist is the security boundary: the worker's filesystem also holds
    .env-shaped config and source code, and a doc name is agent-supplied text."""
    for name in ("../.env", "..%2F.env", "/etc/passwd", "x/../../CLAUDE.md",
                 "..\\secrets", "docs/../kalshi_bot/config.py"):
        doc, err = knowledge.read_doc(name)
        assert doc is None, f"{name!r} escaped the docs allowlist"


def test_a_doc_read_resurfaces_like_any_data_read(evo_session, evo_settings, evo_agent):
    """Reuses the inspect_data persistence path, so the pulled text shows up under
    YOUR RECENT DATA READS next heartbeat with no new plumbing."""
    agent, cohort = evo_agent
    hb = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="mirror-1", md=StaticMarketData(),
        cognition=ScriptedCognition(lambda ctx: {
            "journal": {"decision": "study the operator's registry"},
            "actions": [{"type": "read_doc", "name": "BOOK_REGISTRY"}],
        }),
    )
    outcome = next(o for o in hb.actions_json if o["type"] == "read_doc")
    assert outcome.get("ok") is True, outcome
    reads = data_access.recent_reads(evo_session, agent.agent_uuid)
    assert any(r["source"] == "doc:BOOK_REGISTRY" for r in reads)


def test_read_doc_charges_the_data_read_budget(evo_session, evo_settings, evo_agent):
    """Same budget as inspect_data: a doc pull is a research read, and an unbudgeted
    read path would let an agent spend every heartbeat re-reading the journal."""
    from kalshi_bot.evo import budgets

    agent, cohort = evo_agent
    before = budgets.remaining(evo_session, agent.agent_uuid, cohort.id, "data_reads")
    run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="mirror-2", md=StaticMarketData(),
        cognition=ScriptedCognition(lambda ctx: {
            "journal": {"decision": "read"},
            "actions": [{"type": "read_doc", "name": "BOOK_REGISTRY"}],
        }),
    )
    after = budgets.remaining(evo_session, agent.agent_uuid, cohort.id, "data_reads")
    assert after == before - 1


def test_the_protocol_teaches_read_doc_and_lists_the_library():
    assert "read_doc" in PERMITTED_ACTIONS
    assert "read_doc" in ACTION_PROTOCOL
    # the index is substituted in, so agents see WHAT exists without asking
    assert "BOOK_REGISTRY" in ACTION_PROTOCOL
