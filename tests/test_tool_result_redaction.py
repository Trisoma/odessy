"""Every tool result is redacted before it reaches the model or a log.

`terminal.run` redacts at its own boundary and the Node tool server redacts its
captured output, but the other tools -- `time.now`, `calculator.eval`, and every
configured third-party MCP server -- returned whatever the server said. The
observation boundary in `agent_loop` is the one place all of them pass through,
so that is where the tests look.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from odessy.authority.policy import OdessyPolicy
from odessy.authority.redaction import REDACTED, redact_value
from odessy.models.fake_model import FakeModel
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.tools.mcp_server import build_odessy_mcp_server

SECRET = "sk-abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _agent() -> OdessyAgent:
    return OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=Path.cwd()),
        mcp_server=None,
    )


# -- the shared walk ---------------------------------------------------------


def test_redact_value_walks_nested_results_and_keeps_their_shape() -> None:
    result = {
        "status": "ok",
        "content": f"API_KEY={SECRET}",
        "matches": [{"path": "a.env", "text": f"TOKEN={SECRET}"}],
        "count": 1,
        "nothing_to_do": True,
    }

    redacted = redact_value(result)

    assert redacted["status"] == "ok"
    assert redacted["count"] == 1
    assert redacted["nothing_to_do"] is True
    assert SECRET not in json.dumps(redacted)
    assert REDACTED in redacted["content"]
    assert REDACTED in redacted["matches"][0]["text"]


def test_redact_value_does_not_rewrite_the_caller_s_object() -> None:
    result = {"content": f"API_KEY={SECRET}"}

    redact_value(result)

    assert result["content"] == f"API_KEY={SECRET}"


def test_redact_value_leaves_non_string_scalars_alone() -> None:
    assert redact_value({"exit_code": 0, "duration": 1.5, "timed_out": False}) == {
        "exit_code": 0,
        "duration": 1.5,
        "timed_out": False,
    }


# -- credential-named fields -------------------------------------------------


def test_a_credential_named_field_is_redacted_whatever_its_value_looks_like() -> None:
    """An opaque secret under a telling name has no token shape to detect.

    `redact_text` would leave `{"password": "correct-horse-battery-staple"}` alone:
    it is neither a `NAME=value` assignment nor a known token shape. The field name
    is the evidence, so the walk uses it.
    """

    result = {
        "password": "correct-horse-battery-staple",
        "api_key": "opaque-provider-credential",
        "nested": {"client_secret": "another-opaque-value"},
        "list": [{"token": "also-opaque"}],
        "path": ".env",
    }

    redacted = redact_value(result)

    assert redacted["password"] == REDACTED
    assert redacted["api_key"] == REDACTED
    assert redacted["nested"]["client_secret"] == REDACTED
    assert redacted["list"][0]["token"] == REDACTED
    # A non-credential field must survive, or the model cannot act on the result.
    assert redacted["path"] == ".env"


def test_a_credential_named_field_keeps_its_nested_structure() -> None:
    redacted = redact_value({"credentials": {"user": "alice", "pass": "hunter2"}})

    assert redacted == {"credentials": {"user": REDACTED, "pass": REDACTED}}


def test_an_empty_credential_value_stays_empty() -> None:
    assert redact_value({"password": ""}) == {"password": ""}


def test_structured_credentials_are_redacted_at_the_observation_boundary() -> None:
    result = SimpleNamespace(
        structured_content={
            "password": "correct-horse-battery-staple",
            "api_key": "opaque-provider-credential",
        }
    )

    observation = _agent()._extract_structured_result(result)

    assert observation == {"password": REDACTED, "api_key": REDACTED}


# -- the observation boundary ------------------------------------------------


def test_structured_content_is_redacted_before_it_becomes_an_observation() -> None:
    result = SimpleNamespace(
        structured_content={
            "path": ".env",
            "content": f"OPENAI_API_KEY={SECRET}",
            "nested": {"list": [f"PASSWORD=hunter2-{SECRET}"]},
        }
    )

    observation = _agent()._extract_structured_result(result)

    assert SECRET not in json.dumps(observation)
    assert REDACTED in observation["content"]
    assert REDACTED in observation["nested"]["list"][0]
    # The path is not a credential and must survive, or the model cannot act on it.
    assert observation["path"] == ".env"


def test_text_only_content_from_a_third_party_server_is_redacted() -> None:
    result = SimpleNamespace(content=[SimpleNamespace(text=f"token: {SECRET}")])

    observation = _agent()._extract_structured_result(result)

    assert observation["status"] == "ok"
    assert SECRET not in observation["content"]
    assert REDACTED in observation["content"]


def test_a_non_dict_structured_result_is_redacted_too() -> None:
    result = SimpleNamespace(structured_content=[f"API_KEY={SECRET}"])

    observation = _agent()._extract_structured_result(result)

    assert observation["status"] == "ok"
    assert SECRET not in json.dumps(observation)
    assert REDACTED in observation["result"][0]


# -- end to end --------------------------------------------------------------


@pytest.mark.anyio
async def test_a_tool_result_and_its_log_entry_never_carry_the_secret() -> None:
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=Path.cwd(), allowed_tools={"time.now"}),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(clock=lambda: f"TOKEN={SECRET}")),
    )

    response = await agent.run_turn("What time is it?")

    assert response.tool_result is not None
    assert SECRET not in json.dumps(response.tool_result)
    assert REDACTED in response.tool_result["iso_time"]

    observations = json.dumps([entry.detail for entry in response.log])
    assert SECRET not in observations
    assert SECRET not in response.final_text
