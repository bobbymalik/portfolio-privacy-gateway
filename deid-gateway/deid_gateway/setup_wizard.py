"""Guided setup for the de-id portfolio gateway (macOS + Windows).

One command takes a new user from a fresh install to a working gateway:
  * generates a strong gateway secret,
  * collects their tastytrade read-only OAuth credentials (hidden input),
  * stores everything in the OS keychain,
  * registers the gateway in claude_desktop_config.json (with a backup),
  * verifies the engine binary and reference data are present.

Run:  deid-gateway-setup
"""

from __future__ import annotations

import getpass
import json
import os
import secrets as _secrets
import shutil
import sys
import time

SERVICE = "deid-gateway"
SERVER_NAME = "deid-portfolio-gateway"


def _config_path() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")
    return os.path.expanduser("~/.config/Claude/claude_desktop_config.json")


def _banner(msg: str) -> None:
    print("\n" + "=" * 64 + f"\n{msg}\n" + "=" * 64)


def main() -> None:
    _banner("De-identification Portfolio Gateway — setup")
    print("This stores credentials in your OS keychain and registers the gateway\n"
          "with Claude Desktop. Your broker credentials never leave your machine.\n")

    try:
        import keyring
    except ImportError:
        sys.exit("keyring is required: pip install keyring")

    # 1. health check: engine binary + reference data
    try:
        from deid_gateway import local_engine, refdata
        n = len(refdata.ReferenceData()._tickers)
        print(f"  engine binary:  {'OK' if local_engine.AVAILABLE else 'MISSING (build it first)'}")
        print(f"  reference data: {n} tickers loaded")
    except Exception as e:
        print(f"  warning: could not import gateway package ({e})")

    # 2. gateway secret (auto-generated, used for tokenization + audit-log key)
    if keyring.get_password(SERVICE, "DEID_GATEWAY_SECRET"):
        print("\n  DEID_GATEWAY_SECRET already set — keeping existing value.")
    else:
        keyring.set_password(SERVICE, "DEID_GATEWAY_SECRET", _secrets.token_urlsafe(32))
        print("\n  generated a strong DEID_GATEWAY_SECRET and stored it in the keychain.")

    # 3. broker choice + credentials
    _banner("Broker")
    choice = input("Which broker(s)? [1] tastytrade  [2] SnapTrade  "
                   "[3] both  (default 1): ").strip()
    broker = {"2": "snaptrade", "3": "multi"}.get(choice, "tastytrade")

    # Check the broker SDK is importable now, so a missing extra surfaces here
    # (with the exact fix) instead of silently skipping the broker at pull time.
    import importlib.util
    missing = []
    if broker in ("tastytrade", "multi") and importlib.util.find_spec("tastytrade") is None:
        missing.append(("tastytrade", "tastytrade"))
    # SnapTrade's free Personal path needs no SDK; only warn for it if you'll use a
    # Commercial key (userId/userSecret). We can't know yet, so we don't hard-warn.
    if missing:
        print("\n  NOTE: these broker SDK(s) aren't installed yet, so that broker "
              "won't load until you add them:")
        for extra, _ in missing:
            print(f"    pip install -e \".[{extra}]\"")
        print("  You can finish setup now and install them before restarting Claude.\n")

    def _prompt(prompts):
        for key, label in prompts:
            val = getpass.getpass(f"  {key} ({label}): ").strip()
            if val:
                keyring.set_password(SERVICE, key, val)
                print(f"    stored {key}")
            elif keyring.get_password(SERVICE, key):
                print(f"    keeping existing {key}")
            else:
                print(f"    (left blank — set later with: deid-gateway-secrets set {key})")

    _TT = (("TT_SECRET", "client secret"), ("TT_REFRESH", "refresh token"))
    _ST = (("SNAPTRADE_CLIENT_ID", "client id"),
           ("SNAPTRADE_CONSUMER_KEY", "consumer key"))

    if broker in ("tastytrade", "multi"):
        print("\ntastytrade (my.tastytrade.com -> My Profile -> API; request READ-ONLY):")
        _prompt(_TT)
    if broker in ("snaptrade", "multi"):
        print("\nSnapTrade Personal key (dashboard.snaptrade.com; 1 user, 20 connections):")
        _prompt(_ST)

    # 4. register with Claude Desktop
    _banner("Claude Desktop registration")
    cfg_path = _config_path()
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    cfg = {}
    if os.path.exists(cfg_path):
        shutil.copyfile(cfg_path, cfg_path + f".bak.{int(time.time())}")
        try:
            cfg = json.load(open(cfg_path))
        except Exception:
            print("  existing config wasn't valid JSON; starting fresh (backup kept).")
    cfg.setdefault("mcpServers", {})[SERVER_NAME] = {
        "command": sys.executable,
        "args": ["-m", "deid_gateway.server"],
        "env": {"DEID_BROKER": broker, "DEID_SECRETS_SOURCE": "keychain"},
    }
    json.dump(cfg, open(cfg_path, "w"), indent=2)
    print(f"  registered '{SERVER_NAME}' in {cfg_path} (broker: {broker})")
    if broker in ("snaptrade", "multi"):
        print("\n  SnapTrade (free Personal key): connect your brokerage(s) in the\n"
              "  SnapTrade dashboard (dashboard.snaptrade.com), then verify with:\n"
              "    deid-gateway-snaptrade accounts\n"
              "  No register/userSecret needed — the Personal key's user is implicit.")

    # 5. summary
    _banner("Done")
    print("Keychain status:")
    keys = ["DEID_GATEWAY_SECRET"]
    if broker in ("tastytrade", "multi"):
        keys += ["TT_SECRET", "TT_REFRESH"]
    if broker in ("snaptrade", "multi"):
        keys += ["SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY",
                 "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET"]
    for key in keys:
        present = keyring.get_password(SERVICE, key) is not None
        print(f"  {'set   ' if present else 'MISSING'}  {key}")
    print("\nNext: fully quit Claude Desktop (Cmd+Q / right-click Quit), reopen it,\n"
          "then start a NEW chat and ask it to run a risk assessment.\n")


if __name__ == "__main__":
    main()
