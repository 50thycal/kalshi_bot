# Production telemetry archive — preparation only

This describes an export format and review gates. It does **not** authorize a
production export, retention job, deletion, table rewrite, or bot configuration
change. The production source is the `kalshi-bot` Postgres service
`f338101d-7fb2-44e2-890c-9258112616d5`. Never point these steps at the
separate `kalshi-desks` database.

## Scope and evidence

At 2026-10-02 02:10 UTC, the 100 GB volume used about 25.94 GB. [Read-only
workflow #17](https://github.com/50thycal/kalshi_bot/actions/runs/36954343502)
measured `incentive_book_events` at 5,907 MiB, `execution_book_events` at
3,026 MiB, and `incentive_market_snapshots` at 2,142 MiB. Compared with
[Sep 30 workflow #12](https://github.com/50thycal/kalshi_bot/actions/runs/36719012145),
these grew about 584, 161, and 137 MiB/day respectively. These are the initial
**export-only** allowlist in `scripts/archive_telemetry.py`.

The approximately 14-day-old raw tapes have no meaningful data beyond a
30-day retention cutoff yet. The incentive book tape supports fill-model replay;
the shadow outcome/quote tables support Experiment OS metrics and are not in
the allowlist. Orders, fills, positions, balances, experiment records, and the
shared market-ownership register are excluded. Snapshot reader dependencies
must be checked again before any future removal.

## One-day export and integrity checks

The script requires `DATABASE_URL_RO` alone; it never falls back to a writer
credential. Run it in an isolated job with bounded CPU/network use and enough
temporary storage **outside the Postgres volume**, after a fresh backup. It
exports one completed UTC day per invocation through a read-only repeatable-read
transaction. For example, after approving that exact production day and table:

```bash
python scripts/archive_telemetry.py export \
  --table incentive_book_events --day 2026-09-17 \
  --output-dir /secure/telemetry-archive
python scripts/archive_telemetry.py verify \
  /secure/telemetry-archive/incentive_book_events_2026-09-17.csv.gz \
  /secure/telemetry-archive/incentive_book_events_2026-09-17.manifest.json
```

The output is PostgreSQL `COPY` CSV compressed with gzip plus a manifest with
the exact snapshot row count, column names/types, id bounds, SHA-256, and UTC
interval. Files are created with mode 0600 and never overwritten. Keep the
manifest and data together. The script has no database deletion action.
After a separate approval for the specific production export, the `upload`
subcommand can place the verified pair in the private
`kalshi-bot-research-archive` Railway bucket and read both objects back to
check their SHA-256 hashes. Install `requirements-archive.txt` only in the
isolated archive job. Use Railway bucket variable references for endpoint,
S3 bucket name (`BUCKET`, not the display name), key id, secret, and region,
mapped to `ARCHIVE_S3_ENDPOINT`, `ARCHIVE_S3_BUCKET`,
`ARCHIVE_S3_ACCESS_KEY_ID`, `ARCHIVE_S3_SECRET_ACCESS_KEY`, and
`ARCHIVE_S3_REGION`. Set `ARCHIVE_S3_ADDRESSING_STYLE` only if the bucket's
Credentials tab requires `path` rather than the default `virtual`. Never put
these values in logs or repository files. This credentials grant and upload
must be explicitly approved; neither is performed by this PR.

```bash
python scripts/archive_telemetry.py upload \
  /secure/telemetry-archive/incentive_book_events_2026-09-17.csv.gz \
  /secure/telemetry-archive/incentive_book_events_2026-09-17.manifest.json
```

The manifest key is uploaded last. Repeating an upload checks existing object
bytes and refuses a conflicting object. Full CSV count/header verification is
performed on the local file before upload; the bucket readback checks every
remote byte against the local checksum.

Before any removal, restore **the same archived day** to an isolated Postgres
database with the matching migrated schema using `COPY FROM STDIN` and compare
counts, id range, and selected rows, including JSON and quoted/newline fields.
Verify a replay consumer can read its time range from the archive. A successful
Railway volume backup is not evidence that this export can be restored.

## Retention and disk reclamation remain separate decisions

After archive readback and restore evidence, propose explicit table/date batches
and recheck replay windows, active readers, unresolved outcomes, dependencies,
and disk headroom. Obtain operator approval for the exact deletion or partition
maintenance operation. Small batched `DELETE` can make pages reusable inside
Postgres but may not lower Railway's physical volume usage; it also generates
WAL. A table rewrite such as `VACUUM FULL` needs substantial spare volume space
and stronger locks. A later partition design can remove whole expired time
ranges more predictably, but requires a separate migration and live-reader
review. Do not infer recoverable bytes from old-row percentages alone.

Aim to keep physical usage under 70 GB of 100 GB, alert at 60/70/80 GB, and
re-evaluate growth and backup health daily until bounded retention is proven.
No alert/configuration change is made by this runbook.
