from __future__ import annotations

import json
from typing import Any

from odessy.deployment import deployment_mode, model_tool_names, tool_is_available
from odessy.execution.contract import ESCALATION_TARGETS

#: Spec keys that describe Odessy's own rendering rather than the JSON Schema, and so
#: must not be forwarded to a model API verbatim.
_NON_SCHEMA_KEYS = frozenset({"optional", "model_requested"})


TOOL_REQUEST_SCHEMAS: dict[str, dict[str, Any]] = {
    "time.now": {
        "properties": {},
        "required": [],
    },
    "terminal.run": {
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "optional": True},
            "timeout": {"type": "integer", "optional": True},
            "sandbox_permissions": {
                "type": "string",
                "enum": list(ESCALATION_TARGETS),
                "optional": True,
                "model_requested": True,
                "description": (
                    "Request a wider sandbox mode for this one call. Must be strictly "
                    "wider than the mode the command would otherwise run under, and "
                    "must be accompanied by a justification. The user is asked to approve."
                ),
            },
            "justification": {
                "type": "string",
                "optional": True,
                "model_requested": True,
                "description": (
                    "One sentence explaining why the wider mode is needed. Only valid "
                    "together with sandbox_permissions."
                ),
            },
        },
        "required": ["command"],
    },
    "laptop.terminal.run": {
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "optional": True},
            "timeout": {"type": "integer", "optional": True},
            "sandbox_permissions": {
                "type": "string",
                "enum": list(ESCALATION_TARGETS),
                "optional": True,
                "model_requested": True,
                "description": (
                    "Request a wider sandbox mode for this one call. Must be strictly "
                    "wider than the mode the command would otherwise run under, and "
                    "must be accompanied by a justification. The user is asked to approve."
                ),
            },
            "justification": {
                "type": "string",
                "optional": True,
                "model_requested": True,
                "description": (
                    "One sentence explaining why the wider mode is needed. Only valid "
                    "together with sandbox_permissions."
                ),
            },
        },
        "required": ["command"],
    },
    "filesystem.write": {
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    "log.append": {
        "properties": {
            "content": {"type": "string"},
        },
        "required": ["content"],
    },
    "calculator.eval": {
        "properties": {
            "expression": {"type": "string"},
        },
        "required": ["expression"],
    },
    "filesystem.read": {
        "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer", "optional": True},
        },
        "required": ["path"],
    },
    "filesystem.list": {
        "properties": {
            "path": {"type": "string", "optional": True},
            "limit": {"type": "integer", "optional": True},
        },
        "required": [],
    },
    "project.find": {
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "optional": True},
            "limit": {"type": "integer", "optional": True},
        },
        "required": ["pattern"],
    },
    "project.grep": {
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "optional": True},
            "glob": {"type": "string", "optional": True},
            "ignore_case": {"type": "boolean", "optional": True},
            "literal": {"type": "boolean", "optional": True},
            "context": {"type": "integer", "optional": True},
            "limit": {"type": "integer", "optional": True},
        },
        "required": ["pattern"],
    },
    "git.status": {
        "properties": {},
        "required": [],
    },
    "git.diff": {
        "properties": {
            "mode": {"type": "string", "enum": ["working", "cached"], "optional": True},
        },
        "required": [],
    },
    "git.changed_files": {
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["working", "cached", "all"],
                "optional": True,
            },
        },
        "required": [],
    },
    "git.log": {
        "properties": {
            "limit": {"type": "integer", "optional": True},
        },
        "required": [],
    },
}


def render_tool_contracts() -> str:
    lines = [
        "Use only these exact JSON shapes.",
        "If a tool has no arguments, emit \"arguments\": {}.",
    ]
    for name in model_tool_names():
        lines.append(f"{name}:")
        lines.append(_render_tool_request_shape(name, TOOL_REQUEST_SCHEMAS[name]))
    return "\n".join(lines)


def render_tool_contract(name: str) -> str:
    schema = TOOL_REQUEST_SCHEMAS.get(name)
    if schema is None:
        raise ValueError(f"Unknown tool contract requested: {name}")
    if not tool_is_available(name):
        return render_tool_contracts()
    return "\n".join(
        [
            "Use this exact JSON shape.",
            f"{name}:",
            _render_tool_request_shape(name, schema),
        ]
    )


#: Appended to the command tools. In the first evaluation runs a small model answered
#: "I have no tool for that" to memory, disk and IP questions while a shell tool was
#: offered: "run a shell command" alone did not tell it what a shell is for.
_SHELL_USES = (
    " Use it for anything a shell command can answer when no other tool fits: processes, "
    "memory, disk space, network addresses and ports, environment variables, counting files."
)

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "time.now": "Return the current local time.",
    "terminal.run": "Run a shell command in the local workspace and return structured output." + _SHELL_USES,
    "laptop.terminal.run": "Run a shell command on the user's laptop (not the local machine)." + _SHELL_USES,
    "filesystem.write": "Write content to a file inside the workspace. Requires confirmation.",
    "log.append": "Append a line to the internal Odessy log. Destination is fixed and internal.",
    "calculator.eval": "Evaluate a side-effect-free arithmetic expression.",
    "filesystem.read": "Read a regular text file inside the workspace.",
    "filesystem.list": "List directory entries inside the workspace.",
    "project.find": "Find files by name inside the workspace. Does not look inside files.",
    "project.grep": "Search inside file contents in the workspace for text, such as a word or a function name.",
    "git.status": "Return git status output.",
    "git.diff": "Return git diff output.",
    "git.changed_files": "Return changed file names.",
    "git.log": "Return recent commit summaries.",
}

#: Descriptions that differ in ``single`` mode, where there is only one machine.
_SINGLE_MACHINE_DESCRIPTIONS: dict[str, str] = {
    "terminal.run": "Run a shell command on the user's computer and return structured output." + _SHELL_USES,
}


def tool_description(name: str) -> str:
    if deployment_mode() == "single" and name in _SINGLE_MACHINE_DESCRIPTIONS:
        return _SINGLE_MACHINE_DESCRIPTIONS[name]
    return _TOOL_DESCRIPTIONS.get(name, name)


def build_openai_tools(names: list[str] | None = None) -> list[dict[str, Any]]:
    """Render tool schemas in OpenAI function-calling format for Ollama's /api/chat.

    With no ``names``, renders the tools this deployment offers
    (``deployment.model_tool_names``), not every tool in the registry.
    """
    selected = names if names is not None else model_tool_names()
    tools: list[dict[str, Any]] = []
    for name in selected:
        if not tool_is_available(name):
            continue
        schema = TOOL_REQUEST_SCHEMAS.get(name)
        if schema is None:
            continue
        tools.append(_to_openai_tool(name, schema))
    return tools


def _to_openai_tool(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    for key, spec in schema["properties"].items():
        properties[key] = {k: v for k, v in spec.items() if k not in _NON_SCHEMA_KEYS}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": tool_description(name),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": schema["required"],
            },
        },
    }


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> None:
    schema = TOOL_REQUEST_SCHEMAS.get(name)
    if schema is None:
        raise ValueError(f"Unknown tool requested by model: {name}")
    if not isinstance(arguments, dict):
        raise ValueError(f"{name} arguments must be a JSON object")

    properties: dict[str, dict[str, Any]] = schema["properties"]
    required = set(schema["required"])

    missing = required.difference(arguments)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Missing required argument(s) for {name}: {names}")

    unexpected = set(arguments).difference(properties)
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ValueError(f"Unexpected argument(s) for {name}: {names}")

    for argument_name, argument_value in arguments.items():
        spec = properties[argument_name]
        _validate_argument_type(name, argument_name, argument_value, spec)


def _render_tool_request_shape(name: str, schema: dict[str, Any]) -> str:
    properties: dict[str, dict[str, Any]] = schema["properties"]
    required = set(schema["required"])
    arguments: dict[str, str] = {}
    for argument_name, spec in properties.items():
        label = _render_type_label(spec)
        if spec.get("model_requested"):
            # The model raises this one on its own initiative. The stock note below
            # would tell it to stay silent unless the user asked first, which would
            # make the mechanism unreachable.
            label = f"{label} (request this only when the command genuinely needs it)"
        elif argument_name not in required:
            label = f"{label} (omit unless the user explicitly specifies it)"
        arguments[argument_name] = label
    return json.dumps(
        {
            "type": "tool",
            "name": name,
            "arguments": arguments,
        },
        ensure_ascii=True,
    )


def _render_type_label(spec: dict[str, Any]) -> str:
    if "enum" in spec:
        values = " | ".join(f'"{value}"' for value in spec["enum"])
        return f"string ({values})"
    return spec["type"]


def _validate_argument_type(
    tool_name: str,
    argument_name: str,
    argument_value: Any,
    spec: dict[str, Any],
) -> None:
    expected_type = spec["type"]
    if expected_type == "string":
        if not isinstance(argument_value, str):
            raise ValueError(f"{tool_name}.{argument_name} must be a string")
    elif expected_type == "integer":
        if not isinstance(argument_value, int) or isinstance(argument_value, bool):
            raise ValueError(f"{tool_name}.{argument_name} must be an integer")
    elif expected_type == "boolean":
        if not isinstance(argument_value, bool):
            raise ValueError(f"{tool_name}.{argument_name} must be a boolean")
    elif expected_type == "number":
        if not isinstance(argument_value, (int, float)) or isinstance(argument_value, bool):
            raise ValueError(f"{tool_name}.{argument_name} must be a number")
    else:
        raise ValueError(f"Unsupported tool schema type: {expected_type}")

    if "enum" in spec and argument_value not in spec["enum"]:
        allowed = ", ".join(repr(value) for value in spec["enum"])
        raise ValueError(f"{tool_name}.{argument_name} must be one of: {allowed}")
