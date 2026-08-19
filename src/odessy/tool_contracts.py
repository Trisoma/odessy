from __future__ import annotations

import json
from typing import Any


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
        },
        "required": ["command"],
    },
    "laptop.terminal.run": {
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "optional": True},
            "timeout": {"type": "integer", "optional": True},
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
    for name, schema in TOOL_REQUEST_SCHEMAS.items():
        lines.append(f"{name}:")
        lines.append(_render_tool_request_shape(name, schema))
    return "\n".join(lines)


def render_tool_contract(name: str) -> str:
    schema = TOOL_REQUEST_SCHEMAS.get(name)
    if schema is None:
        raise ValueError(f"Unknown tool contract requested: {name}")
    return "\n".join(
        [
            "Use this exact JSON shape.",
            f"{name}:",
            _render_tool_request_shape(name, schema),
        ]
    )


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
        if argument_name not in required:
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
