"""Global experiment monitor (WS-026) — one read-only snapshot of everything running.

Experiment OS stays canonical. This package does not hold a copy of any experiment
state: `snapshot.build_snapshot` asks the Control Tower for its report, adds the
operational facts the Tower does not model (last evidence per arm, entries per
day, systems that run outside Experiment OS), and returns one JSON document that
`page` renders. Nothing here writes, and nothing here is durable.

Modules:
  expectations — the declared `expect` contract (how quiet an arm may go)
  sources      — the registry of systems outside Experiment OS, each with a probe
  snapshot     — build the snapshot (reads) and assemble it (pure)
  page         — render the HTML page and decide whether the owner is notified
"""
