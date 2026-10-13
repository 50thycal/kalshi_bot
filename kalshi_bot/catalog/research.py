"""Reproducible, bounded research cohorts for thesis work (WS-023 / DEC-034).

One page lists markets of one series with what the catalog knew AS OF a point in time: the
observed listing revision, review bindings, selection, history coverage, imported evidence and
attributable live economics. Information that arrived after `as_of` is masked or flagged, and
every gap is named. Selection is not qualification, and a traded/reviewed cohort is a selected
sample, not an unbiased control. Nothing here scores, qualifies or authorizes a trade.
"""

from datetime import datetime, timezone

from . import selection, templates
from .evaluators import timestamp
from .store import SERIES_TEMPLATE, digest, now, unpack

METHOD = "research-cohort-v1"
POPULATIONS = ("all", "selected", "unselected")
MAX_SCAN = 5000
ROW_LIMIT = 50
ID_LIMIT = 200
MISSING_DENOMINATORS = [
    "unfilled_live_orders_not_imported",
    "paper_candidates_not_entered_not_imported",
    "opportunity_population_not_imported",
]


def _parse(value):
    if value is None:
        return datetime.now(timezone.utc)
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("as_of requires an explicit timezone")
    stamp = stamp.astimezone(timezone.utc)
    if (stamp - datetime.now(timezone.utc)).total_seconds() > 60:
        raise ValueError("as_of cannot be in the future")
    return stamp


def _before(value, as_of):
    stamp = timestamp(value) if value else None
    return stamp is not None and stamp <= as_of


def _observation(captured_at, document):
    kind = selection.representation(document)
    facts = document.get("raw") if kind == selection.FULL else document.get("lifecycle")
    facts = facts or {}
    return {
        "captured_at": captured_at,
        "representation": kind,
        "full_payload_retained": kind == selection.FULL,
        "rules_hash": document.get("rules_hash"),
        "parent_rules_hashes": document.get("parent_rules_hashes"),
        **{
            key: facts.get(key)
            for key in ("status", "result", "settlement_ts", "settlement_value_dollars")
        },
    }


def _review(db, kind, ticker, as_of):
    row = db.execute(
        "SELECT id,rules_hash,created_at,template FROM reviews WHERE kind=? AND ticker=? "
        "AND created_at<=? ORDER BY created_at DESC LIMIT 1",
        (kind, ticker, as_of.isoformat()),
    ).fetchone()
    return (
        {"review_id": row[0], "rules_hash": row[1], "created_at": row[2], "template": bool(row[3])}
        if row
        else None
    )


def _template_review(db, series, revision, as_of):
    """A series approval recorded by as_of whose wording matches the as-of listing."""
    template = templates.template_hash(revision.get("raw") or {})
    row = db.execute(
        "SELECT id,rules_hash,created_at FROM reviews WHERE kind=? AND ticker=? AND rules_hash=? "
        "AND created_at<=? ORDER BY created_at DESC LIMIT 1",
        (SERIES_TEMPLATE, series, template, as_of.isoformat()),
    ).fetchone()
    return (
        {"review_id": row[0], "rules_hash": row[1], "created_at": row[2], "template": True,
         "basis": SERIES_TEMPLATE}
        if row
        else None
    )


def _capture_before(db, url, as_of):
    """Latest archived bytes retrieved at or before as_of. Retrieval time is not the date the
    terms took effect, so this never proves which terms governed a market or an execution."""
    if not url:
        return None
    row = db.execute(
        "SELECT sha256,retrieved_at FROM contract_observations WHERE url=? AND retrieved_at<=? "
        "ORDER BY retrieved_at DESC LIMIT 1",
        (url, as_of.isoformat()),
    ).fetchone()
    return {
        "content_sha256": row[0] if row else None,
        "retrieved_at": row[1] if row else None,
        "governing_terms_proven": False,
    }


def _known_at(source, row):
    # Live: the source's collection time, never earlier than execution. Paper: entry time.
    return row.get("filled_at") if source == "live" else row.get("created_at")


def _evidence(db, ticker, as_of, include_rows):
    groups, rows, ids = {}, [], []
    for source, source_id, stored in db.execute(
        "SELECT e.source,e.source_id,e.document FROM evidence_markets m JOIN evidence e "
        "ON e.source=m.source AND e.source_id=m.source_id WHERE m.market=? "
        "ORDER BY e.source,e.source_id",
        (ticker,),
    ):
        row = unpack(stored)
        known = _known_at(source, row)
        visible = _before(known, as_of)
        key = (source, row.get("strategy"), row.get("experiment_deployment_arm_id"),
               bool(row.get("is_twin")))
        group = groups.setdefault(key, {
            "source": source, "strategy": key[1], "deployment_arm_id": key[2],
            "is_twin": key[3], "rows": 0, "known_by_as_of": 0, "known_after_as_of": 0,
            "first_known_at": None, "last_known_at": None, "outcomes_known_by_as_of": 0,
            "outcomes_after_as_of": 0, "losses_known_by_as_of": 0, "fees_missing": 0,
        })
        group["rows"] += 1
        group["known_by_as_of" if visible else "known_after_as_of"] += 1
        if known:
            group["first_known_at"] = min(filter(None, (group["first_known_at"], known)))
            group["last_known_at"] = max(filter(None, (group["last_known_at"], known)))
        if row.get("fees" if source == "paper" else "fee") is None:
            group["fees_missing"] += 1
        outcome_visible = False
        if source == "paper" and row.get("pnl") is not None:
            # The catalog keeps the latest imported row; its outcome is masked until closed_at.
            outcome_visible = visible and _before(row.get("closed_at"), as_of)
            group["outcomes_known_by_as_of" if outcome_visible else "outcomes_after_as_of"] += 1
            if outcome_visible and float(row["pnl"]) < 0:
                group["losses_known_by_as_of"] += 1
        ids.append(f"{source}:{source_id}")
        if include_rows and visible and len(rows) < ROW_LIMIT:
            rows.append({
                "source": source, "source_id": source_id, "known_at": known,
                **{key: row.get(key) for key in (
                    "strategy", "experiment_deployment_arm_id", "side", "action", "status",
                    "assumed_price", "price", "quantity", "fill_assumption", "is_twin")},
                "fees": row.get("fees" if source == "paper" else "fee"),
                "pnl": row.get("pnl") if outcome_visible else None,
                "outcome_masked_after_as_of": source == "paper" and not outcome_visible,
            })
    return {
        "groups": sorted(groups.values(), key=lambda g: str(
            (g["source"], g["strategy"], g["deployment_arm_id"], g["is_twin"]))),
        "source_ids": ids[:ID_LIMIT],
        "source_ids_total": len(ids),
        "source_ids_truncated": len(ids) > ID_LIMIT,
        "source_ids_fingerprint": digest(ids),
        "rows": rows if include_rows else None,
        "rows_truncated": include_rows and len(rows) >= ROW_LIMIT,
        "paper_live_pooled": False,
    }


def _economics(db, ticker, as_of, computed_at):
    row = db.execute(
        "SELECT e.id,e.document FROM live_economics e JOIN current_live_economics c "
        "ON c.economics_id=e.id WHERE c.ticker=?",
        (ticker,),
    ).fetchone()
    if not row:
        return None
    document = unpack(row[1])
    return {
        "record_id": row[0],
        **{key: document.get(key) for key in (
            "method_version", "status", "blocked_reasons", "strategy_version",
            "deployment_arm_id", "side", "source_fill_count", "buy_contracts", "net_pnl_dollars",
            "actual_fees_dollars", "first_fill_at", "last_fill_at", "settlement_result",
            "settlement_at", "exchange_fill_coverage_verified", "exchange_execution_times_verified")},
        "computed_at": computed_at,
        "outcome_known_after_as_of": not _before(document.get("settlement_at"), as_of),
        "fills_after_as_of": bool(document.get("last_fill_at"))
        and not _before(document.get("last_fill_at"), as_of),
        "exchange_account_coverage_verified": False,
    }


def _item(store, db, ticker, stored, as_of, include_rows, computed_at, evidence_ready):
    document = store.decorate(unpack(stored))
    raw = document["raw"]
    revisions = db.execute(
        "SELECT captured_at,document FROM revisions WHERE kind='market' AND ticker=? "
        "ORDER BY rowid LIMIT 501",
        (ticker,),
    ).fetchall()
    observed = [(c, unpack(d)) for c, d in revisions[:500]]
    prior = [(c, d) for c, d in observed if _before(c, as_of)]
    at = _observation(*prior[-1]) if prior else None
    later = [d for c, d in observed if not _before(c, as_of)]
    coverage = selection.coverage_in(db, "market", ticker, document)
    memberships = [m for m in coverage["memberships"] if m["active"]]
    market_review = _review(db, "market", ticker, as_of)
    if not market_review and at and at["representation"] == selection.FULL:
        market_review = _template_review(db, document["series_ticker"], prior[-1][1], as_of)
    series_review = _review(db, "series", document["series_ticker"], as_of)
    missing = list(coverage["missing"])
    if at is None:
        missing.append("not_observed_by_as_of")
    elif at["representation"] == selection.COMPACT:
        missing.append("as_of_observation_compact")
    if not market_review:
        missing.append("market_review_missing_at_as_of")
    elif at and market_review.get("basis") != SERIES_TEMPLATE and (
        market_review["rules_hash"] != at["rules_hash"]
    ):
        missing.append("market_review_not_bound_to_as_of_rules")
    if not series_review:
        missing.append("series_review_missing_at_as_of")
    if not evidence_ready:
        missing.append("evidence_market_index_incomplete")
    selected_by_as_of = any(_before(m["effective_at"], as_of) for m in memberships)
    if memberships and not selected_by_as_of:
        missing.append("selection_recorded_after_as_of")
    return {
        "market_ticker": ticker,
        "series_ticker": document["series_ticker"],
        "event_ticker": raw.get("event_ticker"),
        "as_of_observation": at,
        "rules_changed_after_as_of": bool(at) and any(
            d.get("rules_hash") != at["rules_hash"] for d in later),
        "current": {
            "rules_hash": document["rules_hash"],
            "status": raw.get("status"),
            "result": raw.get("result"),
            "close_time": raw.get("close_time"),
            "first_seen_at": document.get("first_seen_at"),
            "last_seen_at": document.get("last_seen_at"),
            "exchange_created_at": document.get("exchange_created_at"),
            "review_status": document["review_status"],
            "parent_rules_changed": document["parent_rules_changed"],
            "contract_type": document.get("contract_type"),
            "settlement_type": document.get("settlement_type"),
            "legacy_classification": document.get("legacy_classification"),
        },
        "reviews_at_as_of": {"market": market_review, "series": series_review},
        "contract_documents": [
            {
                **{key: ref.get(key) for key in (
                    "field", "url", "status", "content_sha256", "version_at_execution_verified",
                    "contract_document_binding_verified")},
                "capture_at_or_before_as_of": _capture_before(db, ref.get("url"), as_of),
            }
            for ref in document.get("contract_documents", [])
        ],
        "selection": {
            "selected_now": bool(memberships),
            "selected_by_as_of": selected_by_as_of,
            "memberships": memberships,
            "selection_is_qualification": False,
        },
        "history_coverage": {k: v for k, v in coverage.items() if k != "memberships"},
        "evidence": _evidence(db, ticker, as_of, include_rows),
        "live_economics": _economics(db, ticker, as_of, computed_at),
        "missing": sorted(set(missing)),
        "confidence_score": None,
        "qualified": False,
    }


def cohort(store, series=None, as_of=None, population="all", cursor=None, limit=50,
           include_rows=False):
    if not series or not selection.TICKER.fullmatch(series):
        raise ValueError("A cohort requires one series ticker")
    if population not in POPULATIONS:
        raise ValueError("Population must be all, selected or unselected")
    if not 1 <= limit <= 100:
        raise ValueError("Cohort page must have 1–100 markets")
    if cursor is not None and not selection.TICKER.fullmatch(cursor):
        raise ValueError("Invalid cursor")
    stamp = _parse(as_of)
    prefix, upper = series + "-", series + "."
    definition = {
        "series": series,
        "as_of": stamp.isoformat(),
        "population": population,
        "limit": limit,
        "include_rows": include_rows,
        "membership_version": selection.MEMBERSHIP_VERSION,
    }
    items, examined, after = [], 0, max(cursor or "", prefix)
    exhausted = False
    with store.connect() as db:
        evidence_ready = selection.membership_ready(db)
        computed_at = (selection._state(db, "evaluation") or {}).get("last_success_at")
        counts = None
        if cursor is None:
            counts = {
                # Index-only counts: the stored series column versus the ticker-prefix range
                # this endpoint pages through. A difference is named, not hidden.
                "series_market_count": db.execute(
                    "SELECT count(*) FROM objects WHERE kind='market' AND series=?", (series,)
                ).fetchone()[0],
                "prefix_market_count": db.execute(
                    "SELECT count(*) FROM objects WHERE kind='market' AND ticker>=? AND ticker<?",
                    (prefix, upper),
                ).fetchone()[0],
            }
            counts["prefix_population_complete"] = (
                counts["series_market_count"] == counts["prefix_market_count"]
            )
        while len(items) < limit and examined < MAX_SCAN:
            batch = db.execute(
                "SELECT ticker,series,document FROM objects WHERE kind='market' AND ticker>? "
                "AND ticker<? ORDER BY ticker LIMIT ?",
                (after, upper, min(500, MAX_SCAN - examined)),
            ).fetchall()
            if not batch:
                exhausted = True
                break
            for ticker, stored_series, stored in batch:
                examined += 1
                after = ticker
                if stored_series != series:
                    continue
                if population != "all":
                    event = unpack(stored)["raw"].get("event_ticker")
                    if selection.selected_in(db, "market", ticker, series, event) != (
                        population == "selected"
                    ):
                        continue
                items.append(_item(store, db, ticker, stored, stamp, include_rows,
                                   computed_at, evidence_ready))
                if len(items) >= limit:
                    break
        policy = selection.status_in(db)
    more = not exhausted and (len(items) >= limit or examined >= MAX_SCAN)
    return {
        "schema_version": 1,
        "method_version": METHOD,
        "captured_at": now(),
        "definition": definition,
        "cohort_id": digest(definition),
        "population_basis": "market tickers prefixed '<series>-' whose stored series matches; "
        "selection filters use current membership",
        **(counts or {}),
        "items": items,
        "page_fingerprint": digest(items),
        "next_cursor": after if more else None,
        "examined": examined,
        "scan_capped": examined >= MAX_SCAN and len(items) < limit,
        "policy": {key: policy[key] for key in (
            "policy_configured", "policy_effective", "membership_ready", "revision_writes")},
        "evidence_market_index_complete": evidence_ready,
        "missing_denominators": MISSING_DENOMINATORS,
        "as_of_semantics": "revisions by catalog capture time; live evidence by source collection "
        "time; paper entries by created_at and outcomes by closed_at; reviews by created_at",
        "reproducibility": "deterministic for this catalog state; later imports whose known time "
        "precedes as_of can extend evidence, and current-only fields are labelled current",
        "epoch_boundaries": "rows keep strategy and deployment arm; Experiment OS epoch "
        "poolability is not evaluated here and must be honoured by the consumer",
        "selection_is_qualification": False,
        "selected_sample_is_unbiased_control": False,
        "paper_live_pooled": False,
        "confidence_score": None,
        "qualified": False,
        "advisory_only": True,
    }
