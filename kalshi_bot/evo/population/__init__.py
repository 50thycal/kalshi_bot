"""Evo population layer — a controlled evolutionary search over strategy genomes.

This is **not** the LLM-agent organism in `kalshi_bot/evo/` (agents, heartbeats,
cognition, memory, prospective paper). That system runs in wall-clock time and its
`EvoAgent`/`EvoCohort`/`EvoGenome` mean something else. This package is a parallel
layer with its own `evo_pop_*` namespace:

    EvoProgram → EvoGeneration → EvoCandidate → EvoGenomeVersion → EvoRun → trades

A *program* is one evolutionary configuration. A *generation* is one evaluation
population over one environment/window. A *candidate* is a durable identity whose
lineage of immutable *genome versions* is what actually evolves. A *run* is one
genome evaluated against one window, and it owns its own virtual ledger.

Three rules the rest of the package exists to enforce:

1. **A genome is immutable once evaluated.** A material change makes a new version,
   never an edit. `genome.py` hashes the normalized document so this is checkable.
2. **PROPOSE is separate from ACCEPT.** Anything — a sweep, a perturbation, a
   research finding, eventually an LLM — may propose a mutation. Only
   `mutation.admit_proposal` writes a genome, and only after schema, compatibility,
   risk, and novelty checks pass.
3. **Experiment OS stays canonical.** This layer records the XOS platform-snapshot
   fingerprint for provenance and reuses XOS metric definitions, but it does not
   register experiments, move lifecycle states, or import evidence into XOS. A
   candidate that earns formal advancement enters the normal XOS path; there is no
   `EVO_LIVE`.

No module here can place a real order: the replay engine reads settled history and
the ledger is virtual throughout.
"""

from __future__ import annotations

__all__ = ["models", "genome", "replay", "fitness", "mutation", "evolution", "service"]
