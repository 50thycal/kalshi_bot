"""Selective detailed history (DEC-034): broad inventory for every object, complete semantic
snapshots for selected contexts.

Selection records relevance — imported evidence, a review, registry provenance or an explicit
research choice. It is never qualification, confidence or an unbiased control. Missing or
unready membership keeps full history: the only path to a compact revision is a configured
`selective` policy AND a completed bootstrap of the current membership version AND an object
no active member covers. Nothing here deletes or rewrites an existing revision.
"""

import os
import re
import time

from .store import RULE_FIELDS, digest, now, pack, unpack

# Default keeps today's behaviour. `selective` takes effect only once membership is ready.
POLICY = os.environ.get("CATALOG_HISTORY_POLICY", "broad_full")
MEMBERSHIP_VERSION = "selection-membership-v1"
FULL = "full-semantic-snapshot-v1"
COMPACT = "compact-observation-v1"
SCOPES = ("market", "event", "series")
AUTOMATIC_REASONS = (
    "live_evidence",
    "paper_evidence",
    "market_review",
    "event_review",
    "series_review",
    "legacy_registry",
    "review_draft",
)
RESEARCH = "research"
BOOTSTRAP_PHASES = ("registry", "reviews", "drafts", "evidence")
BOOTSTRAP_BATCH = 2000
HYDRATION_BUDGET = max(1, int(os.environ.get("CATALOG_HYDRATION_REQUESTS", "3")))
HYDRATION_RETRY_SECONDS = 3600
HYDRATION_MAX_ATTEMPTS = 5
TICKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PROVIDER = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")
# Facts outcome attribution needs at every lifecycle change. Rule fields and the constant
# identity fields travel only on rows that carry rules (first sight, or a rules change).
LIFECYCLE_FIELDS = (
    "status",
    "result",
    "settlement_ts",
    "settlement_value",
    "settlement_value_dollars",
    "is_provisional",
    "expiration_value",
    "expiration_time",
    "latest_expiration_time",
    "updated_time",
)
IDENTITY_FIELDS = (
    "event_ticker",
    "series_ticker",
    "created_time",
    "market_type",
    "notional_value",
    "notional_value_dollars",
)
COMPACT_PACK_MINIMUM = 256


def _state(db, key, default=None):
    row = db.execute("SELECT document FROM state WHERE key=?", (key,)).fetchone()
    return unpack(row[0]) if row else default


def _put(db, key, value):
    from .store import Store

    Store.put_state(db, key, value)


def representation(document):
    """Revisions written before this policy carry no marker: they are full snapshots."""
    return document.get("representation", FULL)


def membership_ready(db):
    boot = _state(db, "selection:bootstrap") or {}
    return boot.get("version") == MEMBERSHIP_VERSION and bool(boot.get("complete"))


def membership_ready_for(store):
    with store.connect() as db:
        return membership_ready(db)


def candidates(kind, ticker, series=None, event=None):
    """Membership scopes that cover an object: itself, its event, its series."""
    scopes = [(kind, ticker)]
    if kind == "market" and event:
        scopes.append(("event", event))
    if kind != "series" and series:
        scopes.append(("series", series))
    return scopes


def selected_in(db, kind, ticker, series=None, event=None):
    return any(
        db.execute(
            "SELECT 1 FROM selection_members WHERE scope=? AND ticker=? AND active=1 LIMIT 1",
            scope,
        ).fetchone()
        for scope in candidates(kind, ticker, series, event)
    )


def history_mode(db, kind, ticker, series=None, event=None):
    """Called inside the upsert's write transaction, so a membership publish cannot race it."""
    if POLICY != "selective":
        return "full", "policy_broad_full"
    if not membership_ready(db):
        return "full", "membership_unready"
    if selected_in(db, kind, ticker, series, event):
        return "full", "selected"
    return "compact", "inventory"


def compact_key(semantic):
    # A distinct key: a later full snapshot of the same semantic state is never ignored as
    # "already stored", and the compact row never occupies the full row's identity.
    return digest({"semantic": semantic, "representation": COMPACT})


def compact_observation(document, semantic, include_rules):
    """Rules, lifecycle and provenance only.

    A rule-bearing row (first sight of the object, or any rules/parent change) carries
    `rules` exactly as `rule_hash` reads them plus the parent hashes, so its rules hash is
    re-derivable, and the constant identity fields. A lifecycle row omits all of that: the
    previous observation had the same rules hash, so an earlier revision carries those
    values. Prices, sizes, volume and open interest are never kept."""
    raw = document["raw"]
    compact = {
        "schema_version": 1,
        "representation": COMPACT,
        "full_payload_retained": False,
        "satisfies_payload_proof": False,
        "kind": document["kind"],
        "ticker": document["ticker"],
        "source": document["source"],
        "captured_at": document["last_seen_at"],
        "rules_hash": document["rules_hash"],
        "semantic_hash": semantic,
        "lifecycle": {key: raw[key] for key in LIFECYCLE_FIELDS if key in raw},
        "rules": None,
    }
    if include_rules:
        compact.update(
            series_ticker=document["series_ticker"],
            first_seen_at=document["first_seen_at"],
            exchange_created_at=document["exchange_created_at"],
            parent_rules_hashes=document["parent_rules_hashes"],
            identity={key: raw[key] for key in IDENTITY_FIELDS if key in raw},
            rules={key: raw.get(key) for key in RULE_FIELDS},
        )
    compact["content_sha256"] = digest(compact)
    return compact


def count_write(db, mode):
    writes = _state(db, "history:writes") or {"full": 0, "compact": 0, "since": now()}
    writes[mode] = writes.get(mode, 0) + 1
    _put(db, "history:writes", writes)


def _counts(db, reason=None, scope=None, member_delta=0, object_delta=0):
    counts = _state(db, "selection:counts") or {"active_by_reason": {}, "selected_by_scope": {}}
    if reason:
        by_reason = counts["active_by_reason"]
        by_reason[reason] = by_reason.get(reason, 0) + member_delta
    if scope and object_delta:
        by_scope = counts["selected_by_scope"]
        by_scope[scope] = by_scope.get(scope, 0) + object_delta
    _put(db, "selection:counts", counts)


def _event(db, action, document, stamp):
    record = {"action": action, "recorded_at": stamp, **document}
    db.execute(
        "INSERT OR IGNORE INTO selection_events VALUES (?,?,?,?,?)",
        (digest(record), document["scope"], document["ticker"], stamp, pack(record)),
    )


def _has_compact(db, kind, ticker):
    for row in db.execute(
        "SELECT document FROM revisions WHERE kind=? AND ticker=?", (kind, ticker)
    ):
        if representation(unpack(row[0])) == COMPACT:
            return True
    return False


def _needs_hydration(db, scope, ticker):
    """Only history that was actually written (or thinned, DEC-038) compact has anything to
    recover."""
    thinned = ((_state(db, "retention:execute") or {}).get("revisions") or {}).get(
        "rows_compacted"
    )
    if not (_state(db, "history:writes") or {}).get("compact") and not thinned:
        return False
    if scope == "series":
        return True
    return _has_compact(db, scope, ticker)


def enqueue(db, scope, ticker, stamp=None):
    db.execute(
        "INSERT OR IGNORE INTO hydration_targets "
        "(scope,ticker,status,next_check,enqueued_at,attempts,records) "
        "VALUES (?,?,'pending',0,?,0,0)",
        (scope, ticker, stamp or now()),
    )


def select_in(db, scope, ticker, reason, provider, *, source_ref=None, actor=None, note=None):
    """Activate one membership inside the caller's transaction. Returns True if it was new.

    Automatic callers pass source tickers as stored; only explicit requests are pattern-checked,
    so an unusual source ticker can never block an evidence import or lose its membership."""
    if scope not in SCOPES or not isinstance(ticker, str) or not ticker:
        raise ValueError("Selection requires a market, event or series ticker")
    row = db.execute(
        "SELECT active FROM selection_members WHERE scope=? AND ticker=? AND reason=? "
        "AND provider=?",
        (scope, ticker, reason, provider),
    ).fetchone()
    if row and row[0]:
        return False
    already = db.execute(
        "SELECT 1 FROM selection_members WHERE scope=? AND ticker=? AND active=1 LIMIT 1",
        (scope, ticker),
    ).fetchone()
    stamp = now()
    document = {
        "membership_version": MEMBERSHIP_VERSION,
        "scope": scope,
        "ticker": ticker,
        "reason": reason,
        "provider": provider,
        "automatic": reason in AUTOMATIC_REASONS,
        "source_ref": source_ref,
        "actor": actor,
        "note": note,
        "selected_at": stamp,
        "effective_at": stamp,
        "selection_is_qualification": False,
        "selected_sample_is_unbiased_control": False,
    }
    db.execute(
        "INSERT OR REPLACE INTO selection_members VALUES (?,?,?,?,1,?,?,?,?)",
        (scope, ticker, reason, provider, stamp, stamp, stamp, pack(document)),
    )
    _event(db, "select", document, stamp)
    _counts(db, reason, scope, 1, 0 if already else 1)
    if not already and _needs_hydration(db, scope, ticker):
        enqueue(db, scope, ticker, stamp)
    return True


def deselect_in(db, scope, ticker, provider, *, actor=None, note=None):
    """End one explicit research membership. Automatic relevance and history are untouched."""
    row = db.execute(
        "SELECT document FROM selection_members WHERE scope=? AND ticker=? AND reason=? "
        "AND provider=? AND active=1",
        (scope, ticker, RESEARCH, provider),
    ).fetchone()
    if not row:
        raise LookupError("No active research selection for this scope, ticker and provider")
    stamp = now()
    document = {**unpack(row[0]), "deselected_at": stamp, "deselected_by": actor,
                "deselection_note": note}
    db.execute(
        "UPDATE selection_members SET active=0,updated_at=?,document=? WHERE scope=? AND "
        "ticker=? AND reason=? AND provider=?",
        (stamp, pack(document), scope, ticker, RESEARCH, provider),
    )
    _event(db, "deselect", document, stamp)
    remaining = [
        {"reason": r[0], "provider": r[1]}
        for r in db.execute(
            "SELECT reason,provider FROM selection_members WHERE scope=? AND ticker=? "
            "AND active=1 ORDER BY reason,provider",
            (scope, ticker),
        )
    ]
    _counts(db, RESEARCH, scope, -1, 0 if remaining else -1)
    return remaining


def research_request(store, payload):
    """Authenticated, bounded explicit selection. Never deletes history or automatic members."""
    action = payload.get("action", "select")
    scope, ticker = payload.get("scope"), payload.get("ticker")
    provider = payload.get("provider", RESEARCH)
    actor, note = payload.get("actor"), payload.get("reason")
    if action not in ("select", "deselect"):
        raise ValueError("Action must be select or deselect")
    if scope not in SCOPES or not isinstance(ticker, str) or not TICKER.fullmatch(ticker):
        raise ValueError("Selection requires a market, event or series ticker")
    if not isinstance(provider, str) or not PROVIDER.fullmatch(provider):
        raise ValueError("Provider must be 1–40 lowercase letters, digits, - or _")
    if not isinstance(actor, str) or not 0 < len(actor) <= 80:
        raise ValueError("Selection requires an actor of at most 80 characters")
    if not isinstance(note, str) or not 0 < len(note) <= 500:
        raise ValueError("Selection requires a reason of at most 500 characters")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if action == "deselect":
            remaining = deselect_in(db, scope, ticker, provider, actor=actor, note=note)
            return {"action": action, "scope": scope, "ticker": ticker, "provider": provider,
                    "history_deleted": False, "remaining_memberships": remaining,
                    "still_selected": bool(remaining)}
        created = select_in(db, scope, ticker, RESEARCH, provider, source_ref="api:research",
                            actor=actor, note=note)
        target = db.execute(
            "SELECT status FROM hydration_targets WHERE scope=? AND ticker=?", (scope, ticker)
        ).fetchone()
    return {"action": action, "scope": scope, "ticker": ticker, "provider": provider,
            "created": created, "hydration": target[0] if target else None,
            "selection_is_qualification": False}


def _bootstrap_rows(db, phase, after, batch, drafts):
    """One bounded page of one phase: (rows, next_after, memberships added)."""
    added = 0
    if phase == "registry":
        rows = db.execute(
            "SELECT series,migration_id FROM current_review_migrations WHERE series>? "
            "ORDER BY series LIMIT ?",
            (after or "", batch),
        ).fetchall()
        for series, migration_id in rows:
            added += select_in(db, "series", series, "legacy_registry", "catalog",
                               source_ref="review_migration:" + migration_id)
        return rows, rows[-1][0] if rows else None, added
    if phase == "reviews":
        rows = db.execute(
            "SELECT rowid,kind,ticker,id FROM reviews WHERE rowid>? ORDER BY rowid LIMIT ?",
            (after or 0, batch),
        ).fetchall()
        for _rowid, kind, ticker, review_id in rows:
            if kind in SCOPES:
                added += select_in(db, kind, ticker, kind + "_review", "catalog",
                                   source_ref="review:" + review_id)
        return rows, rows[-1][0] if rows else None, added
    if phase == "drafts":
        start = after or 0
        rows = drafts[start : start + batch]
        for ticker in rows:
            added += select_in(db, "market", ticker, "review_draft", "catalog",
                               source_ref="review_drafts.json")
        return rows, start + len(rows), added
    rows = db.execute(
        "SELECT e.rowid,e.source,e.source_id,e.document,m.market FROM evidence e "
        "LEFT JOIN evidence_markets m ON m.source=e.source AND m.source_id=e.source_id "
        "WHERE e.rowid>? ORDER BY e.rowid LIMIT ?",
        (after or 0, batch),
    ).fetchall()
    for _rowid, source, source_id, document, market in rows:
        ticker = market or unpack(document)["market_ticker"]
        if market is None:
            # Backfills the per-market index cohort reads use; evidence itself is untouched.
            db.execute("INSERT OR REPLACE INTO evidence_markets VALUES (?,?,?)",
                       (source, source_id, ticker))
        added += select_in(db, "market", ticker, source + "_evidence", "catalog",
                           source_ref=f"{source}:{source_id}")
    return rows, rows[-1][0] if rows else None, added


def bootstrap_page(store, batch=BOOTSTRAP_BATCH):
    """Build automatic membership in bounded, resumable, atomic pages.

    Evidence and reviews written by current code publish membership in their own transaction;
    this pass covers everything already stored, then marks the version ready. One call reads
    at most one non-empty page; empty phases are passed in the same transaction."""
    from .readiness import REVIEW_DRAFTS

    drafts = sorted(REVIEW_DRAFTS)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        state = _state(db, "selection:bootstrap") or {}
        if state.get("version") != MEMBERSHIP_VERSION:
            state = {"version": MEMBERSHIP_VERSION, "phase": 0, "after": None,
                     "complete": False, "started_at": now(), "added": 0, "examined": 0}
        if state["complete"]:
            return 0
        examined = 0
        while not state["complete"]:
            phase = BOOTSTRAP_PHASES[state["phase"]]
            rows, after, added = _bootstrap_rows(db, phase, state["after"], batch, drafts)
            examined = len(rows)
            state["added"] += added
            state["examined"] += examined
            if rows:
                state["after"] = after
                break
            state["phase"] += 1
            state["after"] = None
            if state["phase"] == len(BOOTSTRAP_PHASES):
                state["complete"] = True
                state["completed_at"] = now()
                state["covered"] = _coverage_counts(db)
        state["last_page_at"] = now()
        _put(db, "selection:bootstrap", state)
    return examined


def _coverage_counts(db):
    return {
        "evidence": db.execute("SELECT count(*) FROM evidence").fetchone()[0],
        "evidence_markets": db.execute("SELECT count(*) FROM evidence_markets").fetchone()[0],
        "reviews": db.execute("SELECT count(*) FROM reviews").fetchone()[0],
    }


def verify_bootstrap(store):
    """At startup: if an image without this code wrote evidence or reviews after membership
    was built (a rollback, then a roll forward), reopen the bootstrap. Until it completes
    again, every write keeps full history."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        state = _state(db, "selection:bootstrap") or {}
        if not state.get("complete") or state.get("version") != MEMBERSHIP_VERSION:
            return False
        counts = _coverage_counts(db)
        covered = state.get("covered") or {}
        # Evidence rows are never deleted, so equal counts mean every row is indexed (and was
        # selected when indexed). Reviews are few: check each has its membership.
        counts["reviews_without_membership"] = db.execute(
            "SELECT count(*) FROM reviews r WHERE r.kind IN ('market','event','series') AND "
            "NOT EXISTS (SELECT 1 FROM selection_members m WHERE m.scope=r.kind AND "
            "m.ticker=r.ticker AND m.reason=r.kind||'_review')"
        ).fetchone()[0]
        if counts["evidence"] == counts["evidence_markets"] and not counts[
            "reviews_without_membership"
        ]:
            return False
        _put(db, "selection:bootstrap", {
            "version": MEMBERSHIP_VERSION, "phase": 0, "after": None, "complete": False,
            "started_at": now(), "added": 0, "examined": 0,
            "reopened_because": {"found": counts, "covered": covered},
        })
    return True


def policy_tick(store):
    """Record configured/effective policy transitions so gaps can be dated afterwards."""
    with store.connect() as db:
        effective = POLICY == "selective" and membership_ready(db)
        state = _state(db, "selection:policy") or {}
        if state.get("configured") == POLICY and state.get("effective") == effective:
            return state
        stamp = now()
        state = {
            "configured": POLICY,
            "effective": effective,
            "since": stamp,
            "first_selective_at": state.get("first_selective_at") or (stamp if effective else None),
            "transitions": (state.get("transitions") or [])[-19:]
            + [{"at": stamp, "configured": POLICY, "effective": effective}],
        }
        _put(db, "selection:policy", state)
    return state


def _finish(store, scope, ticker, status, records, error=None, cursor=None, attempts=None):
    with store.connect() as db:
        db.execute(
            "UPDATE hydration_targets SET status=?,records=records+?,error=?,cursor=?,"
            "attempted_at=?,completed_at=?,next_check=?,attempts=coalesce(?,attempts) "
            "WHERE scope=? AND ticker=?",
            (
                status,
                records,
                error,
                cursor,
                now(),
                now() if status in ("completed", "unavailable", "failed") else None,
                time.time() + HYDRATION_RETRY_SECONDS if error and status == "pending" else 0,
                attempts,
                scope,
                ticker,
            ),
        )


def hydrate_step(store, discovery, budget=HYDRATION_BUDGET):
    """Fetch what the exchange still serves for newly selected contexts, a few requests a
    cycle. Earlier compact revisions stay as they are; a gap is reported, never filled in."""
    from urllib.parse import quote

    import httpx

    from .ingest import DiscoveryDeferred

    bulk_paused = store.state("storage:guard", {}).get("bulk_paused")
    with store.connect() as db:
        targets = db.execute(
            "SELECT scope,ticker,cursor,attempts FROM hydration_targets WHERE status='pending' "
            "AND next_check<=? ORDER BY next_check,enqueued_at,scope,ticker LIMIT ?",
            (time.time(), budget),
        ).fetchall()
    done = 0
    for scope, ticker, cursor, attempts in targets:
        if scope == "series" and bulk_paused:
            continue  # a whole series is bulk work; selected markets keep hydrating
        try:
            if scope == "market":
                data = None
                for prefix in ("/markets/", "/historical/markets/"):
                    try:
                        data = discovery.get_json(prefix + quote(ticker, safe=""), {})
                        break
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code != 404:
                            raise
                if data is None:
                    _finish(store, scope, ticker, "unavailable", 0,
                            "not_served_by_live_or_historical_endpoint")
                else:
                    market = data["market"]
                    store.upsert_revision(
                        "market", ticker, market,
                        market.get("series_ticker") or ticker.split("-", 1)[0],
                    )
                    _finish(store, scope, ticker, "completed", 1)
            elif scope == "event":
                data = discovery.get_json("/events/" + quote(ticker, safe=""), {"with_nested_markets": "true"})
                event = data["event"]
                series = event.get("series_ticker") or ticker.split("-", 1)[0]
                store.upsert_revision(
                    "event", ticker, {k: v for k, v in event.items() if k != "markets"}, series
                )
                markets = event.get("markets") or data.get("markets") or []
                for market in markets:
                    store.upsert_revision("market", market["ticker"], market, series)
                _finish(store, scope, ticker, "completed", 1 + len(markets))
            else:
                if not cursor:
                    data = discovery.get_json("/series/" + quote(ticker, safe=""), {})
                    store.upsert_revision("series", ticker, data["series"], ticker)
                params = {"series_ticker": ticker, "limit": 200, "mve_filter": "exclude"}
                if cursor:
                    params["cursor"] = cursor
                data = discovery.get_json("/markets", params)
                for market in data["markets"]:
                    store.upsert_revision("market", market["ticker"], market, ticker)
                following = data.get("cursor") or None
                _finish(store, scope, ticker, "pending" if following else "completed",
                        len(data["markets"]) + (0 if cursor else 1), cursor=following)
            done += 1
        except DiscoveryDeferred:
            raise  # shared provider backoff: target, cursor and attempts stay as they are
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429 or error.response.status_code >= 500:
                raise  # throttling recorded by get_json; the target keeps its attempts
            attempts = (attempts or 0) + 1
            _finish(store, scope, ticker,
                    "failed" if attempts >= HYDRATION_MAX_ATTEMPTS else "pending", 0,
                    "http_" + str(error.response.status_code), cursor=cursor, attempts=attempts)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            attempts = (attempts or 0) + 1
            _finish(store, scope, ticker,
                    "failed" if attempts >= HYDRATION_MAX_ATTEMPTS else "pending", 0,
                    type(error).__name__, cursor=cursor, attempts=attempts)
    return done


def coverage_in(db, kind, ticker, document=None, limit=500):
    """What detailed history exists for one object, and what is explicitly missing."""
    raw = (document or {}).get("raw") or {}
    series = (document or {}).get("series_ticker")
    event = raw.get("event_ticker") if kind == "market" else None
    rows = db.execute(
        "SELECT captured_at,document FROM revisions WHERE kind=? AND ticker=? "
        "ORDER BY rowid LIMIT ?",
        (kind, ticker, limit + 1),
    ).fetchall()
    truncated = len(rows) > limit
    full = compact = 0
    first_full = last_compact = None
    for captured_at, stored in rows[:limit]:
        if representation(unpack(stored)) == COMPACT:
            compact += 1
            last_compact = captured_at
        else:
            full += 1
            first_full = first_full or captured_at
    memberships = []
    for scope in candidates(kind, ticker, series, event):
        for row in db.execute(
            "SELECT reason,provider,active,selected_at,effective_at,updated_at "
            "FROM selection_members WHERE scope=? AND ticker=? ORDER BY reason,provider",
            scope,
        ):
            memberships.append({
                "scope": scope[0], "ticker": scope[1], "reason": row[0], "provider": row[1],
                "active": bool(row[2]), "selected_at": row[3], "effective_at": row[4],
                "updated_at": row[5],
            })
    targets = [
        dict(zip(("scope", "ticker", "status", "enqueued_at", "completed_at", "error",
                  "records"), row, strict=True))
        for scope in candidates(kind, ticker, series, event)
        for row in db.execute(
            "SELECT scope,ticker,status,enqueued_at,completed_at,error,records "
            "FROM hydration_targets WHERE scope=? AND ticker=?",
            scope,
        )
    ]
    first_seen = (document or {}).get("first_seen_at")
    created = (document or {}).get("exchange_created_at")
    from .evaluators import timestamp

    late = bool(
        first_seen and created
        and (timestamp(first_seen) - timestamp(created)).total_seconds() > 60
    )
    missing = ["historical_contract_document_binding_unverified"]
    if compact:
        missing.append("compact_revisions_without_full_payload")
    if not rows:
        # Upserts always write a revision; none left means retention kept only the current
        # object (DEC-038, `final_only`).
        missing.append("revisions_retired_current_object_only")
    if late:
        missing.append("observed_after_exchange_creation")
    if truncated:
        missing.append("revision_scan_truncated")
    if any(t["status"] == "unavailable" for t in targets):
        missing.append("hydration_unavailable_from_exchange")
    if any(t["status"] == "pending" for t in targets):
        missing.append("hydration_pending")
    return {
        "selected": any(m["active"] for m in memberships),
        "memberships": memberships,
        "revisions_full": full,
        "revisions_compact": compact,
        "revisions_truncated": truncated,
        "first_full_snapshot_at": first_full,
        "last_compact_observation_at": last_compact,
        "full_payload_history_complete": bool(rows) and not compact and not truncated,
        "observed_from": first_seen,
        "exchange_created_at": created,
        "observed_after_exchange_creation": late,
        "hydration": targets,
        "unobserved_history_recoverable": False,
        "missing": missing,
    }


def status_in(db):
    """Cheap counters only: never scans revisions or objects."""
    boot = _state(db, "selection:bootstrap") or {}
    hydration = dict(
        db.execute("SELECT status,count(*) FROM hydration_targets GROUP BY status").fetchall()
    )
    oldest = db.execute(
        "SELECT min(enqueued_at) FROM hydration_targets WHERE status='pending'"
    ).fetchone()[0]
    policy = _state(db, "selection:policy") or {}
    return {
        "policy_configured": POLICY,
        "policy_effective": POLICY == "selective" and membership_ready(db),
        "policy_history": policy.get("transitions", []),
        "first_selective_at": policy.get("first_selective_at"),
        "membership_version": MEMBERSHIP_VERSION,
        "membership_ready": membership_ready(db),
        "bootstrap": {
            key: boot.get(key)
            for key in ("version", "phase", "complete", "started_at", "completed_at",
                        "added", "examined", "last_page_at")
        },
        "bootstrap_phase_name": BOOTSTRAP_PHASES[boot["phase"]]
        if boot and boot.get("phase", 0) < len(BOOTSTRAP_PHASES)
        else None,
        **(_state(db, "selection:counts") or {"active_by_reason": {}, "selected_by_scope": {}}),
        "hydration": {"by_status": hydration, "oldest_pending_enqueued_at": oldest},
        "revision_writes": _state(db, "history:writes")
        or {"full": 0, "compact": 0, "since": None},
        "compact_revisions_satisfy_payload_proof": False,
        "selection_is_qualification": False,
        "selected_sample_is_unbiased_control": False,
    }


def members(store, scope=None, ticker=None, reason=None, active=None, limit=100, offset=0):
    where, args = [], []
    for column, value in (("scope", scope), ("ticker", ticker), ("reason", reason)):
        if value is not None:
            where.append(column + "=?")
            args.append(value)
    if active is not None:
        where.append("active=?")
        args.append(int(active))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with store.connect() as db:
        rows = db.execute(
            "SELECT active,document FROM selection_members" + clause
            + " ORDER BY scope,ticker,reason,provider LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        result = {
            "items": [{**unpack(r[1]), "active": bool(r[0])} for r in rows],
            "policy": status_in(db),
            "limit": limit,
            "offset": offset,
            "advisory_only": True,
        }
    return result
