"""Backend layer: command construction, output parsing, failover.

Nothing here starts a process. Each CLI backend funnels through
``process.run``, which is replaced with a stub that records the argv it was
handed and replays canned output - so the tests assert on the exact invocation
and the exact parse, which is where these integrations actually break.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pydantic
import pytest

from sourcework import usage as usage_module
from sourcework.backends import base, process, resolve_chain
from sourcework.backends.agy import AgyBackend
from sourcework.backends.agy import parse_output as agy_output
from sourcework.backends.base import (
    BackendQuotaError,
    BackendRequest,
    EmptyBackendResponseError,
    ImageInput,
    LLMUsage,
    OutputTruncatedError,
    looks_like_quota,
)
from sourcework.backends.claude_code import ClaudeCodeBackend
from sourcework.backends.codex import CodexBackend
from sourcework.backends.codex import parse_events as codex_events
from sourcework.backends.copilot import CopilotBackend
from sourcework.backends.copilot import parse_events as copilot_events
from sourcework.backends.opencode import OpenCodeBackend, parse_events
from sourcework.config import LLMSettings

PIXEL = base64.b64encode(bytes.fromhex("89504e470d0a1a0a")).decode()


@pytest.fixture
def cli(monkeypatch):
    """Replace the subprocess runner; record argv, replay a scripted result."""
    calls: list[dict] = []
    scripted: list[process.ProcessResult] = []

    async def fake_run(argv, *, cwd=None, env=None, stdin_text=None, timeout_s=300.0,
                       on_line=None):
        calls.append(
            {"argv": list(argv), "cwd": str(cwd) if cwd else None, "env": env or {},
             "stdin": stdin_text, "timeout": timeout_s, "streamed": on_line is not None}
        )
        result = scripted.pop(0) if scripted else process.ProcessResult(0, "", "")
        if on_line is not None:
            # The real runner feeds each line to the sink as it arrives. Replaying
            # the scripted stdout the same way is what lets a test assert on what
            # a backend would have streamed.
            for line in (result.stdout or "").splitlines():
                on_line(line)
        return result

    monkeypatch.setattr(process, "run", fake_run)

    class Harness:
        def script(self, stdout="", *, exit_code=0, stderr="", timed_out=False):
            scripted.append(process.ProcessResult(exit_code, stdout, stderr, timed_out))

        @property
        def calls(self):
            return calls

        @property
        def argv(self):
            return calls[-1]["argv"]

    return Harness()


def request(**kwargs) -> BackendRequest:
    return BackendRequest(**{"system": "SYS", "user": "USR", **kwargs})


# ---------------------------------------------------------------------------
# claude-code
# ---------------------------------------------------------------------------


def _claude_json(result="hello", **extra):
    return json.dumps({"is_error": False, "result": result, **extra})


async def test_claude_code_plain_generation_disables_tools_and_mcp(cli):
    cli.script(_claude_json())
    out = await ClaudeCodeBackend().generate(request(model="haiku", effort="low"))

    assert out.text == "hello"
    argv = cli.argv
    # No tools: the whole point of the plain-generation path.
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--effort") + 1] == "low"
    assert argv[-1] == "USR", "the prompt must be the final positional argument"
    # A coding CLI must never inherit the project checkout as its cwd.
    assert cli.calls[-1]["cwd"] == str(process.neutral_cwd())


async def test_claude_code_default_model_alias_is_not_passed_through(cli):
    cli.script(_claude_json())
    await ClaudeCodeBackend().generate(request(model="default"))
    assert "--model" not in cli.argv


async def test_claude_code_grants_read_only_for_images(cli):
    cli.script(_claude_json())
    await ClaudeCodeBackend().generate(request(images=[ImageInput(media_type="image/png", data_b64=PIXEL)]))

    argv = cli.argv
    assert argv[argv.index("--allowed-tools") + 1] == "Read"
    assert "--tools" not in argv, "an empty tool list would revoke the Read grant"
    # --allowed-tools is variadic: a flag has to follow it, never the prompt.
    assert argv[argv.index("--allowed-tools") + 2].startswith("--")
    assert "ATTACHED IMAGE FILES" in argv[-1]


async def test_claude_code_oversized_prompt_goes_on_stdin(cli):
    cli.script(_claude_json())
    big = "x" * (process.MAX_ARGV_PROMPT_BYTES + 1)
    await ClaudeCodeBackend().generate(request(user=big))

    assert cli.calls[-1]["stdin"] == big
    assert big not in cli.argv, "an oversized prompt on argv fails execve with E2BIG"


async def test_claude_code_raises_output_cap_only_when_worth_raising(cli):
    cli.script(_claude_json())
    await ClaudeCodeBackend().generate(request(max_tokens=64_000))
    assert cli.calls[-1]["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"

    cli.script(_claude_json())
    await ClaudeCodeBackend().generate(request(max_tokens=8_000))
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in cli.calls[-1]["env"]


async def test_claude_code_reports_usage_as_api_equivalent_not_dollars(cli):
    cli.script(
        _claude_json(
            usage={"input_tokens": 184, "output_tokens": 31, "cache_read_input_tokens": 12},
            total_cost_usd=0.0023,
            duration_ms=1500,
        )
    )
    out = await ClaudeCodeBackend().generate(request())

    assert out.usage.input_tokens == 184
    assert out.usage.cache_read_tokens == 12
    # Under a subscription the CLI's figure is not what anyone is billed, and
    # summing it with a real dollar figure would be meaningless.
    assert out.usage.cost_unit == base.COST_USD_API_EQUIVALENT


async def test_claude_code_truncation_never_reaches_the_parser(cli):
    cli.script(
        json.dumps(
            {"is_error": False, "result": '{"partial": ', "stop_reason": "max_tokens",
             "usage": {"output_tokens": 8192}}
        )
    )
    with pytest.raises(OutputTruncatedError, match="output limit"):
        await ClaudeCodeBackend().generate(request())


async def test_claude_code_usage_limit_is_a_quota_error(cli):
    cli.script(json.dumps({"is_error": True, "result": "You've reached your usage limit."}))
    with pytest.raises(BackendQuotaError):
        await ClaudeCodeBackend().generate(request())


# ---------------------------------------------------------------------------
# opencode-cli
# ---------------------------------------------------------------------------


def _oc(*events):
    return "\n".join(json.dumps(e) for e in events)


TEXT_EVENT = {"type": "text", "part": {"text": "hello"}}


async def test_opencode_message_precedes_the_file_flag(cli):
    cli.script(_oc(TEXT_EVENT))
    await OpenCodeBackend().generate(
        request(images=[ImageInput(media_type="image/png", data_b64=PIXEL)])
    )

    argv = cli.argv
    # -f is a greedy array flag: a positional after it is eaten as a filename.
    assert argv.index("-f") > argv.index("SYSTEM:\nSYS\n\nUSER:\nUSR")
    assert argv[argv.index("--agent") + 1] == "sourcework-answer"
    assert argv[argv.index("--dir") + 1] == str(process.neutral_cwd())


async def test_opencode_raises_the_output_ceiling(cli):
    cli.script(_oc(TEXT_EVENT))
    await OpenCodeBackend().generate(request())
    env = cli.calls[-1]["env"]
    assert int(env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"]) >= 1_000_000
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "true"


async def test_opencode_retries_once_on_provider_side_model_demotion(cli):
    cli.script(_oc({"type": "error", "error": {"data": {"message": "model not supported"}}}), exit_code=1)
    cli.script(_oc(TEXT_EVENT))

    out = await OpenCodeBackend().generate(request(model="opencode/big", effort="high"))

    assert out.text == "hello"
    assert len(cli.calls) == 2
    retry = cli.calls[1]["argv"]
    assert "-m" not in retry and "--variant" not in retry
    assert cli.calls[1]["stdin"] == cli.calls[0]["stdin"], "the retry must carry the message the same way"


async def test_opencode_prefers_a_standalone_json_final_block_over_narration():
    parsed = parse_events(
        _oc(
            {"type": "text", "part": {"text": "Let me think about this."}},
            {"type": "text", "part": {"text": '{"answer": 42}'}},
        )
    )
    # Concatenating would hand the JSON parser "...this.{"answer": 42}".
    assert parsed.text == '{"answer": 42}'


def test_opencode_keeps_every_part_when_the_answer_is_prose():
    parsed = parse_events(
        _oc({"type": "text", "part": {"text": "First half."}}, {"type": "text", "part": {"text": "Second half."}})
    )
    assert parsed.text == "First half.\nSecond half."


def test_opencode_sums_usage_across_steps():
    parsed = parse_events(
        _oc(
            {"type": "step_finish", "part": {"cost": 0.001, "tokens": {"input": 100, "output": 10, "cache": {"read": 5}}}},
            {"type": "step_finish", "part": {"cost": 0.002, "tokens": {"input": 200, "output": 20, "cache": {"read": 7}}}},
        )
    )
    assert parsed.usage.input_tokens == 300
    assert parsed.usage.output_tokens == 30
    assert parsed.usage.cache_read_tokens == 12
    assert parsed.usage.cost == pytest.approx(0.003)
    assert parsed.usage.cost_unit == base.COST_USD


def test_opencode_survives_non_json_noise_between_events():
    parsed = parse_events("warning: something\n" + _oc(TEXT_EVENT) + "\nnot json at all")
    assert parsed.text == "hello"


async def test_opencode_empty_stream_is_an_empty_response_error(cli):
    cli.script("")
    with pytest.raises(EmptyBackendResponseError):
        await OpenCodeBackend().generate(request())


# ---------------------------------------------------------------------------
# copilot-cli
# ---------------------------------------------------------------------------


async def test_copilot_disables_tools_but_not_when_attaching_images(cli):
    cli.script(json.dumps({"type": "assistant.message", "data": {"content": "hi"}}))
    await CopilotBackend().generate(request())
    assert "--available-tools=" in cli.argv

    cli.script(json.dumps({"type": "assistant.message", "data": {"content": "hi"}}))
    await CopilotBackend().generate(
        request(images=[ImageInput(media_type="image/png", data_b64=PIXEL)])
    )
    assert "--available-tools=" not in cli.argv
    assert "--attachment" in cli.argv


async def test_copilot_honours_a_dedicated_home(cli):
    cli.script(json.dumps({"type": "assistant.message", "data": {"content": "hi"}}))
    await CopilotBackend(home="/tmp/sourcework-copilot").generate(request())
    assert cli.calls[-1]["env"]["COPILOT_HOME"] == "/tmp/sourcework-copilot"


async def test_copilot_refuses_a_prompt_it_cannot_physically_send(cli):
    # -p takes the prompt inline and the CLI does not read stdin, so this is a
    # real limit, not a policy - failing loudly lets the chain route around it.
    with pytest.raises(base.BackendError, match="cannot send"):
        await CopilotBackend().generate(request(user="x" * (process.MAX_ARGV_PROMPT_BYTES + 1)))
    assert not cli.calls


def test_copilot_complete_message_wins_over_deltas():
    text, _ = copilot_events(
        "\n".join(
            json.dumps(e)
            for e in (
                {"type": "assistant.message_delta", "data": {"deltaContent": "he"}},
                {"type": "assistant.message_delta", "data": {"deltaContent": "llo"}},
                {"type": "assistant.message", "data": {"content": "hello"}},
            )
        )
    )
    assert text == "hello"


def test_copilot_falls_back_to_deltas_when_the_stream_was_cut():
    text, _ = copilot_events(
        json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": "par"}})
    )
    assert text == "par"


def test_copilot_converts_session_credits_at_the_published_rate():
    _, usage = copilot_events(
        json.dumps({"type": "session.usage_checkpoint", "data": {"totalNanoAiu": 1_600_000_000}})
    )
    assert usage.credits == pytest.approx(1.6)
    assert usage.cost == pytest.approx(0.016)
    # Never plain USD: the conversion is ours, not the provider's.
    assert usage.cost_unit == base.COST_USD_FROM_CREDITS


# ---------------------------------------------------------------------------
# codex-cli
#
# Event shapes here are copied from a real `codex exec --json` run against
# codex-cli 0.147.0, not invented.
# ---------------------------------------------------------------------------


def _codex(*events):
    return "\n".join(json.dumps(e) for e in events)


CODEX_ANSWER = {"type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "hello"}}
CODEX_USAGE = {"type": "turn.completed",
               "usage": {"input_tokens": 12852, "cached_input_tokens": 9984,
                         "cache_write_input_tokens": 0, "output_tokens": 5,
                         "reasoning_output_tokens": 0}}


async def test_codex_always_skips_the_git_repo_check(cli):
    """Without it every call fails "Not inside a trusted directory", because
    the neutral cwd is a temp directory rather than a repository. A developer
    testing the command by hand inside a checkout never sees it."""
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(request())

    argv = cli.argv
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("-C") + 1] == str(process.neutral_cwd())
    assert cli.calls[-1]["cwd"] == str(process.neutral_cwd())


async def test_codex_prompt_precedes_the_image_flags(cli):
    """`-i` is variadic (`<FILE>...`), so a positional after it is read as
    another image path - the same trap as opencode's `-f`."""
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(
        request(images=[ImageInput(media_type="image/png", data_b64=PIXEL)])
    )

    argv = cli.argv
    assert argv.index("-i") > argv.index("SYSTEM:\nSYS\n\nUSER:\nUSR")


async def test_codex_keeps_the_image_tool_only_when_there_are_images(cli):
    """The tool that reads the picture is the one a vision call cannot lose -
    the same trade claude-code makes by granting Read."""
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(request())
    assert "view_image" in cli.argv, "disabled when nothing needs it"

    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(
        request(images=[ImageInput(media_type="image/png", data_b64=PIXEL)])
    )
    assert "view_image" not in cli.argv, "must stay enabled to read the image"
    assert "shell_tool" in cli.argv, "...but the shell stays off either way"


async def test_codex_runs_read_only_and_ignores_the_developers_setup(cli):
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(request())

    argv = cli.argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules"):
        assert flag in argv


async def test_codex_oversized_prompt_is_read_from_stdin_via_a_bare_dash(cli):
    """A positional prompt alongside piped stdin is *appended* as a <stdin>
    block rather than replaced, so the positional has to become `-`."""
    cli.script(_codex(CODEX_ANSWER))
    big = "x" * (process.MAX_ARGV_PROMPT_BYTES + 1)
    await CodexBackend().generate(request(user=big))

    assert "-" in cli.argv
    assert cli.calls[-1]["stdin"].endswith(big)
    assert big not in cli.argv


@pytest.mark.parametrize(("asked", "sent"), [
    ("low", "low"), ("medium", "high"), ("high", "high"),
    ("xhigh", "xhigh"), ("max", "xhigh"),
])
async def test_codex_maps_the_effort_vocabulary_onto_the_three_it_takes(cli, asked, sent):
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(request(effort=asked))
    assert f"model_reasoning_effort={sent}" in cli.argv


@pytest.mark.parametrize("junk", ["banana", "", "  "])
async def test_codex_drops_an_effort_it_cannot_map(cli, junk):
    """Effort travels as `-c model_reasoning_effort=…`, a config override,
    where an unrecognised value is a hard startup error that kills the call -
    unlike a flag value, which fails validation legibly."""
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(request(effort=junk))
    assert not any(a.startswith("model_reasoning_effort") for a in cli.argv)


async def test_codex_honours_a_dedicated_home(cli):
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend(home="/tmp/codex-sourcework").generate(request())
    assert cli.calls[-1]["env"]["CODEX_HOME"] == "/tmp/codex-sourcework"


async def test_codex_never_asks_for_a_strict_output_schema(cli):
    """--output-schema requires OpenAI strict mode (additionalProperties false,
    every property required), which this project's Pydantic schemas do not
    satisfy - the same reason the litellm backend omits `strict`."""
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(
        request(json_schema={"type": "object", "properties": {"a": {"type": "string"}}})
    )
    assert "--output-schema" not in cli.argv


def test_codex_sums_usage_across_turns_and_reports_no_cost():
    text, usage, error = codex_events(_codex(CODEX_ANSWER, CODEX_USAGE, CODEX_USAGE))

    assert text == "hello"
    assert error is None
    assert usage.input_tokens == 2 * 12852
    assert usage.cache_read_tokens == 2 * 9984
    # Codex reports tokens and no money; deriving dollars would invent the
    # number the cost units exist to prevent.
    assert usage.cost is None and usage.cost_unit is None


def test_codex_prefers_a_standalone_json_final_message_over_narration():
    narration = {"type": "item.completed",
                 "item": {"id": "a", "type": "agent_message", "text": "Let me think..."}}
    answer = {"type": "item.completed",
              "item": {"id": "b", "type": "agent_message", "text": '{"value": 1}'}}
    text, _, _ = codex_events(_codex(narration, answer))
    assert text == '{"value": 1}'


def test_codex_keeps_every_message_when_the_answer_is_prose():
    first = {"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": "one"}}
    second = {"type": "item.completed", "item": {"id": "b", "type": "agent_message", "text": "two"}}
    text, _, _ = codex_events(_codex(first, second))
    assert text == "one\ntwo", "joined, never concatenated mid-sentence"


async def test_codex_streams_without_asking_for_anything_extra(cli):
    """--json is unconditional because it is how the answer is parsed at all,
    so watching a run costs no flag change - unique among the CLI backends."""
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(request())
    silent = list(cli.argv)

    chunks, sink = _sink()
    cli.script(_codex(CODEX_ANSWER))
    await CodexBackend().generate(request(on_chunk=sink))

    assert cli.argv == silent
    assert [(c.kind, c.text) for c in chunks] == [("text", "hello")]


async def test_codex_does_not_show_the_answer_twice(cli):
    """A build emitting deltas *and* a completed event for the same item would
    otherwise print the whole answer a second time."""
    delta = {"type": "item.updated",
             "item": {"id": "item_0", "type": "agent_message", "text": "hel"}}
    chunks, sink = _sink()
    cli.script(_codex(delta, CODEX_ANSWER))
    await CodexBackend().generate(request(on_chunk=sink))

    assert [c.text for c in chunks] == ["hel"]


async def test_codex_empty_stream_is_an_empty_response_error(cli):
    cli.script(_codex(CODEX_USAGE))
    with pytest.raises(EmptyBackendResponseError):
        await CodexBackend().generate(request())


async def test_codex_reports_usage_billed_before_a_failure(cli):
    cli.script(_codex(CODEX_USAGE), exit_code=1, stderr="usage limit reached")
    with pytest.raises(BackendQuotaError) as caught:
        await CodexBackend().generate(request())
    assert caught.value.usage.input_tokens == 12852


# ---------------------------------------------------------------------------
# agy-cli
#
# Shapes copied from a real `agy --print --output-format json` run against
# agy 1.1.12.
# ---------------------------------------------------------------------------

AGY_RESULT = {
    "conversation_id": "abc", "status": "SUCCESS", "response": "hello\n",
    "duration_seconds": 2.18, "num_turns": 1,
    "usage": {"input_tokens": 17744, "output_tokens": 26, "thinking_tokens": 22,
              "cache_read_tokens": 0, "total_tokens": 17770},
}


async def test_agy_never_lets_a_document_look_like_a_slash_command(cli):
    """Print mode expands slash commands and skills from the prompt text, and
    these prompts are documents - a transcript line starting "/" is content."""
    cli.script(json.dumps(AGY_RESULT))
    await AgyBackend().generate(request())
    assert "--disable-slash-commands" in cli.argv
    assert "--dangerously-skip-permissions" not in cli.argv


async def test_agy_is_told_our_timeout_not_its_own(cli):
    """agy's --print-timeout defaults to five minutes, which would cut a long
    analyst call off before this project's timeout ever applied. The shortest
    clock wins and it must not be theirs."""
    cli.script(json.dumps(AGY_RESULT))
    await AgyBackend().generate(request(timeout_s=600.0))
    assert cli.argv[cli.argv.index("--print-timeout") + 1] == "600s"


async def test_agy_sends_the_schema_it_can_actually_enforce(cli):
    """The only CLI backend that honours request.json_schema rather than only
    describing it in the prompt."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    cli.script(json.dumps({**AGY_RESULT, "structured_output": {"a": "x"}}))
    result = await AgyBackend().generate(request(json_schema=schema))

    assert json.loads(cli.argv[cli.argv.index("--json-schema") + 1]) == schema
    # The conforming value is the answer; `response` keeps the prose.
    assert json.loads(result.text) == {"a": "x"}


async def test_agy_asks_for_no_schema_when_none_was_given(cli):
    cli.script(json.dumps(AGY_RESULT))
    await AgyBackend().generate(request())
    assert "--json-schema" not in cli.argv


@pytest.mark.parametrize(("asked", "sent"), [
    ("low", "low"), ("medium", "medium"), ("high", "high"),
    ("xhigh", "high"), ("max", "high"),
])
async def test_agy_collapses_the_top_of_the_effort_vocabulary(cli, asked, sent):
    cli.script(json.dumps(AGY_RESULT))
    await AgyBackend().generate(request(model="claude-sonnet-4-6", effort=asked))
    assert cli.argv[cli.argv.index("--effort") + 1] == sent


async def test_agy_lets_a_tiered_model_id_win_over_the_effort_flag(cli):
    """Most ids already encode a tier (gemini-3.6-flash-low). Sending both is a
    contradiction the CLI would have to resolve for us."""
    cli.script(json.dumps(AGY_RESULT))
    await AgyBackend().generate(request(model="gemini-3.6-flash-low", effort="high"))
    assert "--effort" not in cli.argv


async def test_agy_oversized_prompt_goes_on_stdin_with_no_print_flag(cli):
    """agy reads a piped stdin as the prompt, so passing --print as well would
    send the whole thing twice."""
    cli.script(json.dumps(AGY_RESULT))
    big = "x" * (process.MAX_ARGV_PROMPT_BYTES + 1)
    await AgyBackend().generate(request(user=big))

    assert "--print" not in cli.argv
    assert cli.calls[-1]["stdin"].endswith(big)
    assert big not in cli.argv


def test_agy_reads_tokens_and_reports_no_cost():
    text, usage, error = agy_output(json.dumps(AGY_RESULT))

    assert text == "hello\n"
    assert error is None
    assert usage.input_tokens == 17744
    assert usage.reasoning_tokens == 22, "agy calls them thinking_tokens"
    assert usage.cost is None and usage.cost_unit is None


def test_agy_finds_the_result_inside_a_stream():
    stream = "\n".join([
        json.dumps({"event": "init"}),
        json.dumps({"event": "step_update",
                    "step_update": {"step_type": "agent_response", "state": "ACTIVE",
                                    "text_delta": "hel"}}),
        json.dumps({"event": "result", "result": AGY_RESULT}),
    ])
    text, usage, _ = agy_output(stream)
    assert text == "hello\n"
    assert usage.input_tokens == 17744


def test_agy_a_non_success_status_is_an_error():
    failed = {**AGY_RESULT, "status": "FAILED", "error": "quota exceeded"}
    _, _, error = agy_output(json.dumps(failed))
    assert "quota" in error


async def test_agy_streams_deltas_only_when_someone_is_watching(cli):
    cli.script(json.dumps(AGY_RESULT))
    await AgyBackend().generate(request())
    assert cli.argv[cli.argv.index("--output-format") + 1] == "json"

    stream = "\n".join([
        json.dumps({"event": "step_update",
                    "step_update": {"step_type": "agent_response", "state": "ACTIVE",
                                    "text_delta": "hel"}}),
        json.dumps({"event": "result", "result": AGY_RESULT}),
    ])
    chunks, sink = _sink()
    cli.script(stream)
    await AgyBackend().generate(request(on_chunk=sink))

    assert cli.argv[cli.argv.index("--output-format") + 1] == "stream-json"
    assert [(c.kind, c.text) for c in chunks] == [("text", "hel")]


async def test_agy_cannot_carry_images_and_says_so():
    """Stated rather than fudged: resolve_chain drops it from an image call so
    the vision role fails over, instead of it answering about nothing."""
    assert AgyBackend().supports_vision is False

    cfg = LLMSettings(backend="agy-cli", failover_order=["codex-cli"])
    assert resolve_chain(cfg, needs_vision=True) == ["codex-cli"]
    assert resolve_chain(cfg) == ["agy-cli", "codex-cli"]


# ---------------------------------------------------------------------------
# Chain resolution
# ---------------------------------------------------------------------------


def test_chain_is_just_the_active_backend_without_failover():
    assert resolve_chain(LLMSettings(backend="claude-code")) == ["claude-code"]


def test_chain_appends_failover_targets_and_drops_duplicates():
    cfg = LLMSettings(backend="claude-code", failover_order=["claude-code", "opencode-cli", "nonsense"])
    assert resolve_chain(cfg) == ["claude-code", "opencode-cli"]


def test_chain_survives_underscored_backend_ids_from_the_environment():
    cfg = LLMSettings(backend="claude_code", failover_order=["opencode_cli"])
    assert resolve_chain(cfg) == ["claude-code", "opencode-cli"]


def test_failover_order_accepts_a_comma_separated_string():
    # .env files cannot write JSON lists comfortably.
    cfg = LLMSettings(failover_order="claude-code, opencode-cli")
    assert cfg.failover_order == ["claude-code", "opencode-cli"]


def test_a_model_id_never_travels_to_another_backend():
    cfg = LLMSettings(backend="litellm", default_model="anthropic/claude-sonnet-4-5")
    assert cfg.model_for("default") == "anthropic/claude-sonnet-4-5"
    # An OpenRouter-style id means nothing to the claude CLI; None means
    # "use your own default", which is the only safe answer.
    assert cfg.model_for("default", "claude-code") is None


def test_per_backend_models_fall_back_to_that_backend_default_role():
    cfg = LLMSettings(backend="claude-code", claude_code_models={"default": "haiku", "reasoning": "opus"})
    assert cfg.model_for("reasoning") == "opus"
    assert cfg.model_for("vision") == "haiku"


def test_cli_backends_get_the_longer_timeout():
    cfg = LLMSettings(timeout_s=180, cli_timeout_s=600)
    assert cfg.timeout_for("litellm") == 180
    assert cfg.timeout_for("opencode-cli") == 600


# ---------------------------------------------------------------------------
# Quota classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detail",
    [
        "You've reached your usage limit. Your limit resets 11:30pm",
        "quota exceeded for this model",
        "insufficient balance",          # OpenCode's wording for an empty wallet
        "not enough credits",
        "429 Too Many Requests",
    ],
)
def test_quota_signatures_are_recognised_across_backend_vocabularies(detail):
    assert looks_like_quota(detail)
    assert isinstance(base.classify(detail), BackendQuotaError)


def test_an_ordinary_failure_is_not_mistaken_for_a_quota_hit():
    assert not looks_like_quota("unexpected server error")
    assert not isinstance(base.classify("unexpected server error"), BackendQuotaError)


# ---------------------------------------------------------------------------
# Usage ledger
# ---------------------------------------------------------------------------


def test_ledger_never_adds_costs_denominated_in_different_units():
    with usage_module.track() as ledger:
        usage_module.record("claude-code", LLMUsage(output_tokens=10, cost=0.05, cost_unit=base.COST_USD_API_EQUIVALENT))
        usage_module.record("opencode-cli", LLMUsage(output_tokens=20, cost=0.01, cost_unit=base.COST_USD))

    totals = ledger.as_dict()["backends"]
    assert totals["claude-code"]["cost"] == {base.COST_USD_API_EQUIVALENT: 0.05}
    assert totals["opencode-cli"]["cost"] == {base.COST_USD: 0.01}


def test_recording_outside_a_tracked_block_is_a_no_op():
    usage_module.record("claude-code", LLMUsage(output_tokens=1))  # must not raise
    assert usage_module.current() is None


def test_a_backend_that_reports_nothing_still_counts_as_a_call():
    with usage_module.track() as ledger:
        usage_module.record("copilot-cli", None)
    assert ledger.calls == 1


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def test_argv_limit_counts_bytes_not_characters():
    # 40k CJK characters are ~120KB to execve; a length check would wave them by.
    assert process.exceeds_argv_limit("漢" * 40_000)
    assert not process.exceeds_argv_limit("a" * 40_000)


def test_error_detail_digs_the_reason_out_of_json_on_stdout():
    result = process.ProcessResult(
        1, json.dumps({"type": "error", "error": {"message": "boom"}}), ""
    )
    # Trusting stderr alone reports "failed: " with the reason sitting in stdout.
    assert "boom" in process.error_detail(result)


def test_error_detail_prefers_stderr_when_there_is_one():
    assert process.error_detail(process.ProcessResult(1, "{}", "real reason")) == "real reason"


async def test_run_captures_output_and_exit_code():
    result = await process.run(["sh", "-c", "echo out; echo err >&2; exit 3"], timeout_s=30)
    assert result.exit_code == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


async def test_run_kills_a_hung_process_and_still_reports_what_it_said():
    result = await process.run(["sh", "-c", "echo partial; sleep 30"], timeout_s=1.5)
    assert result.timed_out
    # The output produced before the kill is usually the only clue about the hang.
    assert "partial" in result.stdout


async def test_cancelling_a_call_kills_the_process_it_started():
    """Unwinding the coroutine stops nothing on its own.

    This is where the money is: the child is a CLI answering a model prompt,
    and left alone it runs to completion and bills for every token of an answer
    nobody is waiting for. Watched happening before this - the Python side
    reported "cancelled" while the subprocess was still going.
    """
    marker = Path(tempfile.mkdtemp()) / "still-running"
    started = Path(str(marker) + ".started")
    call = asyncio.create_task(
        process.run(
            ["sh", "-c", f"touch {started}; sleep 5; touch {marker}"], timeout_s=30
        )
    )
    for _ in range(100):  # wait for the child to be up, without a fixed sleep
        if started.exists():
            break
        await asyncio.sleep(0.05)

    call.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await call

    await asyncio.sleep(0.3)
    assert started.exists(), "the child never ran, so this proves nothing"
    assert not marker.exists(), "the child outlived the call that started it"


async def test_cancelling_a_call_kills_what_the_child_started_too():
    """The group, not the process. Every backend here is a CLI that shells out
    further, and killing only the direct child leaves the grandchildren holding
    the work."""
    marker = Path(tempfile.mkdtemp()) / "grandchild-finished"
    started = Path(str(marker) + ".started")
    call = asyncio.create_task(
        process.run(
            ["sh", "-c", f"(sleep 5; touch {marker}) & touch {started}; wait"], timeout_s=30
        )
    )
    for _ in range(100):
        if started.exists():
            break
        await asyncio.sleep(0.05)

    call.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await call

    await asyncio.sleep(0.3)
    assert started.exists(), "the child never ran, so this proves nothing"
    assert not marker.exists(), "a grandchild outlived the call that started it"


async def test_run_survives_a_child_that_ignores_stdin():
    result = await process.run(["sh", "-c", "echo done"], stdin_text="x" * 200_000, timeout_s=30)
    assert result.stdout.strip() == "done"


def test_staged_media_writes_files_and_cleans_up_after():
    image = ImageInput(media_type="image/jpeg", data_b64=PIXEL)
    with process.staged_media([image]) as paths:
        assert paths[0].suffix == ".jpg"
        assert paths[0].read_bytes() == image.raw_bytes()
        directory = paths[0].parent
    assert not directory.exists()


# ---------------------------------------------------------------------------
# Empty-response resilience
# ---------------------------------------------------------------------------


class _Flaky(base.LLMBackend):
    """Answers on the Nth attempt; returns nothing before that."""

    id = "litellm"
    supports_vision = True

    def __init__(self, succeed_on: int) -> None:
        self.succeed_on = succeed_on
        self.calls = 0

    async def generate(self, req):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.calls < self.succeed_on:
            raise EmptyBackendResponseError("no content", backend=self.id)
        return base.BackendResult(text="answered")


async def test_an_empty_response_is_retried_on_the_same_backend(monkeypatch):
    from sourcework import llm as llm_module
    from sourcework.config import LLMSettings

    flaky = _Flaky(succeed_on=2)
    monkeypatch.setattr(llm_module, "build", lambda backend_id, cfg: flaky)

    cfg = LLMSettings(backend="litellm", default_model="m", empty_retries=1)
    assert await llm_module.LLM(cfg=cfg).text("s", "u") == "answered"
    # One empty answer must not cost the whole extraction.
    assert flaky.calls == 2


async def test_a_persistently_empty_backend_gives_actionable_advice(monkeypatch):
    from sourcework import llm as llm_module
    from sourcework.config import LLMSettings

    flaky = _Flaky(succeed_on=99)
    monkeypatch.setattr(llm_module, "build", lambda backend_id, cfg: flaky)

    cfg = LLMSettings(backend="litellm", default_model="m", empty_retries=1)
    with pytest.raises(llm_module.LLMError, match="reasoning effort"):
        await llm_module.LLM(cfg=cfg).text("s", "u")
    assert flaky.calls == 2  # bounded: 1 + empty_retries


async def test_a_quota_error_is_not_retried_on_the_same_backend(monkeypatch):
    from sourcework import llm as llm_module
    from sourcework.config import LLMSettings

    class Exhausted(base.LLMBackend):
        id = "litellm"
        supports_vision = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, req):  # noqa: ANN001, ANN201
            self.calls += 1
            raise BackendQuotaError("usage limit", backend=self.id)

    exhausted = Exhausted()
    monkeypatch.setattr(llm_module, "build", lambda backend_id, cfg: exhausted)

    cfg = LLMSettings(backend="litellm", default_model="m", empty_retries=2)
    with pytest.raises(llm_module.LLMError):
        await llm_module.LLM(cfg=cfg).text("s", "u")
    # Repeating a quota error only burns wall clock.
    assert exhausted.calls == 1


async def test_opencode_names_its_own_session(cli):
    # Without --title, OpenCode makes a second model call per invocation on its
    # own "small" model just to invent a session title nobody reads.
    cli.script(_oc(TEXT_EVENT))
    await OpenCodeBackend().generate(request())
    argv = cli.argv
    assert argv[argv.index("--title") + 1] == "sourcework"


# ---------------------------------------------------------------------------
# Streaming
#
# The chunk sink is opt-in: without it every backend must invoke exactly as it
# did before, because the flags that enable streaming are not free (an extra
# output format for claude-code, --thinking for opencode).
# ---------------------------------------------------------------------------


def _sink() -> tuple[list, object]:
    chunks: list = []
    return chunks, chunks.append


async def test_opencode_streams_reasoning_and_text(cli):
    chunks, sink = _sink()
    cli.script(_oc({"type": "reasoning", "part": {"text": "weighing options"}}, TEXT_EVENT))
    await OpenCodeBackend().generate(request(on_chunk=sink))

    assert "--thinking" in cli.argv
    assert [(c.kind, c.text) for c in chunks] == [
        ("reasoning", "weighing options"),
        ("text", "hello"),
    ]


async def test_opencode_asks_for_thinking_only_when_someone_is_watching(cli):
    cli.script(_oc(TEXT_EVENT))
    await OpenCodeBackend().generate(request())
    assert "--thinking" not in cli.argv


async def test_claude_code_streams_deltas_and_still_parses_its_result(cli):
    chunks, sink = _sink()
    cli.script(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "system", "subtype": "init"},
                {"type": "stream_event",
                 "event": {"delta": {"type": "thinking_delta", "thinking": ""}}},
                {"type": "stream_event",
                 "event": {"delta": {"type": "text_delta", "text": "half "}}},
                {"type": "stream_event",
                 "event": {"delta": {"type": "text_delta", "text": "an answer"}}},
                {"type": "result", "is_error": False, "result": "half an answer",
                 "usage": {"input_tokens": 3, "output_tokens": 4}},
            ]
        )
    )
    result = await ClaudeCodeBackend().generate(request(on_chunk=sink))

    argv = cli.argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in argv
    # The result event carries the same fields the plain json format returns, so
    # switching format must not change what the caller gets back.
    assert result.text == "half an answer"
    assert result.usage.output_tokens == 4
    assert [c.text for c in chunks if c.kind == "text"] == ["half ", "an answer"]


async def test_claude_code_keeps_plain_json_when_nobody_is_streaming(cli):
    cli.script(json.dumps({"type": "result", "is_error": False, "result": "done"}))
    result = await ClaudeCodeBackend().generate(request())
    argv = cli.argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--include-partial-messages" not in argv
    assert result.text == "done"


async def test_copilot_streams_reasoning_and_message_deltas(cli):
    chunks, sink = _sink()
    cli.script(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "assistant.reasoning_delta", "data": {"deltaContent": "weighing "}},
                {"type": "assistant.reasoning_delta", "data": {"deltaContent": "options"}},
                {"type": "assistant.message_delta", "data": {"deltaContent": "one "}},
                {"type": "assistant.message_delta", "data": {"deltaContent": "two"}},
                {"type": "assistant.message", "data": {"content": "one two"}},
                # Repeats the whole summary that already streamed; ignored, or
                # the panel shows the reasoning twice.
                {"type": "assistant.reasoning", "data": {"content": "weighing options"}},
            ]
        )
    )
    result = await CopilotBackend().generate(request(on_chunk=sink))
    assert result.text == "one two"
    assert "--enable-reasoning-summaries" in cli.argv
    assert [(c.kind, c.text) for c in chunks] == [
        ("reasoning", "weighing "),
        ("reasoning", "options"),
        ("text", "one "),
        ("text", "two"),
    ]


async def test_copilot_asks_for_reasoning_only_when_someone_is_watching(cli):
    cli.script(json.dumps({"type": "assistant.message", "data": {"content": "hi"}}))
    await CopilotBackend().generate(request())
    assert "--enable-reasoning-summaries" not in cli.argv


async def test_claude_code_reports_withheld_thinking_as_a_step(cli):
    chunks, sink = _sink()
    # Verified against the CLI at --effort high: thinking deltas do arrive, and
    # every one carries an empty `thinking` with the content only present as an
    # encrypted signature. The token estimate is the one honest thing to show.
    cli.script(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "stream_event", "event": {"delta": {
                    "type": "thinking_delta", "thinking": "", "estimated_tokens": 50}}},
                {"type": "stream_event", "event": {"delta": {
                    "type": "thinking_delta", "thinking": "", "estimated_tokens": 100}}},
                {"type": "result", "is_error": False, "result": "done"},
            ]
        )
    )
    await ClaudeCodeBackend().generate(request(on_chunk=sink))

    # A step, not `reasoning` - putting a status line in a panel headed with the
    # model's name reads as something the model said.
    assert [c.kind for c in chunks] == ["step", "step"]
    assert "~150 tokens" in chunks[-1].text


async def test_a_huge_event_survives_the_streaming_reader(tmp_path):
    """The regression that broke a real run.

    These CLIs emit one JSON object per line, so a large answer is a single
    very long line. ``StreamReader.readline`` raises above its 64 KB limit and
    clears its buffer on the way out, which does not merely fail to deliver the
    event - it destroys it, and the captured stdout comes back short. The
    analyst hit this on a 223-evidence-item run.
    """
    import sys

    emitter = tmp_path / "emit.py"
    emitter.write_text(
        "import json, sys\n"
        "big = json.dumps({'type': 'text', 'part': {'text': 'x' * 200_000}})\n"
        "sys.stdout.write(big + chr(10))\n"
        "sys.stdout.write('{\"type\":\"step_finish\"}' + chr(10))\n",
        encoding="utf-8",
    )
    expected = json.dumps({"type": "text", "part": {"text": "x" * 200_000}})

    lines: list[str] = []
    result = await process.run([sys.executable, str(emitter)], on_line=lines.append)

    assert expected in result.stdout, "the captured output must not be truncated"
    assert lines[0] == expected, "the sink must see the whole event, not a fragment"
    assert len(lines) == 2

    # ...and the non-streaming path, which was never affected, still agrees.
    plain = await process.run([sys.executable, str(emitter)])
    assert plain.stdout == result.stdout


# ---------------------------------------------------------------------------
# Constrained decoding
#
# The schema is in the prompt for every backend. On an OpenAI-compatible server
# it can also be *enforced*, and these tests pin the difference: what goes on
# the wire, and what happens when the server will not take it.
# ---------------------------------------------------------------------------


@pytest.fixture
def litellm_api(monkeypatch):
    """Replace ``litellm.acompletion``; record kwargs, replay a scripted answer."""
    import litellm

    calls: list[dict] = []
    failures: list[Exception] = []

    async def fake_acompletion(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)
        if failures:
            raise failures.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    class Harness:
        def __init__(self) -> None:
            self.calls = calls

        def fail_first(self, exc: Exception) -> None:
            failures.append(exc)

    return Harness()


def _schema_request(**overrides):  # noqa: ANN003, ANN202
    base_kwargs = {
        "system": "s",
        "user": "u",
        "model": "openai/local",
        "json_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "schema_name": "Answer",
    }
    return BackendRequest(**{**base_kwargs, **overrides})


async def test_a_schema_is_enforced_not_merely_described(litellm_api):
    from sourcework.backends.litellm_backend import LiteLLMBackend

    await LiteLLMBackend().generate(_schema_request())

    sent = litellm_api.calls[0]["response_format"]
    assert sent["type"] == "json_schema"
    assert sent["json_schema"]["name"] == "Answer"
    assert sent["json_schema"]["schema"]["properties"] == {"ok": {"type": "boolean"}}
    # `strict` would make OpenAI reject every optional field the pipeline's
    # models legitimately have, and llama.cpp/vLLM constrain without it.
    assert "strict" not in sent["json_schema"]


async def test_no_schema_means_no_response_format_at_all(litellm_api):
    from sourcework.backends.litellm_backend import LiteLLMBackend

    await LiteLLMBackend().generate(_schema_request(json_schema=None, schema_name=None))

    assert "response_format" not in litellm_api.calls[0]


async def test_a_server_that_cannot_compile_the_schema_still_answers(litellm_api):
    """The prompt carries the schema too, so an unconstrained retry is a real
    answer rather than a lost call."""
    from sourcework.backends.litellm_backend import LiteLLMBackend

    litellm_api.fail_first(ValueError("Invalid schema for response_format"))
    result = await LiteLLMBackend().generate(_schema_request())

    assert result.text == '{"ok": true}'
    assert len(litellm_api.calls) == 2
    assert "response_format" not in litellm_api.calls[1]


async def test_an_unrelated_failure_does_not_get_a_second_chance(litellm_api):
    """Retrying a quota error without the schema only spends the wait twice."""
    from sourcework.backends.litellm_backend import LiteLLMBackend

    litellm_api.fail_first(RuntimeError("rate limit exceeded"))
    with pytest.raises(BackendQuotaError):
        await LiteLLMBackend().generate(_schema_request())

    assert len(litellm_api.calls) == 1


async def test_litellm_internal_retries_are_configurable(litellm_api):
    """Three attempts at a 20-minute local timeout is an hour of the same news."""
    from sourcework.backends import build

    cfg = LLMSettings(backend="litellm", default_model="openai/local", litellm_retries=0)
    await build("litellm", cfg).generate(_schema_request())

    assert litellm_api.calls[0]["num_retries"] == 0


async def test_structured_hands_the_schema_to_the_backend(monkeypatch):
    from sourcework import llm as llm_module

    seen: list[BackendRequest] = []

    class Recorder(base.LLMBackend):
        id = "litellm"
        supports_vision = True

        async def generate(self, req):  # noqa: ANN001, ANN201
            seen.append(req)
            return base.BackendResult(text='{"value": 1}')

    monkeypatch.setattr(llm_module, "build", lambda backend_id, cfg: Recorder())

    class Answer(pydantic.BaseModel):
        value: int

    cfg = LLMSettings(backend="litellm", default_model="m")
    assert (await llm_module.LLM(cfg=cfg).structured("s", "u", Answer)).value == 1

    assert seen[0].schema_name == "Answer"
    assert seen[0].json_schema["properties"] == {"value": {"title": "Value", "type": "integer"}}
    # ...and the prompt still describes it, for backends that cannot enforce.
    assert "JSON Schema" in seen[0].system

    seen.clear()
    off = LLMSettings(backend="litellm", default_model="m", constrained_json=False)
    await llm_module.LLM(cfg=off).structured("s", "u", Answer)
    assert seen[0].json_schema is None


def test_a_reasoning_trace_never_reaches_the_json_parser():
    """First-`{`-to-last-`}` is positional, and a model reasoning *about* a
    schema writes braces while it does so."""
    from sourcework.llm import _extract_json

    trace = '<think>Maybe {"value": 99} fits?</think>\n{"value": 1}'
    assert json.loads(_extract_json(trace)) == {"value": 1}

    # The truncated case: the opening tag was never streamed, the closing one was.
    cut = 'weighing {"value": 99}</think>{"value": 1}'
    assert json.loads(_extract_json(cut)) == {"value": 1}

    # A body that merely mentions the word is not a trace.
    assert json.loads(_extract_json('{"value": 1, "note": "think about it"}')) == {
        "value": 1, "note": "think about it"
    }


def test_the_critic_can_be_a_different_family_from_the_writer():
    """The point of the role: a critic trained alongside the writer finds the
    same phrasing natural, so it confirms rather than challenges."""
    cfg = LLMSettings(
        backend="litellm",
        default_model="openai/qwen3.5-9b",
        reasoning_model="openai/qwen3.6-27b",
        critic_model="openai/gemma-4-12b",
    )
    assert cfg.model_for("reasoning") == "openai/qwen3.6-27b"
    assert cfg.model_for("critic") == "openai/gemma-4-12b"


def test_an_unset_critic_reviews_at_the_reasoning_model_not_the_cheap_one():
    """Reviewing a PRD is the same weight of work as writing one. Falling back
    to `default` would make the review weaker than the thing it reviews."""
    cfg = LLMSettings(
        backend="litellm",
        default_model="openai/small",
        reasoning_model="openai/big",
    )
    assert cfg.model_for("critic") == "openai/big"

    # Same rule for a CLI backend, where the models live in a sub-model.
    cli = LLMSettings(backend="claude-code")
    cli = cli.model_copy(update={
        "claude_code_models": cli.claude_code_models.model_copy(
            update={"default": "haiku", "reasoning": "opus"}
        )
    })
    assert cli.model_for("critic") == "opus"


def test_a_local_endpoint_offers_the_models_it_actually_serves(monkeypatch):
    import httpx

    from sourcework.backends.litellm_backend import LiteLLMBackend

    seen: dict = {}

    def fake_get(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": "qwen3.5-9b"}, {"id": "gemma-4-12b"}]},
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    models = LiteLLMBackend(api_base="http://127.0.0.1:8081/v1", api_key="local").list_models()

    assert seen["url"] == "http://127.0.0.1:8081/v1/models"
    assert seen["headers"] == {"Authorization": "Bearer local"}
    # Prefixed, because a bare id copied out of this picker routes at OpenAI
    # rather than at api_base and fails the moment it is saved.
    assert models == ["openai/gemma-4-12b", "openai/qwen3.5-9b"]


def test_a_hosted_provider_is_never_asked_for_its_catalogue(monkeypatch):
    """Hundreds of ids the account may not be entitled to, and a round trip to
    someone else's server every time the settings page opens."""
    import httpx

    from sourcework.backends.litellm_backend import LiteLLMBackend

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("no network call without an explicit api_base")

    monkeypatch.setattr(httpx, "get", explode)
    assert LiteLLMBackend().list_models() == []


def test_the_settings_page_still_renders_when_the_model_server_is_down(monkeypatch):
    import httpx

    from sourcework.backends.litellm_backend import LiteLLMBackend

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", refuse)
    assert LiteLLMBackend(api_base="http://127.0.0.1:8081/v1").list_models() == []
