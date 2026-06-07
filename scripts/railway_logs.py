"""Fetch Railway deployment logs via the public GraphQL API.

Built to run inside the `Railway Logs` GitHub Actions workflow. GitHub runners
have open egress, so they can reach Railway's API (`backboard.railway.com`)
even though the Claude environment cannot. Stdlib only — no pip install needed.

Auth: a Railway token in `RAILWAY_TOKEN`, sent as `Authorization: Bearer`.
A **Workspace** (team) or **Account** token is the most reliable against the
GraphQL v2 endpoint; Project tokens have historically been finicky there.

Locating the deployment:
  - If `RAILWAY_DEPLOYMENT_ID` is set, its logs are fetched directly.
  - Otherwise the latest deployment is looked up from
    `RAILWAY_PROJECT_ID` + `RAILWAY_ENVIRONMENT_ID` (+ optional
    `RAILWAY_SERVICE_ID`).

Escape hatch: set `RAILWAY_RAW_QUERY` (and optional JSON `RAILWAY_RAW_VARS`)
to run an arbitrary GraphQL query — handy if Railway's schema drifts and the
canned queries below need adjusting (introspection works from the runner).

Env:
    RAILWAY_TOKEN            (required)
    RAILWAY_PROJECT_ID       (required unless RAILWAY_DEPLOYMENT_ID is set)
    RAILWAY_ENVIRONMENT_ID   (required unless RAILWAY_DEPLOYMENT_ID is set)
    RAILWAY_SERVICE_ID       (optional; narrows to one service)
    RAILWAY_DEPLOYMENT_ID    (optional; skips the lookup)
    LOG_LIMIT                (optional; default 200)
    LOG_FILTER               (optional; Railway log filter string)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://backboard.railway.com/graphql/v2"

LATEST_DEPLOYMENT_QUERY = """
query LatestDeployment($projectId: String!, $environmentId: String!, $serviceId: String) {
  deployments(
    first: 1
    input: { projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId }
  ) {
    edges { node { id status createdAt staticUrl } }
  }
}
"""

DEPLOYMENT_LOGS_QUERY = """
query DeploymentLogs($deploymentId: String!, $limit: Int!, $filter: String) {
  deploymentLogs(deploymentId: $deploymentId, limit: $limit, filter: $filter) {
    timestamp
    message
    severity
  }
}
"""


def _gql(query: str, variables: dict, token: str) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"Railway API HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach Railway API: {e.reason}") from None
    if body.get("errors"):
        raise SystemExit("Railway GraphQL errors:\n" + json.dumps(body["errors"], indent=2))
    return body.get("data") or {}


def _resolve_deployment_id(token: str) -> str:
    dep = os.environ.get("RAILWAY_DEPLOYMENT_ID", "").strip()
    if dep:
        return dep
    project = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
    env_id = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
    if not (project and env_id):
        raise SystemExit(
            "Set RAILWAY_DEPLOYMENT_ID, or both RAILWAY_PROJECT_ID and "
            "RAILWAY_ENVIRONMENT_ID, to locate a deployment."
        )
    variables = {
        "projectId": project,
        "environmentId": env_id,
        "serviceId": os.environ.get("RAILWAY_SERVICE_ID", "").strip() or None,
    }
    data = _gql(LATEST_DEPLOYMENT_QUERY, variables, token)
    edges = (data.get("deployments") or {}).get("edges") or []
    if not edges:
        raise SystemExit("No deployments found for that project/environment/service.")
    node = edges[0]["node"]
    print(f"# latest deployment {node['id']} (status={node.get('status')}, "
          f"created={node.get('createdAt')})", file=sys.stderr)
    return node["id"]


def main() -> int:
    token = os.environ.get("RAILWAY_TOKEN", "").strip()
    if not token:
        print("RAILWAY_TOKEN is not set.", file=sys.stderr)
        return 1

    raw_query = os.environ.get("RAILWAY_RAW_QUERY", "").strip()
    if raw_query:
        raw_vars = json.loads(os.environ.get("RAILWAY_RAW_VARS", "{}") or "{}")
        print(json.dumps(_gql(raw_query, raw_vars, token), indent=2))
        return 0

    try:
        limit = int(os.environ.get("LOG_LIMIT", "200"))
    except ValueError:
        limit = 200

    deployment_id = _resolve_deployment_id(token)
    variables = {
        "deploymentId": deployment_id,
        "limit": limit,
        "filter": os.environ.get("LOG_FILTER", "").strip() or None,
    }
    data = _gql(DEPLOYMENT_LOGS_QUERY, variables, token)
    logs = data.get("deploymentLogs") or []
    if not logs:
        print("(no log lines returned)")
        return 0
    for entry in logs:
        ts = entry.get("timestamp", "")
        sev = (entry.get("severity") or "").upper()
        msg = entry.get("message", "")
        prefix = f"{ts} {sev}".strip()
        print(f"{prefix}  {msg}" if prefix else msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
