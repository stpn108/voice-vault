"""Shared helpers of the MCP tests."""
import dataclasses

from config import load_config

TOKEN = "t" * 40
OTHER_TOKEN = "o" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_cfg(**overrides):
    base = dict(mcp_tokens=(TOKEN,), mcp_allowed_hosts=("mcp.example.org",),
                mcp_allowed_origins=("https://allowed.example",))
    base.update(overrides)
    return dataclasses.replace(load_config(), **base)


def rpc(client, method, params=None, id=1, headers=AUTH, **kwargs):
    message = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        message["params"] = params
    return client.post("/mcp", json=message, headers=headers, **kwargs)


def call(client, name, arguments=None):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})


def text_of(response):
    return response.json()["result"]["content"][0]["text"]
