WITH candidates AS MATERIALIZED (
    SELECT live_tag, market_ticker, min(recorded_at) AS first_at,
           min(recorded_at) FILTER (WHERE live_outcome = 'gate:open_cap') AS first_cap_at,
           bool_or(live_outcome = 'placed') AS placed,
           bool_or(live_outcome = 'gate:open_cap') AS open_cap,
           bool_or(parent_outcome = 'skip_open_cap' OR twin_outcome = 'skip_open_cap') AS paper_cap,
           bool_or(parent_outcome = 'skip_contest_cap' OR twin_outcome = 'skip_contest_cap') AS contest_cap,
           bool_or(parent_outcome = 'skip_live_tier' OR twin_outcome = 'skip_live_tier') AS tier,
           bool_or(parent_outcome = 'skip_live_paused' OR twin_outcome = 'skip_live_paused') AS paused,
           (array_agg(no_bid ORDER BY recorded_at) FILTER (WHERE live_outcome = 'gate:open_cap'))[1] AS cap_price_min,
           array_agg(DISTINCT coalesce(live_outcome, '(null)')) AS outcomes,
           count(*) AS repeated_rows
    FROM live_paper_parity_events
    WHERE live_tag IN ('Fmmsell10', 'Hmmsell10')
      AND recorded_at >= '__SINCE__'::timestamptz AND recorded_at < '__UNTIL__'::timestamptz
    GROUP BY live_tag, market_ticker
), settlements AS MATERIALIZED (
    SELECT market_ticker, min(resolved_value) AS value_min, max(resolved_value) AS value_max
    FROM paper_trades WHERE side = 'no' AND strategy IN ('Fmmsell10', 'Hmmsell10', 'mmsell10')
      AND resolved_value IN (0, 100) AND closed_at < '__UNTIL__'::timestamptz
    GROUP BY market_ticker
)
SELECT json_build_array(c.live_tag, c.market_ticker, extract(epoch FROM c.first_at),
    extract(epoch FROM c.first_cap_at), c.placed, c.open_cap, c.paper_cap, c.contest_cap,
    c.tier, c.paused, c.cap_price_min, c.outcomes, c.repeated_rows,
    CASE WHEN s.value_min = s.value_max THEN s.value_min END) AS row
FROM candidates c LEFT JOIN settlements s USING (market_ticker)
ORDER BY c.live_tag, c.first_at, c.market_ticker
