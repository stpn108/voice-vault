"""Scripted stand-in for the Claude API client in the analysis tests (REQ-007)."""
import itertools

from llm_client import LlmError, ToolCall, Turn

_ids = itertools.count(1)


def tool_turn(*calls, model="claude-opus-5-5"):
    """A turn that asks for the given (name, input) tool calls."""
    tool_calls = [ToolCall(f"toolu_{next(_ids)}", name, arguments) for name, arguments in calls]
    content = [{"type": "thinking", "thinking": "", "signature": "sig"}] + [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tool_calls]
    return _turn(content, "tool_use", model, tool_calls)


def end_turn(text="Done.", stop_reason="end_turn", model="claude-opus-5-5"):
    return _turn([{"type": "text", "text": text}], stop_reason, model, [])


def _turn(content, stop_reason, model, tool_calls):
    return Turn(content=content, stop_reason=stop_reason, model=model, request_id=f"req_{next(_ids)}",
                latency_ms=1200, input_tokens=900, output_tokens=120, cache_read_tokens=5000,
                cache_write_tokens=300, request_hash="a" * 64, response_hash="b" * 64, tool_calls=tool_calls)


class ScriptedLlm:
    """Answers with the scripted turns in order; an LlmError in the script is raised instead."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def turn(self, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        step = self.script.pop(0) if self.script else tool_turn(("list_topics", {}))
        if isinstance(step, LlmError):
            raise step
        return step
