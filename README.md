# Ops mutation audit

Append-only receipts for ops requests that CHANGED production —
real-money capability, the risk envelope around it, and the Experiment OS
write transports (`ops_meta.AUDIT_WORTHY_VARS`).

Each `receipts/<UTC-timestamp>-<request-id>.json` file is one receipt,
committed automatically by the Ops Runner workflow: what was asked, by whom,
against which code, what the readback said, and how it ended.

Receipts only — request payloads are not copied here. This branch is never
merged into the default branch and never deployed.
