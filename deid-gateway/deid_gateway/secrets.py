"""Secret resolution.

Pulls credentials from a secure store so they never sit in plaintext in the
Claude Desktop config. Three sources, selected by DEID_SECRETS_SOURCE:

  keychain  OS keychain via the `keyring` library -- macOS Keychain on Mac,
            Windows Credential Manager on Windows (one implementation, both OSes).
  aws       AWS Secrets Manager (one JSON secret of key/value pairs).
  env       Plain environment variables (fallback / quick tests only).

Resolution order for any key is: the selected source first, then the environment,
then the provided default. The config file holds only the source name and other
non-secret pointers.

Store credentials in the keychain with the bundled CLI (no plaintext, no shell
history -- values are prompted with hidden input):

    deid-gateway-secrets set TT_SECRET
    deid-gateway-secrets set TT_REFRESH
    deid-gateway-secrets set DEID_GATEWAY_SECRET
    deid-gateway-secrets check
"""

from __future__ import annotations

import json
import os
from typing import Callable

DEFAULT_SERVICE = "deid-gateway"
MANAGED_KEYS = ("DEID_GATEWAY_SECRET", "TT_SECRET", "TT_REFRESH",
                "SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY",
                "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET",
                "DEID_ENGINE_URL", "DEID_ENGINE_KEY_ID", "DEID_ENGINE_SECRET")


def _fetch_aws_secret(secret_id: str, region: str | None) -> dict:
    import boto3  # lazy: only needed when source == "aws"

    client = boto3.client("secretsmanager", region_name=region) if region else boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_id)
    raw = resp.get("SecretString")
    if raw is None:
        raise ValueError("secret has no SecretString (binary secrets are not supported)")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("secret JSON must be an object of key/value pairs")
    return data


class SecretStore:
    """Resolves keys from a secure source (keychain/aws), falling back to env."""

    def __init__(
        self,
        secret_id: str | None = None,
        region: str | None = None,
        source: str | None = None,
        service: str | None = None,
        _fetcher: Callable[[str, str | None], dict] | None = None,
        _keyring_get: Callable[[str], str | None] | None = None,
    ) -> None:
        self._secret_id = secret_id or os.environ.get("DEID_AWS_SECRET_ID")
        self._region = region or os.environ.get("DEID_AWS_REGION") or os.environ.get("AWS_REGION")
        self._service = service or os.environ.get("DEID_KEYCHAIN_SERVICE") or DEFAULT_SERVICE
        # Default: aws if a secret id is configured, otherwise env-only. Set
        # DEID_SECRETS_SOURCE=keychain to use the OS keychain.
        self._source = (source or os.environ.get("DEID_SECRETS_SOURCE")
                        or ("aws" if self._secret_id else "env")).lower()

        self._cache: dict = {}
        self._keyring_get = _keyring_get

        if self._source == "aws":
            if not self._secret_id:
                raise ValueError("DEID_SECRETS_SOURCE=aws requires DEID_AWS_SECRET_ID")
            self._cache = (_fetcher or _fetch_aws_secret)(self._secret_id, self._region)
        elif self._source == "keychain" and self._keyring_get is None:
            import keyring  # lazy: only needed when source == "keychain"
            self._keyring_get = lambda key: keyring.get_password(self._service, key)

    def get(self, key: str, default: str | None = None) -> str | None:
        """Selected source wins; otherwise the environment; otherwise default."""
        if self._source == "aws":
            val = self._cache.get(key)
            if val is not None:
                return str(val)
        elif self._source == "keychain":
            val = self._keyring_get(key)  # type: ignore[misc]
            if val is not None:
                return val
        return os.environ.get(key, default)

    @property
    def source(self) -> str:
        return self._source


def _cli() -> None:
    """Manage keychain entries: set / delete / check. Cross-platform via keyring."""
    import argparse
    import getpass

    import keyring

    service = os.environ.get("DEID_KEYCHAIN_SERVICE") or DEFAULT_SERVICE
    parser = argparse.ArgumentParser(prog="deid-gateway-secrets",
                                     description=f"Manage gateway secrets in the OS keychain (service: {service})")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set", help="store a secret (prompts for the value if omitted)")
    p_set.add_argument("key")
    p_set.add_argument("value", nargs="?")
    p_del = sub.add_parser("delete", help="remove a secret")
    p_del.add_argument("key")
    sub.add_parser("check", help="show which managed keys are present (values never printed)")
    args = parser.parse_args()

    if args.cmd == "set":
        value = args.value or getpass.getpass(f"Value for {args.key}: ")
        keyring.set_password(service, args.key, value)
        print(f"stored {args.key} in {service}")
    elif args.cmd == "delete":
        keyring.delete_password(service, args.key)
        print(f"deleted {args.key} from {service}")
    elif args.cmd == "check":
        for key in MANAGED_KEYS:
            present = keyring.get_password(service, key) is not None
            print(f"  {'set   ' if present else 'MISSING'}  {key}")
