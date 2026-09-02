# MARKTANGLE-2 research package

The run outputs land here, split from the ops result by
`python scripts/marktangle2_package.py ops/results/<id>.txt docs/marktangle2`:

- `MARKTANGLE_2_DATA_REPORT.md` — universe, funnel, classes, families, price/spot coverage, exclusions
- `MARKTANGLE_2_TRACK_A.md` — cross-family conditional reversion: streak tables, model effects, economics, verdicts
- `MARKTANGLE_2_TRACK_B.md` — crypto threshold persistence: duration and distance tables, model vs market, economics, verdicts
- `MARKTANGLE_2_SUMMARY.md` — track verdicts, surviving arms, statistical-only arms, next gate, fingerprints
- `MARKTANGLE_2_TRADES.csv` — one row per simulated trade (treatment and mirror), enough to reproduce every number

The preregistration is `docs/MARKTANGLE_2_SPEC.md`. **No run has been recorded yet.** The
splitter refuses a package whose trades CSV does not match the fingerprint the run printed.
