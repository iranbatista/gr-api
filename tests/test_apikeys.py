"""Testes do registro multi-key (apikeys.py) e da validacao no api.require_key."""
import hashlib
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apikeys  # noqa: E402


@pytest.fixture
def reg(tmp_path, monkeypatch):
    """Aponta o registro para um arquivo isolado por teste."""
    path = tmp_path / "apikeys.json"
    monkeypatch.setenv("GYMRATS_API_KEYS_FILE", str(path))
    return str(path)


def test_registry_path_prefers_env(reg):
    assert apikeys.registry_path() == reg


def test_registry_path_falls_back_to_db_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GYMRATS_API_KEYS_FILE", raising=False)
    monkeypatch.setenv("GYMRATS_DB", str(tmp_path / "sub" / "gymrats.db"))
    assert apikeys.registry_path() == str(tmp_path / "sub" / "apikeys.json")


def test_generate_returns_plaintext_and_stores_only_hash(reg):
    key = apikeys.generate("app-react")
    assert key.startswith("grk_")
    data = apikeys.load()
    entry = data["keys"][0]
    assert entry["name"] == "app-react"
    assert entry["hash"] == hashlib.sha256(key.encode()).hexdigest()
    assert key not in open(reg, encoding="utf-8").read()  # segredo nunca vai pro disco
    assert entry["revoked_at"] is None


def test_generate_rejects_duplicate_active_name(reg):
    apikeys.generate("dash")
    with pytest.raises(ValueError):
        apikeys.generate("dash")


def test_generate_allows_name_reuse_after_revoke(reg):
    apikeys.generate("dash")
    apikeys.revoke("dash")
    key2 = apikeys.generate("dash")  # nao deve levantar
    assert apikeys.verify(key2)


def test_verify_active_and_revoked(reg):
    key = apikeys.generate("x")
    assert apikeys.verify(key) is True
    assert apikeys.revoke("x") == 1
    assert apikeys.verify(key) is False
    assert apikeys.verify("grk_qualquer") is False
    assert apikeys.verify("") is False


def test_active_hashes_excludes_revoked(reg):
    k1 = apikeys.generate("a")
    k2 = apikeys.generate("b")
    apikeys.revoke("a")
    h = apikeys.active_hashes()
    assert hashlib.sha256(k2.encode()).hexdigest() in h
    assert hashlib.sha256(k1.encode()).hexdigest() not in h


def test_revoke_missing_returns_zero(reg):
    assert apikeys.revoke("naoexiste") == 0


def _fresh_api(monkeypatch, env_key=""):
    """api.py com GYMRATS_API_KEY ajustado (restaurado pelo monkeypatch, sem reload
    para nao vazar estado p/ outros testes) e cache de chaves invalidado."""
    import api
    monkeypatch.setattr(api, "API_KEY", env_key)
    monkeypatch.setattr(api, "_keys_cache", {"stamp": object(), "hashes": frozenset()})
    return api


def test_require_key_open_when_nothing_configured(reg, monkeypatch):
    api = _fresh_api(monkeypatch, env_key="")
    # registro vazio + sem env key -> aberto
    assert api.require_key(x_api_key=None) is None


def test_require_key_env_key(reg, monkeypatch):
    api = _fresh_api(monkeypatch, env_key="envkey-123")
    assert api.require_key(x_api_key="envkey-123") is None
    with pytest.raises(HTTPException) as e:
        api.require_key(x_api_key="errada")
    assert e.value.status_code == 401
    with pytest.raises(HTTPException):
        api.require_key(x_api_key=None)


def test_require_key_registry_and_revoke(reg, monkeypatch):
    key = apikeys.generate("app-react")
    api = _fresh_api(monkeypatch, env_key="")  # so o registro protege
    assert api.require_key(x_api_key=key) is None
    apikeys.revoke("app-react")  # muda o mtime -> cache do api invalida
    with pytest.raises(HTTPException) as e:
        api.require_key(x_api_key=key)
    assert e.value.status_code == 401


def test_require_key_fail_closed_when_all_revoked(reg, monkeypatch):
    """Revogar a ultima chave NAO pode abrir a API: registro presente trava tudo."""
    apikeys.generate("only")
    apikeys.revoke("only")  # registro existe, zero chaves ativas
    api = _fresh_api(monkeypatch, env_key="")
    with pytest.raises(HTTPException) as e:
        api.require_key(x_api_key=None)
    assert e.value.status_code == 401
    with pytest.raises(HTTPException):
        api.require_key(x_api_key="grk_qualquer")


def test_require_key_env_and_registry_coexist(reg, monkeypatch):
    key = apikeys.generate("app-react")
    api = _fresh_api(monkeypatch, env_key="envkey-123")
    assert api.require_key(x_api_key="envkey-123") is None  # chave unica
    assert api.require_key(x_api_key=key) is None            # chave do registro
    with pytest.raises(HTTPException):
        api.require_key(x_api_key="grk_naoexiste")


def test_cli_generate_list_revoke(reg, capsys):
    assert apikeys.main(["generate", "cli-key"]) == 0
    out = capsys.readouterr().out
    assert "grk_" in out and "cli-key" in out
    assert apikeys.main(["list"]) == 0
    assert "cli-key" in capsys.readouterr().out
    assert apikeys.main(["revoke", "cli-key"]) == 0
    assert "revogada" in capsys.readouterr().out
    assert apikeys.main(["revoke", "cli-key"]) == 1  # ja revogada
