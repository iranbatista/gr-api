#!/usr/bin/env python3
"""Gerenciador de API keys da GymRats Personal API (multi-key).

Guarda apenas o **hash SHA-256** de cada chave num registro JSON — a chave em
texto so aparece uma vez, no momento em que e gerada. Serve tanto de biblioteca
(importado pelo api.py para validar) quanto de CLI.

CLI:
    python apikeys.py generate <nome>     # cria uma chave nova (mostra o segredo 1x)
    python apikeys.py list                # lista as chaves (sem segredo)
    python apikeys.py revoke <nome>       # desativa uma chave

Onde fica o registro (nesta ordem):
    1. env GYMRATS_API_KEYS_FILE
    2. ao lado do banco (GYMRATS_DB) -> em producao: /data/apikeys.json
    3. ./apikeys.json (ao lado deste arquivo)

O registro NAO e versionado (pasta data/ e apikeys.json estao no .gitignore).
Compat: a env GYMRATS_API_KEY (chave unica) continua valendo em paralelo.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone

KEY_PREFIX = "grk_"          # so para reconhecer "isto e uma chave da gr-api"
_PREFIX_SHOWN = 12           # quantos chars da chave guardamos so para exibir no list


def registry_path() -> str:
    """Caminho do registro JSON (ver docstring do modulo)."""
    env = os.environ.get("GYMRATS_API_KEYS_FILE")
    if env:
        return env
    db = os.environ.get("GYMRATS_DB")
    if db:
        return os.path.join(os.path.dirname(os.path.abspath(db)) or ".", "apikeys.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "apikeys.json")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load(path: str | None = None) -> dict:
    """Le o registro. Estrutura: {"keys": [ {name, hash, prefix, created_at, revoked_at} ]}."""
    path = path or registry_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"keys": []}
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        return {"keys": []}
    return data


def save(data: dict, path: str | None = None) -> None:
    path = path or registry_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)  # troca atomica: nunca deixa o registro pela metade
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def active_hashes(path: str | None = None) -> set[str]:
    """Hashes das chaves ativas (nao revogadas). Usado pelo api.py na validacao."""
    return {k["hash"] for k in load(path).get("keys", [])
            if isinstance(k, dict) and k.get("hash") and not k.get("revoked_at")}


def verify(presented: str, path: str | None = None) -> bool:
    """True se a chave apresentada bate com alguma chave ativa do registro."""
    if not presented:
        return False
    return _hash(presented) in active_hashes(path)


def generate(name: str, path: str | None = None) -> str:
    """Cria uma chave nova e guarda o hash. Devolve o segredo em texto (mostre 1x)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("nome obrigatorio")
    path = path or registry_path()
    data = load(path)
    for k in data["keys"]:
        if k.get("name") == name and not k.get("revoked_at"):
            raise ValueError(f"ja existe uma chave ativa chamada '{name}' (revogue antes de reusar o nome)")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    data["keys"].append({
        "name": name,
        "hash": _hash(key),
        "prefix": key[:_PREFIX_SHOWN],   # so para o list identificar visualmente
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "revoked_at": None,
    })
    save(data, path)
    return key


def revoke(name: str, path: str | None = None) -> int:
    """Revoga (desativa) todas as chaves ativas com esse nome. Devolve quantas revogou."""
    path = path or registry_path()
    data = load(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for k in data["keys"]:
        if k.get("name") == name and not k.get("revoked_at"):
            k["revoked_at"] = now
            n += 1
    if n:
        save(data, path)
    return n


def list_keys(path: str | None = None) -> list[dict]:
    return load(path).get("keys", [])


# ---------------- CLI ----------------
def _cmd_generate(args) -> int:
    try:
        key = generate(args.name)
    except ValueError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    print(f"Chave '{args.name}' criada. GUARDE AGORA — ela nao sera mostrada de novo:\n")
    print(f"    {key}\n")
    print(f"Registro: {registry_path()}")
    print("Entregue esta chave ao consumidor (header  X-API-Key: <chave>).")
    return 0


def _cmd_list(args) -> int:
    keys = list_keys()
    if not keys:
        print(f"Nenhuma chave no registro ({registry_path()}).")
        return 0
    print(f"{'NOME':<20} {'PREFIXO':<14} {'CRIADA EM':<22} STATUS")
    for k in keys:
        status = "revogada" if k.get("revoked_at") else "ativa"
        print(f"{(k.get('name') or '?'):<20} {(k.get('prefix') or '?'):<14} "
              f"{(k.get('created_at') or '?'):<22} {status}")
    return 0


def _cmd_revoke(args) -> int:
    n = revoke(args.name)
    if n:
        print(f"{n} chave(s) '{args.name}' revogada(s).")
        return 0
    print(f"Nenhuma chave ativa chamada '{args.name}'.", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Gerencia as API keys da GymRats Personal API.")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="cria uma chave nova (mostra o segredo 1x)")
    g.add_argument("name", help="nome do consumidor, ex.: app-react")
    g.set_defaults(func=_cmd_generate)
    ls = sub.add_parser("list", help="lista as chaves (sem segredo)")
    ls.set_defaults(func=_cmd_list)
    rv = sub.add_parser("revoke", help="desativa uma chave")
    rv.add_argument("name", help="nome da chave a revogar")
    rv.set_defaults(func=_cmd_revoke)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
