"""Fake local executables only: no saved credentials or paid provider calls."""
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kalshi_bot.desks import cli_adapter
from kalshi_bot.desks.contracts import DeskError
from kalshi_bot.desks.research import ResearchOutput

OUTPUT = {"summary": "No verified edge found", "next_action": "Review fresh evidence later"}


def envelope(provider="codex", **overrides):
    value = {"protocol_version": 1, "desk_id": "chatgpt" if provider == "codex" else "claude",
             "model_id": "test-model", "system": "Research only the supplied evidence.",
             "context": {"sources": []}, "output_schema": ResearchOutput.model_json_schema(),
             "phase": "research"}
    value.update(overrides)
    return json.dumps(value).encode()


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    home = tmp_path / "authentication-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(binary))
    def install(provider, code):
        path = binary / provider
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path
    return install, home


def codex_script(output=OUTPUT):
    return '''import json, os, pathlib, sys
args = sys.argv[1:]
assert args[:2] == ['exec', '-']
assert '--ignore-user-config' in args and '--ephemeral' in args
assert args[args.index('--sandbox')+1] == 'read-only'
assert 'features.shell_tool=false' in args and 'features.unified_exec=false' in args
assert os.environ['CODEX_HOME'] == os.environ['HOME'] + '/.codex'
assert not any(k in os.environ for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','DATABASE_URL','DESKS_OPERATOR_TOKEN'))
assert pathlib.Path.cwd() != pathlib.Path(os.environ['HOME'])
assert not (pathlib.Path.cwd()/'CLAUDE.md').exists()
prompt = sys.stdin.read()
assert 'Research only the supplied evidence.' in prompt
assert '"desk_id": "chatgpt"' in prompt and '"model_id": "test-model"' in prompt
wire = json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
assert set(wire['required']) == set(wire['properties'])
for definition in wire['$defs'].values():
    if definition.get('type') == 'object':
        assert set(definition['required']) == set(definition['properties'])
print('private provider progress that must not escape')
pathlib.Path(args[args.index('-o')+1]).write_text(''' + repr(json.dumps(output)) + ''')
'''


def test_codex_uses_final_file_and_strips_secret_environment(fake_cli, monkeypatch, capsys):
    install, home = fake_cli
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL", "DESKS_OPERATOR_TOKEN"):
        monkeypatch.setenv(name, "DO-NOT-INHERIT")
    install("codex", codex_script())
    result = cli_adapter.run("codex", envelope())
    assert result.summary == OUTPUT["summary"]
    assert capsys.readouterr().out == ""


def test_claude_unwraps_structured_output_and_has_no_tools(fake_cli):
    install, home = fake_cli
    install("claude", '''import json, sys
args=sys.argv[1:]
assert args[args.index('--tools')+1] == ''
assert args[args.index('--disallowedTools')+1] == 'mcp__*'
assert args[args.index('--mcp-config')+1] == '{"mcpServers":{}}'
assert '--strict-mcp-config' in args and '--no-session-persistence' in args
assert args[args.index('--setting-sources')+1] == 'project'
assert json.loads(args[args.index('--json-schema')+1])['additionalProperties'] is False
sys.stdin.read()
print(json.dumps({'type':'result','subtype':'success','is_error':False,'structured_output':''' + repr(OUTPUT) + '''}))
''')
    assert cli_adapter.run("claude", envelope("claude")).summary == OUTPUT["summary"]


@pytest.mark.parametrize("result", [
    {"is_error": True, "structured_output": OUTPUT},
    {"subtype": "error_max_turns", "structured_output": OUTPUT},
    {"result": json.dumps(OUTPUT)},
    {"structured_output": "not an object"},
])
def test_claude_error_or_unstructured_result_rejected(fake_cli, result):
    install, _ = fake_cli
    install("claude", f"print({json.dumps(result)!r})\n")
    with pytest.raises(DeskError, match="invalid_claude_result"):
        cli_adapter.run("claude", envelope("claude"))


@pytest.mark.parametrize("change,code", [
    ({"protocol_version": True}, "invalid_cli_protocol"),
    ({"desk_id": "claude"}, "cli_desk_mismatch"),
    ({"model_id": "--dangerous-option"}, "invalid_cli_model"),
    ({"output_schema": {}}, "cli_schema_mismatch"),
    ({"phase": "execute"}, "invalid_cli_phase"),
])
def test_invalid_envelope_rejected_before_process(fake_cli, change, code):
    with pytest.raises(DeskError, match=code):
        cli_adapter.run("codex", envelope(**change))


def test_output_and_time_are_bounded(fake_cli, monkeypatch):
    install, _ = fake_cli
    install("codex", "import pathlib,sys\na=sys.argv\npathlib.Path(a[a.index('-o')+1]).write_text('x'*64001)\n")
    with pytest.raises(DeskError, match="cli_output_too_large"):
        cli_adapter.run("codex", envelope())
    install("codex", "import time\ntime.sleep(2)\n")
    monkeypatch.setattr(cli_adapter, "CLI_TIMEOUT", .05)
    with pytest.raises(DeskError, match="research_cli_timeout"):
        cli_adapter.run("codex", envelope())
    with pytest.raises(DeskError, match="cli_input_too_large"):
        cli_adapter.run("codex", b"x" * (cli_adapter.MAX_INPUT + 1))


def test_absolute_script_works_from_unrelated_directory(fake_cli, tmp_path):
    install, home = fake_cli
    install("codex", codex_script())
    cwd = tmp_path / "unrelated"
    cwd.mkdir()
    path = str(Path(cli_adapter.__file__).resolve())
    env = {"HOME": str(home), "PATH": str(home.parent / "bin")}
    result = subprocess.run([sys.executable, path, "codex"], input=envelope(), cwd=cwd,
                            env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    output = ResearchOutput.model_validate_json(result.stdout)
    assert output.summary == OUTPUT["summary"]
    assert b"private provider progress" not in result.stdout


def test_failure_output_is_redacted(fake_cli, tmp_path):
    install, home = fake_cli
    install("codex", "import sys\nprint('account-and-secret-details',file=sys.stderr)\nsys.exit(1)\n")
    result = subprocess.run([sys.executable, str(Path(cli_adapter.__file__).resolve()), "codex"],
        input=envelope(), cwd=tmp_path, env={"HOME": str(home), "PATH": str(home.parent / "bin")},
        capture_output=True, timeout=15)
    assert result.returncode == 1 and result.stdout == b""
    assert result.stderr == b"research_cli_failed\n"


def test_codex_strict_schema_preserves_nullable_constraints_and_protocol():
    source = ResearchOutput.model_json_schema()
    original = json.dumps(source, sort_keys=True)
    wire = cli_adapter._codex_schema(source)
    def verify(node):
        if isinstance(node, list):
            for item in node:
                verify(item)
        elif isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object":
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for item in node.values():
                verify(item)
    verify(wire)
    candidate = wire["$defs"]["Candidate"]["properties"]["probability"]
    assert {"type": "null"} in candidate["anyOf"]
    assert wire["$defs"]["Decision"]["properties"]["probability"]["anyOf"] == source["$defs"]["Decision"]["properties"]["probability"]["anyOf"]
    assert json.dumps(source, sort_keys=True) == original
    assert cli_adapter._validate(envelope(), "codex")["output_schema"] == source


def test_claude_wire_subset_keeps_full_contract_locally():
    source = ResearchOutput.model_json_schema()
    original = json.dumps(source, sort_keys=True)
    wire = cli_adapter._claude_schema(source)
    unsupported = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                   "multipleOf", "minLength", "maxLength", "maxItems"}
    def verify(node):
        if isinstance(node, list):
            for item in node:
                verify(item)
        elif isinstance(node, dict):
            assert not unsupported.intersection(node)
            assert "(?" not in node.get("pattern", "")
            assert node.get("minItems", 0) in (0, 1)
            for item in node.values():
                verify(item)
    verify(wire)
    assert wire["$defs"]["Decision"]["properties"]["max_spend"]["default"] == "1.00"
    assert "maxLength" in wire["properties"]["summary"]["description"]
    assert {"type": "null"} in wire["$defs"]["Candidate"]["properties"]["probability"]["anyOf"]
    assert json.dumps(source, sort_keys=True) == original
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        cli_adapter.parse_output(json.dumps({"summary": "x", "next_action": "Still invalid short summary"}))
