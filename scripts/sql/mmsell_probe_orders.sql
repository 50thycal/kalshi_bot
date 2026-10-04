WITH orders AS MATERIALIZED (
    SELECT o.*, coalesce(c.acked_at, o.created_at) AS observed_start,
           c.close_time, c.hot_entry, c.offset_cents,
           c.open_positions_for_tag, c.open_position_cap,
           least(c.cancel_confirmed_at, x.cancel_at) AS cancel_confirmed_at,
           c.id IS NOT NULL AS context_present
    FROM live_orders o
    LEFT JOIN execution_order_context c ON c.live_order_id = o.id
    LEFT JOIN LATERAL (
        SELECT min(received_at) AS cancel_at FROM execution_order_events
        WHERE kalshi_order_id=o.kalshi_order_id AND status IN ('canceled','cancelled')
          AND received_at < '__UNTIL__'::timestamptz
    ) x ON true
    WHERE o.strategy IN ('Fmmsell10', 'Hmmsell10')
      AND o.action = 'buy' AND o.side = 'no' AND o.kalshi_order_id IS NOT NULL
      AND o.created_at >= '__SINCE__'::timestamptz
      AND o.created_at < '__UNTIL__'::timestamptz
), settlements AS MATERIALIZED (
    SELECT market_ticker, strategy, min(resolved_value) AS value_min,
           max(resolved_value) AS value_max, min(closed_at) AS settled_at
    FROM paper_trades
    WHERE strategy IN ('Fmmsell10', 'Hmmsell10') AND side = 'no'
      AND resolved_value IN (0, 100) AND closed_at < '__UNTIL__'::timestamptz
    GROUP BY market_ticker, strategy
), facts AS MATERIALIZED (
    SELECT o.*, coalesce(nullif(r.qty, 0), w.qty, 0) AS filled,
           coalesce(r.cost / nullif(r.qty, 0), 100 - w.yes_cost / nullif(w.qty, 0)) AS fill_price,
           coalesce(CASE WHEN r.qty > 0 THEN r.fee_cents END, w.fee_cents)
               / nullif(coalesce(nullif(r.qty, 0), w.qty), 0) AS fee_cents,
           coalesce(w.first_ts, r.first_ts) AS first_fill,
           CASE WHEN s.value_min = s.value_max THEN s.value_min END AS settle_no,
           s.settled_at,
           least(o.created_at + interval '4 hours', o.close_time,
                 o.cancel_confirmed_at, coalesce(w.first_ts, r.first_ts),
                 '__UNTIL__'::timestamptz) AS observed_end
    FROM orders o
    LEFT JOIN settlements s ON s.market_ticker = o.market_ticker AND s.strategy = o.strategy
    LEFT JOIN LATERAL (
        SELECT sum(quantity) AS qty, sum(quantity * price) AS cost,
               CASE WHEN count(fee) = count(*) THEN sum(fee) * 100 END AS fee_cents,
               min(nullif(raw_fill_json->>'created_time', '')::timestamptz) AS first_ts
        FROM fills WHERE kalshi_order_id = o.kalshi_order_id
          AND nullif(raw_fill_json->>'created_time', '')::timestamptz < '__UNTIL__'::timestamptz
    ) r ON true
    LEFT JOIN LATERAL (
        SELECT sum(count_fp) AS qty, sum(count_fp * yes_price_cents) AS yes_cost,
               CASE WHEN count(fee_cost) = count(*) THEN sum(fee_cost) * 100 END AS fee_cents,
               to_timestamp(min(ts_ms) / 1000.0) AS first_ts
        FROM execution_fill_events WHERE kalshi_order_id = o.kalshi_order_id
          AND ts_ms < extract(epoch FROM '__UNTIL__'::timestamptz)*1000
    ) w ON true
), rows AS (
    SELECT json_build_array(
        f.strategy, f.market_ticker, extract(epoch FROM f.created_at), f.limit_price,
        f.quantity, f.status, f.filled, f.fill_price, f.fee_cents,
        extract(epoch FROM f.first_fill), f.settle_no, extract(epoch FROM f.settled_at),
        f.context_present, f.hot_entry, f.offset_cents,
        extract(epoch FROM f.observed_start), extract(epoch FROM f.observed_end),
        q.n, q.valid_n, extract(epoch FROM q.first_tick), extract(epoch FROM q.last_tick),
        t.n, extract(epoch FROM t.control_at), extract(epoch FROM t.touch_at),
        extract(epoch FROM t.through_at), f.open_positions_for_tag, f.open_position_cap,
        extract(epoch FROM f.cancel_confirmed_at), extract(epoch FROM f.close_time)
    ) AS row
    FROM facts f
    LEFT JOIN LATERAL (
        SELECT count(*) AS n,
               count(*) FILTER (WHERE features_json->>'book_valid' = 'true') AS valid_n,
               min(captured_at) AS first_tick, max(captured_at) AS last_tick
        FROM live_order_queue_ticks WHERE live_order_id = f.id
          AND captured_at >= f.observed_start AND captured_at <= f.observed_end
          AND features_json IS NOT NULL
    ) q ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS n,
               min(to_timestamp(ts_ms / 1000.0)) FILTER
                   (WHERE yes_price_cents >= 100 - f.limit_price) AS control_at,
               min(to_timestamp(ts_ms / 1000.0)) FILTER
                   (WHERE yes_price_cents >= 101 - f.limit_price) AS touch_at,
               min(to_timestamp(ts_ms / 1000.0)) FILTER
                   (WHERE yes_price_cents > 101 - f.limit_price) AS through_at
        FROM execution_trade_events WHERE market_ticker = f.market_ticker
          AND received_at >= f.observed_start - interval '1 second'
          AND received_at <= f.observed_end + interval '10 seconds'
          AND ts_ms >= extract(epoch FROM f.observed_start) * 1000
          AND ts_ms <= extract(epoch FROM f.observed_end) * 1000
          AND taker_outcome_side = 'yes' AND NOT coalesce(is_block_trade, false)
          AND count_fp >= 1
    ) t ON true
    ORDER BY f.strategy, f.created_at, f.id
)
SELECT row FROM rows
