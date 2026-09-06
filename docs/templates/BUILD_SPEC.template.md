# Build Spec — <feature name>

<!-- For the implementation agent. Exhaustive about behavior, quiet about implementation.
     Mark sections that don't apply as `N/A` rather than deleting them.
     Number everything — OD-n, R-n, AC-n — so handoffs and reviews can reference it. -->

**Workstream:** WS-### · **Build Card:** <link> · **Written under Build OS v0.x**
<!-- Run the framework compatibility check before writing a spec — framework/FRAMEWORK_SYNC.md -->

---

## The three-way split

### Owner decisions — may not be silently changed

- **OD-1.**
- **OD-2.**

### Implementation discretion — yours to decide

<Say it positively so the agent doesn't over-escalate. Default: internal structure,
naming, data structures, algorithms, libraries within existing dependencies, error
handling mechanics, logging detail, test layout, behavior-preserving refactors.>

### Stop / escalation conditions

Stop and raise it — do not improvise a product-level behavior change — if:

- An owner decision cannot be implemented as written
- Two owner decisions conflict in a case neither anticipated
- User-visible behavior is required that the Build Card doesn't cover
- An invariant in `PROJECT_MODEL.md` would break
- Existing out-of-scope behavior must change
- Data loss or an irreversible operation is required unexpectedly
- A security, privacy, or compliance constraint conflicts with the spec

Otherwise prefer reasonable technical judgment. Escalation is for product behavior, not
technical uncertainty.

---

## 1. Objective

## 2. Owner-approved behavior

> After this change, the system should <quoted from the Build Card>.

<Owner decisions restated, plus the card's important rules in enforceable form.>

## 3. Repository context

## 4. Architecture constraints

## 5. Implementation requirements

- **R-1.** <testable requirement> <!-- tag with (OD-n) where it implements a decision -->
- **R-2.**

## 6. State transitions

| From | To | Trigger | Guard | Side effects |
|---|---|---|---|---|
|  |  |  |  |  |

Illegal transitions: <what happens.>

## 7. Interfaces

## 8. Persistence changes

## 9. Migration requirements

## 10. Failure behavior

| Failure | Retryable | User sees | System state left |
|---|---|---|---|
|  |  |  |  |

## 11. Concurrency / idempotency

## 12. Observability

## 13. Backwards compatibility

## 14. Security / privacy constraints

## 15. Edge cases

<!-- Each with a defined expected behavior. An edge case without an expectation is an
     unresolved design question. -->

| Case | Expected behavior |
|---|---|
|  |  |

## 16. Tests

<Behaviors to verify, not file layout.>

## 17. Acceptance criteria

- [ ] **AC-1.**
- [ ] **AC-2.**

## 18. Non-goals

## 19. Required documentation updates

- [ ] `PROJECT_MODEL.md` — <which sections>
- [ ] `DECISIONS.md` — <expected decisions>
- [ ] `docs/workstreams/WS-###-<slug>.md` — phase, implementation state, PR, next step
- [ ] `docs/workstreams/ACTIVE.md` — row for WS-###

<!-- If the design agent could not write to GitHub, put its precise repository-update block
     here for the implementation agent to apply. -->

## 20. Handoff requirements

<Anything beyond the standard `CLAUDE_HANDOFF.md` protocol.>
