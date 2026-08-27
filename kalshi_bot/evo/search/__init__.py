"""Historical search — a capability the Evo agents invoke, not a population.

An agent asks: *"around the strategy I am running, over this window of settled history,
what would these variants have done?"* This package replays the base spec and a bounded
neighbourhood around it, gates every proposed variant, scores what survived, and returns
**evidence**. The agent reads it, reasons about it, and may then adopt a variant through
the organism's own `save_strategy` / `activate_strategy` — or decline to.

```text
EvoAgent  (kalshi_bot/evo/ — authoritative)
  cognitive genome · memory/beliefs · heartbeats · research · peer learning
  cohort fitness + selection · reproduction · retirement
  trading genome + active strategy (evo_strategies)
      │  invokes, from its own heartbeat, against its own sandbox budget
      ▼
  historical search  (here)
      deterministic replay · parameter-neighbourhood search · bounded mutation
      proposals · novelty/duplicate checks · per-replay ledger · explainable scoring
      │  returns EVIDENCE
      ▼
  the AGENT reasons and decides
```

Four things this package will not do, each structural rather than conventional:

1. **It runs no lifecycle.** There is no candidate, generation, cohort, reproduction or
   retirement here. `evo_agents`, `evo_cohorts`, `evo_genomes`, `evo_fitness`,
   `evo_births` and `evo_retirements` own all of that and stay authoritative.
2. **It writes no genome.** `run_search` returns a dict. Only the agent, through the
   organism's own action path, changes an agent.
3. **Its scoring is not agent fitness.** See `fitness.py` — `insufficient → unranked` is
   a property of measuring strategies, and would be an immunity from selection if it
   ever reached the organism.
4. **It cannot trade.** The replay reads settled history and the ledger is virtual. There
   is no order path, no executor import, no arming call.

Parameter perturbation is a bounded operator the agent can point at a dimension. The
hypothesis is the agent's; the search only measures.
"""

from __future__ import annotations

# Registers evo_search_* on the shared Base. Importing the package is enough to make
# the tables exist for create_all / Alembic autogenerate, so a caller never has to know
# which submodule happens to define them.
from . import models  # noqa: F401

__all__ = ["genome", "replay", "fitness", "mutation", "diversity", "models", "search"]
