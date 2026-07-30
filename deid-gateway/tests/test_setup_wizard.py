"""The wizard's MCP-client config generation.

The whole point of client_config() is that a user never types an interpreter
path, so the tests pin the shape clients actually consume and the fact that
`command` is a real, runnable interpreter rather than a placeholder.
"""

import json
import os
import sys

from deid_gateway import setup_wizard as sw


def test_command_is_this_interpreter_not_a_placeholder():
    entry = sw.client_config("tastytrade")["mcpServers"]["deid-portfolio-gateway"]
    assert entry["command"] == sys.executable
    assert os.path.isabs(entry["command"])
    assert os.path.exists(entry["command"])
    assert "PATH/TO" not in entry["command"]


def test_shape_matches_what_clients_expect():
    block = sw.client_config("multi")
    entry = block["mcpServers"]["deid-portfolio-gateway"]
    assert entry["args"] == ["-m", "deid_gateway.server"]
    assert entry["env"]["DEID_SECRETS_SOURCE"] == "keychain"
    # must survive a JSON round-trip -- clients read it from disk
    assert json.loads(json.dumps(block)) == block


def test_broker_choice_is_carried_into_env():
    for broker in ("tastytrade", "snaptrade", "multi"):
        entry = sw.client_config(broker)["mcpServers"]["deid-portfolio-gateway"]
        assert entry["env"]["DEID_BROKER"] == broker


def test_no_secrets_are_written_into_client_config():
    """Credentials live in the keychain; a config file is world-readable."""
    blob = json.dumps(sw.client_config("multi"))
    for leak in ("TT_SECRET", "TT_REFRESH", "SNAPTRADE_CONSUMER_KEY",
                 "DEID_GATEWAY_SECRET"):
        assert leak not in blob


def test_install_root_is_the_venv_parent():
    root = sw.install_root()
    assert os.path.isabs(root)
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        # running in a venv: mcp.json lands beside it, not inside it
        assert root == os.path.dirname(sys.prefix)
        assert not sw.install_root().startswith(sys.prefix + os.sep)
