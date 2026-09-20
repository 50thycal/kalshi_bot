"""Bounded external-session research runner; never starts or funds a desk.

The command is trusted local code, not an OS sandbox. It receives research JSON
on stdin and returns one ResearchOutput JSON on stdout. Authentication for that
command must be provisioned explicitly in its dedicated research home; the runner
never copies credentials or falls back to a paid model API.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .research import ResearchOutput, parse_output

MAX_INPUT = 512_000
MAX_STDOUT = 64_000
MAX_STDERR = 16_000


class RunnerError(Exception):
    """Only static diagnostic codes, never provider text or credentials."""


@dataclass
class RunnerConfig:
    service_url: str
    token: str = field(repr=False)
    desk: str
    worker_id: str
    model_id: str
    command: list[str]
    state_dir: Path
    research_home: Path | None = None
    command_timeout: int = 300

    def __post_init__(self):
        self.service_url = self.service_url.rstrip("/")
        url = urlsplit(self.service_url)
        if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"})) or not url.hostname:
            raise RunnerError("service_url_requires_https")
        if url.username or url.password or url.query or url.fragment or not self.token:
            raise RunnerError("invalid_service_credentials")
        if self.desk not in {"chatgpt", "claude"} or not 1 <= len(self.worker_id) <= 100 or not 1 <= len(self.model_id) <= 200:
            raise RunnerError("invalid_runner_identity")
        if not isinstance(self.command, list) or not 1 <= len(self.command) <= 64 or any(
            not isinstance(arg, str) or not arg or len(arg) > 4096 or "\0" in arg for arg in self.command
        ) or not Path(self.command[0]).is_absolute():
            raise RunnerError("command_requires_json_argv_absolute_executable")
        if not 10 <= self.command_timeout <= 600:
            raise RunnerError("command_timeout_out_of_bounds")
        self.state_dir = Path(self.state_dir).absolute()
        self.research_home = Path(self.research_home).absolute() if self.research_home else None


def bounded_command(argv, payload, *, home: Path, timeout: int) -> str:
    """Non-shell process group with bounded pipes and a wall-clock deadline."""
    data = json.dumps(payload, ensure_ascii=False).encode()
    if len(data) > MAX_INPUT:
        raise RunnerError("research_input_too_large")
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    environment.update({"HOME": str(home), "TMPDIR": str(home / "tmp")})
    (home / "tmp").mkdir(mode=0o700, exist_ok=True)
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=home, env=environment, shell=False, start_new_session=True)
    output, errors, sent = bytearray(), 0, 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (child.stdin, child.stdout, child.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_WRITE if stream is child.stdin else selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RunnerError("research_command_timeout")
                for key, _ in selector.select(min(remaining, 0.1)):
                    stream = key.fileobj
                    if stream is child.stdin:
                        try:
                            sent += os.write(stream.fileno(), data[sent:sent + 8192])
                        except BrokenPipeError:
                            sent = len(data)
                        if sent == len(data):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), 8192)
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                        elif stream is child.stdout:
                            output.extend(chunk)
                            if len(output) > MAX_STDOUT:
                                raise RunnerError("research_stdout_limit")
                        else:
                            errors += len(chunk)
                            if errors > MAX_STDERR:
                                raise RunnerError("research_stderr_limit")
            if child.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                raise RunnerError("research_command_failed")
        return output.decode("utf-8", errors="strict")
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        raise RunnerError("research_command_failed") from None
    finally:
        # Kill the entire process group even if its leader exited but a grandchild
        # inherited pipes. No timed-out research survives into the next cycle.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream and not stream.closed:
                stream.close()


class SessionRunner:
    def __init__(self, config: RunnerConfig, *, transport=None, command_runner=bounded_command, clock=None):
        self.config, self.command_runner = config, command_runner
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        directory = config.state_dir
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            raise RunnerError("state_directory_not_private")
        directory.chmod(0o700)
        self.home = config.research_home or directory / "research-home"
        if config.research_home is None:
            self.home.mkdir(mode=0o700, exist_ok=True)
        if not self.home.is_dir() or self.home.is_symlink():
            raise RunnerError("research_home_must_exist")
        self.path = directory / "pending.json"
        self.identity = hashlib.sha256(json.dumps([config.service_url, config.desk, config.worker_id,
            config.model_id, config.command, str(self.home)], sort_keys=True).encode()).hexdigest()
        self.client = httpx.Client(timeout=httpx.Timeout(90, connect=10), follow_redirects=False,
                                   trust_env=False, transport=transport)

    def close(self):
        self.client.close()

    @contextmanager
    def _lock(self):
        fd = os.open(self.config.state_dir / "runner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RunnerError("runner_already_active") from None
            yield
        finally:
            os.close(fd)

    def _save(self, state):
        raw = json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
        if len(raw) > MAX_INPUT * 3:
            raise RunnerError("runner_state_limit")
        temp = self.config.state_dir / "pending.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
        fd = os.open(self.config.state_dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _load(self):
        if not self.path.exists():
            return None
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as handle:
            raw = handle.read(MAX_INPUT * 3 + 1)
        if len(raw) > MAX_INPUT * 3:
            raise RunnerError("runner_state_limit")
        state = json.loads(raw)
        if state.get("identity") != self.identity:
            raise RunnerError("runner_state_identity_mismatch")
        return state

    def _post(self, action, body):
        url = f"{self.config.service_url}/api/desks/{self.config.desk}/{action}"
        with self.client.stream("POST", url, json=body,
                                headers={"Authorization": "Bearer " + self.config.token, "Accept-Encoding": "identity"}) as response:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise RunnerError("compressed_service_response_refused")
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                data.extend(chunk)
                if len(data) > MAX_INPUT:
                    raise RunnerError("service_response_limit")
            if response.status_code != 200:
                if response.status_code in (401, 403):
                    raise RunnerError("service_authentication_failed")
                if response.status_code in (400, 409, 413):
                    raise RunnerError("service_rejected_request")
                raise RunnerError("service_request_unknown")
            return json.loads(data)

    def _lease_valid(self, state, needed=0):
        lease = datetime.fromisoformat(state["claim"]["lease_until"].replace("Z", "+00:00"))
        if lease.tzinfo is None or lease <= self.clock() + timedelta(seconds=needed + 15):
            raise RunnerError("research_claim_expired_or_insufficient_time")

    def _invoke(self, state, phase):
        self._lease_valid(state, self.config.command_timeout)
        state["phase"] = "invoking_" + phase
        self._save(state)
        context = dict(state["claim"]["context"])
        if phase == "final":
            context.update({"now": self.clock().isoformat(), "requested_sources": state["sources"]})
        prompt = {"protocol_version": 1, "desk_id": self.config.desk, "model_id": self.config.model_id,
                  "system": state["claim"]["system"] + " Return only ResearchOutput JSON; author_model must match model_id and origin must be session. "
                            + ("No more source_requests allowed." if phase == "final" else "One bounded source followup is available."),
                  "context": context, "output_schema": ResearchOutput.model_json_schema(), "phase": phase}
        raw = self.command_runner(self.config.command, prompt, home=self.home, timeout=self.config.command_timeout)
        state.update({"phase": "captured_" + phase, "raw": raw})
        self._save(state)  # Never regenerate a captured output, even when invalid.

    def _output(self, state):
        output = parse_output(state["raw"])
        for pick in output.decisions:
            if (pick.decision.desk_id != self.config.desk or
                pick.decision.round_id != state["claim"]["context"]["round_id"] or
                pick.decision.author_model != self.config.model_id or pick.decision.origin != "session"):
                raise RunnerError("research_result_identity_mismatch")
        return output

    def once(self):
        with self._lock():
            state = self._load()
            if state and state["phase"] == "completed":
                state = None
            if state and state["phase"] in {"claiming", "claim_unknown"}:
                if self.clock() < datetime.fromisoformat(state["retry_after"]):
                    return {"status": "recovering", "reason": "claim_status_unknown_waiting_for_lease"}
                state = None  # Claim-only uncertainty cannot have invoked a model.
            if state is None:
                state = {"identity": self.identity, "phase": "claiming",
                         "retry_after": (self.clock() + timedelta(minutes=31)).isoformat()}
                self._save(state)
                try:
                    claim = self._post("claim", {"worker_id": self.config.worker_id})
                except Exception as exc:
                    if isinstance(exc, RunnerError) and str(exc) in {"service_authentication_failed", "service_rejected_request"}:
                        state["phase"] = "completed"
                        self._save(state)
                        return {"status": "needs_operator", "reason": str(exc)}
                    state["phase"] = "claim_unknown"
                    self._save(state)
                    return {"status": "recovering", "reason": "claim_status_unknown_waiting_for_lease"}
                if claim is None or claim == {"ok": True}:
                    state["phase"] = "completed"
                    self._save(state)
                    return {"status": "idle"}
                if (not isinstance(claim, dict) or claim.get("desk_id") != self.config.desk or
                    claim.get("context", {}).get("desk_id") != self.config.desk or
                    not claim.get("context", {}).get("round_id") or not claim.get("job_id") or
                    not claim.get("claim_token") or not claim.get("lease_until") or not claim.get("system")):
                    raise RunnerError("invalid_service_claim")
                state.update({"phase": "claimed", "claim": claim})
                self._save(state)
            if state["phase"].startswith("invoking_"):
                return {"status": "needs_operator", "reason": "research_command_outcome_unknown"}
            if state["phase"] == "blocked":
                return {"status": "needs_operator", "reason": state["reason"]}
            try:
                if state["phase"] == "claimed":
                    self._invoke(state, "research")
                if state["phase"] == "captured_research":
                    output = self._output(state)
                    state.update({"requests": output.source_requests, "sources": [], "source_index": 0,
                                  "phase": "fetching_sources" if output.source_requests else "captured_final"})
                    self._save(state)
                if state["phase"] == "fetching_sources":
                    for i in range(state["source_index"], len(state["requests"])):
                        self._lease_valid(state, self.config.command_timeout + 90)
                        url = state["requests"][i]
                        # Persist intent before GET proxy request. A restart treats
                        # an uncertain fetch as unavailable instead of consuming
                        # the server's source allowance twice.
                        state.update({"source_index": i + 1})
                        state["sources"].append({"url": url, "error": "source_request_unknown"})
                        self._save(state)
                        try:
                            state["sources"][-1] = self._post("source", {
                                "job_id": state["claim"]["job_id"], "claim_token": state["claim"]["claim_token"], "url": url})
                        except Exception:
                            pass
                        self._save(state)
                    self._invoke(state, "final")
                if state["phase"] == "captured_final":
                    output = self._output(state)
                    if output.source_requests:
                        raise RunnerError("research_followup_limit")
                    state.update({"phase": "ready", "payload": output.model_dump(mode="json")})
                    self._save(state)
                if state["phase"] == "ready":
                    # Replay even after expiry: the first completion may already
                    # have committed. Server checks exact token/model/payload.
                    result = self._post("complete", {"job_id": state["claim"]["job_id"],
                        "claim_token": state["claim"]["claim_token"], "model_id": self.config.model_id,
                        "payload": state["payload"]})
                    if isinstance(result, dict) and result.get("state") == "publishing" and result.get("job_id") == state["claim"]["job_id"]:
                        return {"status": "recovering", "reason": "completion_publishing"}
                    if not isinstance(result, dict) or result.get("state") != "completed" or result.get("job_id") != state["claim"]["job_id"]:
                        raise RunnerError("completion_status_unknown")
                    state["phase"] = "completed"
                    self._save(state)
                    return {"status": "completed", "job_id": result["job_id"]}
            except Exception as exc:
                reason = str(exc) if isinstance(exc, RunnerError) else "research_worker_failed"
                if state["phase"] == "ready":
                    if reason in {"service_rejected_request", "service_authentication_failed"}:
                        return {"status": "needs_operator", "reason": reason}
                    return {"status": "recovering", "reason": "completion_status_unknown"}
                state.update({"phase": "blocked", "reason": reason})
                self._save(state)
                return {"status": "needs_operator", "reason": reason}
            raise RunnerError("invalid_runner_state")


def _stop_worker(signum, _frame):
    # BaseException unwinds active subprocess finally blocks and preserves the
    # durable invoking marker; restart cannot unknowingly repeat the model call.
    raise SystemExit(128 + signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("once", "serve"))
    parser.add_argument("--desk", required=True, choices=("chatgpt", "claude"))
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--research-home", type=Path)
    parser.add_argument("--command-json", default=os.environ.get("DESK_RESEARCH_COMMAND_JSON", ""))
    parser.add_argument("--command-timeout", type=int, default=300)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    runner = None
    previous_sigterm = signal.signal(signal.SIGTERM, _stop_worker)
    try:
        if not 5 <= args.poll_seconds <= 3600:
            raise RunnerError("poll_interval_out_of_bounds")
        config = RunnerConfig(os.environ.get("DESK_SERVICE_URL", ""), os.environ.get("DESK_SESSION_TOKEN", ""),
            args.desk, args.worker_id, args.model_id, json.loads(args.command_json), args.state_dir,
            args.research_home, args.command_timeout)
        runner = SessionRunner(config)
        while True:
            result = runner.once()
            print(json.dumps(result), flush=True)
            if args.mode == "once" or result["status"] == "needs_operator":
                return 2 if result["status"] == "needs_operator" else 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"status": "needs_operator", "reason": str(exc) if isinstance(exc, RunnerError) else "runner_configuration_or_state_failure"}))
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if runner:
            runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
