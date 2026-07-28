"""Read-only live-vs-paper comparison dashboard.

A dedicated operational surface for a strategy running with real money beside its
paired paper TWIN (docs/LIVE_PAPER_TWIN.md): the same markets, the same window, the
same knobs, differing only in whether the resting order actually filled. The page
answers one question — is our paper trading telling the truth about live execution,
and if not, where does the difference come from?

Runs as its OWN Railway web service (`python -m kalshi_bot.livedash`,
railway.livedash.json) against the same Postgres. It is strictly observational:
only GET/HEAD are served, the data layer contains no write, and there is no
endpoint that can place, cancel, close, pause or reconfigure anything. It cannot
affect the live book it observes.

Modules:
  pairs.py    which live run is paired with which paper run (explicit epoch rows)
  legs.py     canonical extraction of each side: positions, orders, fills, P&L
  marks.py    the ONE mark source both legs are valued against
  series.py   historical P&L and price series, reconstructed from timestamped data
  compare.py  metric comparison + the divergence decomposition
  events.py   the paired-run event timeline
  data.py     payload builders; server.py: routes
"""
