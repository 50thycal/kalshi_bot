__FACTS__
, tick_pairs AS MATERIALIZED (
    SELECT f.id AS order_id, q.captured_at AS at,
           (q.features_json->>'best_yes_ask')::float AS ask,
           (q.features_json->>'no_top3')::float AS depth,
           b.captured_at AS baseline_at,
           (b.features_json->>'best_yes_ask')::float AS baseline_ask,
           (b.features_json->>'no_top3')::float AS baseline_depth
    FROM facts f
    JOIN live_order_queue_ticks q ON q.live_order_id = f.id
    JOIN LATERAL (
        SELECT captured_at, features_json FROM live_order_queue_ticks
        WHERE live_order_id = f.id
          AND captured_at >= q.captured_at - interval '60 seconds'
          AND captured_at <= q.captured_at - interval '40 seconds'
          AND captured_at >= f.observed_start
        ORDER BY captured_at DESC, id DESC LIMIT 1
    ) b ON true
    WHERE q.captured_at >= f.observed_start AND q.captured_at < f.observed_end
      AND q.trigger != 'terminal' AND (q.remaining_count IS NULL OR q.remaining_count > 0)
      AND q.features_json->>'book_valid' = 'true'
      AND b.features_json->>'book_valid' = 'true'
      AND (q.features_json->>'book_age_s')::float <= 30
      AND (b.features_json->>'book_age_s')::float <= 30
), coarse AS MATERIALIZED (
    SELECT * FROM tick_pairs WHERE ask >= baseline_ask + 1
      AND baseline_depth >= 40 AND depth <= baseline_depth * 0.5
      AND baseline_depth - depth >= 20
), checked AS MATERIALIZED (
    SELECT p.*, d.removed, d.levels, d.snapshots, d.connections,
           t.yes_volume, d.raw_events, e.bad_events,
           (d.levels >= 2 AND d.removed >= 20 AND d.snapshots = 0
            AND d.connections = 1 AND e.bad_events = 0
            AND t.yes_volume <= d.removed * 0.1) AS full_signal
    FROM coarse p JOIN facts f ON f.id = p.order_id
    LEFT JOIN LATERAL (
        SELECT coalesce(sum(-delta_fp) FILTER
                   (WHERE kind='delta' AND side='no' AND delta_fp < 0
                    AND NOT coalesce(ours,false) AND price_convention='yes'), 0) AS removed,
               count(DISTINCT price_cents) FILTER
                   (WHERE kind='delta' AND side='no' AND delta_fp < 0
                    AND NOT coalesce(ours,false) AND price_convention='yes') AS levels,
               count(*) FILTER (WHERE kind='snapshot') AS snapshots,
               count(DISTINCT connection_id) AS connections,
               count(*) AS raw_events
        FROM execution_book_events WHERE market_ticker=f.market_ticker
          AND received_at >= p.at - interval '60 seconds' AND received_at <= p.at
    ) d ON true
    LEFT JOIN LATERAL (
        SELECT coalesce(sum(count_fp) FILTER
            (WHERE taker_outcome_side='yes' AND NOT coalesce(is_block_trade,false)),0) AS yes_volume
        FROM execution_trade_events WHERE market_ticker=f.market_ticker
          AND received_at >= p.at - interval '60 seconds' AND received_at <= p.at
    ) t ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS bad_events FROM execution_collector_events
        WHERE at >= p.at - interval '60 seconds' AND at <= p.at
          AND kind IN ('throttled','seq_gap','disconnected','connected','book_invalid',
                       'snapshot_requested','unparsed')
          AND (market_ticker IS NULL OR market_ticker=f.market_ticker)
    ) e ON true
), coverage AS (
    SELECT order_id, count(*) AS paired_ticks FROM tick_pairs GROUP BY order_id
), counts AS (
    SELECT order_id, count(*) AS coarse_windows,
           count(*) FILTER (WHERE bad_events=0 AND raw_events>0 AND snapshots=0
                              AND connections=1) AS clean_windows,
           min(at) AS first_coarse FROM checked GROUP BY order_id
), first_signal AS (
    SELECT DISTINCT ON (order_id) * FROM checked WHERE full_signal ORDER BY order_id, at
)
SELECT json_build_array(f.strategy, f.market_ticker, extract(epoch FROM f.created_at),
    coalesce(c.paired_ticks,0), coalesce(n.coarse_windows,0), coalesce(n.clean_windows,0),
    extract(epoch FROM n.first_coarse), extract(epoch FROM s.at), s.removed, s.levels,
    s.yes_volume, s.baseline_depth, s.depth, s.baseline_ask, s.ask) AS row
FROM facts f LEFT JOIN coverage c ON c.order_id=f.id
LEFT JOIN counts n ON n.order_id=f.id LEFT JOIN first_signal s ON s.order_id=f.id
ORDER BY f.strategy, f.created_at, f.id
