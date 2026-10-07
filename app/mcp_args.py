"""Argument checks and result shapes shared by the MCP tool modules (REQ-003, REQ-006)."""
from typing import Optional


class ToolArgumentError(ValueError):
    """The arguments do not match the tool's input schema (reported as JSON-RPC invalid params)."""


def int_arg(arguments: dict, name: str, default: Optional[int], low: int, high: int) -> Optional[int]:
    value = arguments.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolArgumentError(f"'{name}' must be an integer between {low} and {high}")
    return value


def str_arg(arguments: dict, name: str, max_chars: int, default: str = "") -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str) or len(value) > max_chars:
        raise ToolArgumentError(f"'{name}' must be a string of at most {max_chars} characters")
    return value


def bool_arg(arguments: dict, name: str, default: bool = False) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ToolArgumentError(f"'{name}' must be true or false")
    return value


def enum_arg(arguments: dict, name: str, choices: tuple, default: str) -> str:
    value = arguments.get(name, default)
    if value not in choices:
        raise ToolArgumentError(f"'{name}' must be one of: {', '.join(choices)}")
    return value


def text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}
