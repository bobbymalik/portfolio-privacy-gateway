"""SecretStore resolution: AWS Secrets Manager wins, env is the fallback."""

import pytest

from deid_gateway.secrets import SecretStore


def test_aws_value_wins_over_env(monkeypatch):
    monkeypatch.setenv("TT_SECRET", "from-env")
    fake = {"TT_SECRET": "from-aws", "TT_REFRESH": "refresh-aws"}
    store = SecretStore(secret_id="deid/creds", source="aws", _fetcher=lambda sid, region: fake)
    assert store.get("TT_SECRET") == "from-aws"          # Secrets Manager wins
    assert store.get("TT_REFRESH") == "refresh-aws"


def test_env_fallback_when_key_absent_in_aws(monkeypatch):
    monkeypatch.setenv("DEID_BROKER", "tastytrade")
    store = SecretStore(secret_id="deid/creds", source="aws", _fetcher=lambda sid, region: {"TT_SECRET": "x"})
    assert store.get("DEID_BROKER") == "tastytrade"      # not in AWS dict -> env
    assert store.get("MISSING", "default") == "default"


def test_env_only_source_without_secret_id(monkeypatch):
    monkeypatch.delenv("DEID_AWS_SECRET_ID", raising=False)
    monkeypatch.setenv("TT_SECRET", "env-secret")
    store = SecretStore()                                 # no secret id -> env source
    assert store.source == "env"
    assert store.get("TT_SECRET") == "env-secret"


def test_aws_source_requires_secret_id(monkeypatch):
    monkeypatch.delenv("DEID_AWS_SECRET_ID", raising=False)
    with pytest.raises(ValueError):
        SecretStore(source="aws")


def test_keychain_value_wins_over_env(monkeypatch):
    monkeypatch.setenv("TT_SECRET", "from-env")
    vault = {"TT_SECRET": "from-keychain"}
    store = SecretStore(source="keychain", _keyring_get=vault.get)
    assert store.source == "keychain"
    assert store.get("TT_SECRET") == "from-keychain"


def test_keychain_falls_back_to_env_when_absent(monkeypatch):
    monkeypatch.setenv("DEID_BROKER", "tastytrade")
    store = SecretStore(source="keychain", _keyring_get=lambda key: None)
    assert store.get("DEID_BROKER") == "tastytrade"
    assert store.get("MISSING", "default") == "default"
