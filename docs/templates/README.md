# Build OS templates

Copies of the **Build OS v0.4** templates, from the canonical framework repository
`50thycal/build-os` (`templates/`). They live here so the shape of a Build Card, a Build
Spec, a workstream file and a PR handoff is discoverable without leaving this repository.

| File | Used when | Read the protocol in |
|---|---|---|
| `BUILD_CARD.template.md` | The owner-facing design is being finalized (phase `BUILD_CARD`). 30–60 second read. | `framework/DESIGN_ROOM.md` |
| `BUILD_SPEC.template.md` | An approved Build Card is being turned into an implementation packet. Exhaustive; the owner is not expected to read it line by line. | `framework/BUILD_SPEC.md` |
| `WORKSTREAM.template.md` | A new design/build thread earns a `WS-###`. | `framework/WORKSTREAMS.md` |
| `PR_HANDOFF.template.md` | Every implementation PR. Mirrored as `.github/pull_request_template.md`, so it is the default. | `framework/CLAUDE_HANDOFF.md` |

**These are copies, not the source.** If the protocol itself is wrong, incomplete or
awkward, fix it in `50thycal/build-os` and pick the change up at the next compatibility
check — do not fork the protocol by editing these files. See `CLAUDE.md` → *Build OS*.

## Project-specific notes

- **Link to Experiment OS; never restate it.** A Build Card, spec, workstream or handoff may
  reference an experiment, Version, epoch, deployment, gate, platform revision or XOS issue.
  It must not copy a standing, a gate verdict, an epoch boundary or a P&L figure —
  Experiment OS answers those live, and a copy is stale the day after it is written
  (`DEC-001`).
- **A Build Card never authorizes an experiment action.** Owner approval of a card is
  approval of a *design*. Registering, arming, promoting, pausing or retiring happens only
  through Experiment OS's own services, under its own approval rules.
- **A spec that touches shared semantics** — fees, fills, the market taxonomy, execution,
  risk, data provenance, metric definitions — is a Platform Change Review event. Say so in
  the spec, and expect the impact review before the merge, not after.
