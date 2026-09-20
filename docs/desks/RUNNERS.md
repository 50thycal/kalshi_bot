# Running the two research sessions without babysitting

**Current operator choice:** app-driven `Go / Continue`, documented in
[APP_SESSIONS.md](APP_SESSIONS.md). This file describes the deferred hosted option.
Do not start model login or enable runners merely to use app-session mode.


The desk service owns the trading rules, schedules, database, execution, and settlement.
Two separate runner processes supply the research: one authenticated as `chatgpt`, the
other as `claude`. They claim due jobs, run an existing model client, capture requested
sources through the service, and return validated research. A dashboard Continue is an
optional research request; healthy scheduled operation does not require repeated clicks.

The runner is implemented in `kalshi_bot/desks/runner.py`. The model-client adapters live
in `kalshi_bot/desks/cli_adapter.py`. These components do not install a client, create a
model account, authenticate a user, deploy a host, fund a trading account, mark either
session ready, or start a round. Existing model access and an always-running host remain
real setup requirements. No live model calls or hosted deployment are implied by local
regression tests.

## What belongs on which process

| Process | Required access | Must not receive |
|---|---|---|
| Desk service | Desk database, both restricted exchange keys, operator/desk tokens | Existing worker or Evo credentials |
| ChatGPT runner | Service URL, ChatGPT desk token, private state, approved Codex client access | Operator token, Claude token, exchange/database credentials |
| Claude runner | Service URL, Claude desk token, private state, approved Claude client access | Operator token, ChatGPT token, exchange/database credentials |
| Model child | Job input and explicitly selected dedicated research home | Runner token or inherited service/database/exchange/API-key environment |

The runner's child environment is deliberately minimal. Use `--research-home` only for
an existing dedicated model-client home that contains the intended authentication and
configuration. Do not point it at a shared production home. Keep auth homes, runner state,
and token files outside the repository with private permissions. Environment filtering
is not an operating-system sandbox; host each desk as a separate unprivileged service
account or isolated container with access only to its own files and model configuration.

Use the repository's existing Python environment and dependencies. Start the runner module
from the repository checkout. The child command must use absolute executable/script paths
because its working directory is isolated; do not depend on the repository appearing on
an inherited `PYTHONPATH`. The examples below assume `/opt/kalshi_bot` is the checkout and
`/opt/desk-venv/bin/python` is its Python environment. Replace those paths with the real
installation. The example model IDs are placeholders, not current recommendations.

## Connect and inspect before running research

Provide these through each service's private environment or secret manager:

```text
DESK_SERVICE_URL=https://your-private-desk-service.example
DESK_SESSION_TOKEN=<that-desk-token-only>
```

They are client variables, distinct from the server's `DESKS_*` configuration. HTTPS is
required except for loopback development. Do not embed tokens in URLs or command arguments.
Run the read-only doctor from the checkout:

```bash
python -m kalshi_bot.desks.doctor
python -m kalshi_bot.desks.doctor --json
```

Doctor only fetches authenticated status. It neither sends a model request nor renews an
exchange-isolation check, tests alerts, flips readiness, starts a round, or fixes a gate.
Exit `0` means fresh status reports ready/healthy; `2` means reachable but blocked,
incomplete, or stale; `1` means invalid configuration or a failed read. Output is a
redacted diagnostic, not a dump of trading records or credentials. A research-only service
can correctly report trading disabled while researchers continue their preparation.

## Foreground setup: one process per desk

Once the appropriate client is already installed and authenticated under each dedicated
research home, run a single research cycle with `once`. After checking the resulting desk
publication and status, use `serve` for normal unattended operation. Model calls can consume
existing plan usage or incur provider charges depending on the configured client; this
build grants no new paid allowance. Verify the active authentication method and entitlement
before the first actual model call. The adapters strip inherited API-key variables and
never install credentials, but an existing client's saved login can still select API-key
billing or subscription extra usage. Environment filtering does not verify billing; the
operator must verify saved authentication mode, entitlement, and extra-usage settings
before the first cycle. No new paid allowance is inferred from an installed client.
Under the exact dedicated service identity, the clients' read-only authentication checks
are `codex login status` and `claude auth status` (Claude emits JSON by default, with
`--text` available). A successful exit only means the client recognizes a login; it does
not establish subscription billing, remaining entitlement, or whether extra usage charges
are enabled. Do not copy raw status output into public logs because it can include account
metadata. These commands were documented, not run against the operator's account by this
build. [Codex command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
[Claude command reference](https://code.claude.com/docs/en/cli-reference).

Start the ChatGPT process in its own environment:

```bash
python -m kalshi_bot.desks.runner once \
  --desk chatgpt \
  --worker-id chatgpt-runner-1 \
  --model-id REPLACE_WITH_APPROVED_CODEX_MODEL \
  --state-dir /var/lib/desk-chatgpt/runner \
  --research-home /var/lib/desk-chatgpt/model-home \
  --command-json '["/opt/desk-venv/bin/python","/opt/kalshi_bot/kalshi_bot/desks/cli_adapter.py","codex"]'
```

Start the Claude process with its separate token and state:

```bash
python -m kalshi_bot.desks.runner once \
  --desk claude \
  --worker-id claude-runner-1 \
  --model-id REPLACE_WITH_APPROVED_CLAUDE_MODEL \
  --state-dir /var/lib/desk-claude/runner \
  --research-home /var/lib/desk-claude/model-home \
  --command-json '["/opt/desk-venv/bin/python","/opt/kalshi_bot/kalshi_bot/desks/cli_adapter.py","claude"]'
```

Replace `once` with `serve` to poll continuously. The service still decides when research
is due; polling is not an instruction to generate another paid response on every tick.
`--poll-seconds` defaults to 60 (allowed 5–3600), and `--command-timeout` defaults to
300 seconds (allowed 10–600). `once` exits `0` for idle/completed/recovering and `2` when
operator attention is required. `serve` stops on needs-operator instead of repeatedly
calling a failing model. `DESK_RESEARCH_COMMAND_JSON` can hold the same JSON argument
array instead of passing `--command-json`. The bundled model-client adapters additionally
cap each CLI call at 120 seconds; raising the runner deadline does not remove that cap.
It is an argument vector, not a shell command: no pipes, substitutions,
or shell quoting inside it. The model ID recorded in the ledger must match the actual
client invocation. This records the explicitly requested model identifier, not an attestation
of a provider-resolved model version. Keep worker IDs stable across ordinary restarts and state directories
unique per desk/service identity.

## Supervised service example

Use an existing host/process supervisor with two separate services. This systemd template
illustrates the split; it is a deployment recipe, not an installed or enabled unit. Create
unprivileged `desk-chatgpt` and `desk-claude` OS users and their private directories through
your usual host provisioning. Give them read access to the code and their own model login,
not to the desk service's environment. The service manager should read the private
`/etc/desk-runners/chatgpt.env` and `claude.env` files.

```ini
[Unit]
Description=Research desk runner %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=desk-%i
Group=desk-%i
WorkingDirectory=/opt/kalshi_bot
EnvironmentFile=/etc/desk-runners/%i.env
ExecStart=/opt/desk-venv/bin/python -m kalshi_bot.desks.runner serve --desk %i --worker-id %i-runner-1 --model-id ${DESK_MODEL_ID} --state-dir /var/lib/desk-%i/runner --research-home /var/lib/desk-%i/model-home
Restart=on-failure
RestartPreventExitStatus=2
RestartSec=30
KillMode=control-group
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Each private environment file supplies the URL, own-desk token, `DESK_MODEL_ID`, and
`DESK_RESEARCH_COMMAND_JSON`. Its latter value is the corresponding absolute adapter
argument array above. Do not copy the server `.env` into these files. The two instances
are `desk-runner@chatgpt` and `desk-runner@claude` if the template is named
`desk-runner@.service`. Model auth stored in an OS keychain may not be available to a
background service account; verify supported access rather than copying or exporting
credentials opportunistically.

A scheduler may invoke `once` instead of maintaining `serve`, using the same dedicated
identity and state directory. Avoid overlapping invocations and preserve the state volume
across runs. A scheduled job's process finishing does not delete the desk's durable state.
The standard service route is preferable when prompt recovery and regular polling matter.
Keep process-group cleanup enabled so stopping the service also stops model descendants;
a hard kill can still leave the last call's outcome uncertain. Neither route requires
reopening the original human chat window.

## Child protocol for custom research commands

The runner starts the configured command with one JSON object on standard input:

```json
{
  "protocol_version": 1,
  "desk_id": "chatgpt",
  "model_id": "configured-model-id",
  "phase": "research",
  "system": "Desk charter supplied by the service",
  "context": {},
  "output_schema": {}
}
```

The real `context` contains the claimed job's current evidence, desk state, and research
history; `output_schema` is the actual `ResearchOutput` JSON schema. The child emits
exactly one conforming JSON object on stdout. Progress belongs on stderr; error envelopes,
Markdown, JSONL event streams, or explanations outside the object are invalid outputs.
A custom command must not claim the role or model of another desk.

The first output may request at most eight source URLs using `source_requests`. The
runner asks the service to capture them, then makes at most one final model call with
`phase="final"` and the captured evidence added to context. Final output must have no
remaining source requests. Sources must come through the authenticated source endpoint;
the model cannot invent source IDs, timestamps, hashes, market rules, or external facts.
Both desks use the same schema and acquisition path. A rejected source is a research
limitation to record, not permission to bypass server allowlists.

The model does not receive exchange credentials or call the exchange executor directly.
The runner validates and sends the completion; the desk service validates provenance and
applies all existing trading caps. `once` and `serve` never mark a session ready or start
the common round. Optional paper observations remain distinct from live decisions.

## Restart and recovery

The private `pending.json` retains the claim, captured output, and exact completion
payload so a restart can retry completion
without repeating the model call. An uncertain server response is not permission to
manufacture a new job/decision. Preserve the claim, source captures, and output until the
service confirms the result or a reconciliation decision resolves it.

If a process dies during a model call, its outcome/usage may be unknown. The runner does
not automatically rerun that model call. Stop the affected runner and inspect its private
state alongside authenticated service status; reconcile whether work was accepted or
charged before deliberately recovering. There is no automatic reset flag. Only after the job/provider outcome and any lease
expiry have been resolved should the operator deliberately archive pending evidence.
Do not delete its state directory to make a red indicator disappear. Ordinary connection failures are retried without surrendering the
saved result, while persistent auth/configuration/model failures need intervention.

A live runner process is not proof of a completed research cycle. Check completed jobs,
publication timestamps, learning backlog, and readiness in the service. Before common
live start, both fresh desk sessions still complete their startup packets, independently
mark their own readiness, and the operator deliberately starts the round after all gates
pass. The runner does not fund accounts or send the alert-channel test.

## Standard-client behavior and source references

The adapters translate the protocol into existing CLI requests, not direct paid API calls.
Their provider-facing schema is normalized for supported structured-output subsets: Codex
requires all object keys and omits schema defaults; Claude omits unsupported numeric/length
constraints and lookaround patterns from its wire schema. The complete original contract
remains in the prompt as `required_output_contract`, and returned data must still pass
unchanged local `ResearchOutput` validation and the service's evidence/trading checks.
Wire compatibility therefore does not relax accepted spend, evidence, timestamp, or size
constraints. Incompatible client flags, schema handling, or malformed output fail the
cycle; no permissive fallback is used. Compatibility with an installed client still needs
verification before unattended launch. Provider schema limits are documented in the
[OpenAI structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [Claude structured-output guide](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

Codex supports stdin prompts, a JSON-schema file, and a final-message file; its `--json`
mode is an event stream rather than the desired result. The adapter must extract the final
structured response. [Official Codex non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Codex can reuse saved authentication, but authentication type determines workspace access
and billing. Official guidance recommends API-key authentication for programmatic CI;
we do not infer unattended subscription eligibility from a ChatGPT login or promise free
usage. [Official Codex authentication documentation](https://learn.chatgpt.com/docs/auth).

Claude's print mode supports schema-constrained JSON in the `structured_output` field of
its response envelope. The adapter validates that field and does not forward the envelope
as research. [Official Claude programmatic usage](https://code.claude.com/docs/en/headless).

Tool disabling must cover both built-in tools and MCP; those are separate CLI controls.
The adapter uses the model requested by the runner and avoids conversational resume so
one job cannot accidentally continue unrelated work. [Official Claude CLI reference](https://code.claude.com/docs/en/cli-reference).

Claude saved login and API credentials have an authentication precedence. Confirm the
selected identity and billing method under the exact service account before scheduling;
a browser subscription alone is not proof this host is authenticated.
[Official Claude authentication documentation](https://code.claude.com/docs/en/authentication).
