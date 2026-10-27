#!/usr/bin/env python3
"""
GymRats -> SQLite sincronizador (protótipo mínimo, SÓ LEITURA).

Caminho de engenharia reversa: API REST Phoenix usada pelo app React Native.
Base: gym-rats-api-production.gigalixirapp.com  (confirme no 1º login).

Regras respeitadas:
  - Só leitura: apenas GET (+ POST /accounts/sign_in para autenticar).
  - Só os grupos de que VOCÊ participa (parte de GET /challenges).
  - Sem rajadas: 1 request/s, trata 429/Retry-After.
  - Segredos: senha no keyring do SO; JWT em cache fora do git; token MASCARADO nos logs.

Uso:
  # uma vez, guardar a senha no cofre do SO:
  python -c "import keyring; keyring.set_password('gymrats','SEU_EMAIL','SUA_SENHA')"
  set GYMRATS_EMAIL=SEU_EMAIL         (Windows cmd)   /   $env:GYMRATS_EMAIL="..."  (PowerShell)

  python sync.py --dump   # loga, lista desafios, salva JSON cru p/ validar campos [INF]
  python sync.py          # sync incremental -> gymrats.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    import keyring
except ImportError:
    keyring = None

# --- Config -----------------------------------------------------------------
# Base real (produção), confirmada ao vivo: a API fica em www.gymrats.app sob /api.
BASE_URL = os.environ.get("GYMRATS_BASE_URL", "https://www.gymrats.app/api")
EMAIL = os.environ.get("GYMRATS_EMAIL", "")
# Token JWT extraído do app (ver README). Prioridade sobre login por e-mail/senha.
ENV_TOKEN = os.environ.get("GYMRATS_TOKEN", "")
HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "gymrats.db"
TOKEN_CACHE = HERE / ".token.json"          # fora do git (.gitignore)
DUMP_DIR = HERE / "dumps"
MIN_INTERVAL_S = 1.0                          # sem rajadas
LOOKBACK_DAYS = 3                             # janela de segurança do incremental
TIMEOUT = 30
UA = "gymrats-personal-sync/0.1 (read-only)"

_last_req = 0.0


class TokenExpired(RuntimeError):
    """401 do backend: token inválido/expirado (não há refresh token)."""


# --- Util -------------------------------------------------------------------
def mask(tok: str | None) -> str:
    if not tok:
        return "<none>"
    return f"{tok[:6]}…{tok[-4:]} (len={len(tok)})" if len(tok) > 12 else "<short>"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _throttle() -> None:
    global _last_req
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_req)
    if wait > 0:
        time.sleep(wait)
    _last_req = time.monotonic()


# --- Auth -------------------------------------------------------------------
def get_password() -> str:
    if not EMAIL:
        sys.exit("Defina GYMRATS_EMAIL no ambiente.")
    if keyring is None:
        sys.exit("Instale 'keyring' (pip install -r requirements.txt).")
    pw = keyring.get_password("gymrats", EMAIL)
    if not pw:
        sys.exit(
            "Senha não encontrada no cofre. Rode uma vez:\n"
            "  python -c \"import keyring; keyring.set_password('gymrats','SEU_EMAIL','SUA_SENHA')\""
        )
    return pw


def login(session: requests.Session) -> str:
    """POST /accounts/sign_in -> JWT. Só é chamado quando não há token válido."""
    _throttle()
    log(f"Login em {BASE_URL} como {EMAIL} …")
    r = session.post(
        f"{BASE_URL}/accounts/sign_in",
        json={"email": EMAIL, "password": get_password()},
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    # [INF] token pode vir em data["token"] ou data["data"]["token"] — tenta ambos.
    tok = data.get("token") or (data.get("data") or {}).get("token")
    if not tok:
        raise RuntimeError(f"Login OK mas não achei 'token'. Chaves: {list(data)}")
    TOKEN_CACHE.write_text(json.dumps({"token": tok, "email": EMAIL}))
    try:
        os.chmod(TOKEN_CACHE, 0o600)
    except OSError:
        pass
    log(f"Token obtido e cacheado: {mask(tok)}")
    return tok


def load_token() -> str | None:
    # 1) token explícito no ambiente; 2) cache em disco.
    if ENV_TOKEN:
        return ENV_TOKEN
    if TOKEN_CACHE.exists():
        try:
            d = json.loads(TOKEN_CACHE.read_text())
            return d.get("token")
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_token(tok: str) -> None:
    TOKEN_CACHE.write_text(json.dumps({"token": tok}))
    try:
        os.chmod(TOKEN_CACHE, 0o600)
    except OSError:
        pass


# --- HTTP GET com auto re-login no 401 --------------------------------------
class Api:
    def __init__(self):
        self.s = requests.Session()
        self.token = load_token()

    def _headers(self) -> dict:
        # [OBS] header 'Authorization' com token CRU (sem 'Bearer ').
        return {"Authorization": self.token or "", "User-Agent": UA}

    def get(self, path: str, params: dict | None = None, _retried=False):
        if not self.token:
            raise RuntimeError("Sem token. Cole seu JWT do GymRats (ver README).")
        _throttle()
        url = f"{BASE_URL}{path}"
        r = self.s.get(url, headers=self._headers(), params=params, timeout=TIMEOUT)
        if r.status_code == 401:
            # Token expirou/inválido. Não há refresh token: é preciso um token novo.
            raise TokenExpired("401: token inválido ou expirado — extraia um novo do app.")
        if r.status_code == 429:
            retry = int(r.headers.get("Retry-After", "5"))
            log(f"429 rate limit — aguardando {retry}s.")
            time.sleep(retry)
            return self.get(path, params, _retried=_retried)
        r.raise_for_status()
        return r.json()


# --- SQLite -----------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS challenges (
  id INTEGER PRIMARY KEY, name TEXT, raw JSON, synced_at TEXT);
CREATE TABLE IF NOT EXISTS members (
  challenge_id INTEGER, id INTEGER, name TEXT, raw JSON, synced_at TEXT,
  PRIMARY KEY (challenge_id, id));
CREATE TABLE IF NOT EXISTS workouts (
  id INTEGER PRIMARY KEY, challenge_id INTEGER, account_id INTEGER,
  occurred_on TEXT, raw JSON, synced_at TEXT);
CREATE TABLE IF NOT EXISTS rankings (
  challenge_id INTEGER, account_id INTEGER, rank INTEGER, points REAL,
  raw JSON, synced_at TEXT, PRIMARY KEY (challenge_id, account_id));
CREATE TABLE IF NOT EXISTS sync_state (
  challenge_id INTEGER PRIMARY KEY, last_date TEXT);
"""


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def _id(d: dict):
    return d.get("id") or d.get("challenge_id") or d.get("workout_id")


def upsert_challenge(con, c):
    con.execute(
        "INSERT INTO challenges(id,name,raw,synced_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name,raw=excluded.raw,synced_at=excluded.synced_at",
        (_id(c), c.get("name"), json.dumps(c), datetime.now(timezone.utc).isoformat()),
    )


def upsert_member(con, cid, m):
    con.execute(
        "INSERT INTO members(challenge_id,id,name,raw,synced_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(challenge_id,id) DO UPDATE SET name=excluded.name,raw=excluded.raw,synced_at=excluded.synced_at",
        (cid, _id(m), m.get("full_name") or m.get("name"), json.dumps(m),
         datetime.now(timezone.utc).isoformat()),
    )


def upsert_workout(con, cid, w):
    # campos reais: occurred_at, gym_rats_user_id, account{...}
    acc = w.get("gym_rats_user_id") or (w.get("account") or {}).get("id")
    con.execute(
        "INSERT INTO workouts(id,challenge_id,account_id,occurred_on,raw,synced_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "raw=excluded.raw,occurred_on=excluded.occurred_on,synced_at=excluded.synced_at",
        (_id(w), cid, acc,
         w.get("occurred_at") or w.get("created_at"),
         json.dumps(w), datetime.now(timezone.utc).isoformat()),
    )


def upsert_ranking(con, cid, r):
    # campos reais: account{...}, rank, score
    acc = (r.get("account") or {}).get("id") or r.get("account_id")
    con.execute(
        "INSERT INTO rankings(challenge_id,account_id,rank,points,raw,synced_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(challenge_id,account_id) DO UPDATE SET "
        "rank=excluded.rank,points=excluded.points,raw=excluded.raw,synced_at=excluded.synced_at",
        (cid, acc, r.get("rank"),
         r.get("score"), json.dumps(r),
         datetime.now(timezone.utc).isoformat()),
    )


def as_list(payload):
    """Backend pode devolver [...] ou {"data":[...]}; normaliza."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "challenges", "members", "workouts", "rankings", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


# --- Sync -------------------------------------------------------------------
def sync(api: Api):
    con = db()
    challenges = as_list(api.get("/challenges/"))
    log(f"{len(challenges)} desafio(s)/clube(s) encontrados.")
    for c in challenges:
        cid = _id(c)
        # o item da lista ja traz name, rules, score_by, datas — nao busca detalhe
        # (GET /challenges/:id retorna outra entidade e apagava o nome).
        upsert_challenge(con, c)

        for m in as_list(api.get(f"/challenges/{cid}/members")):
            upsert_member(con, cid, m)

        for r in as_list(api.get(f"/challenges/{cid}/rankings")):
            upsert_ranking(con, cid, r)

        # check-ins (workouts) — GET /challenges/:id/workouts
        # A API tem DUAS facetas (mutuamente exclusivas):
        #  - ?page=N : pagina o historico, mas ancora no "hoje" do servidor (= ontem p/ nos),
        #              entao NAO inclui o dia corrente.
        #  - ?date=X : devolve os 20 mais novos <= dia X (cursor por dia), inclui o dia corrente.
        # Estrategia: cursor por data a partir de amanha p/ pegar os dias recentes,
        # depois paginacao por ?page p/ varrer todo o historico. UPSERT deduplica.
        before = len(con.execute(
            "SELECT 1 FROM workouts WHERE challenge_id=?", (cid,)).fetchall())

        # (A) dias recentes via cursor de data (pega o dia de hoje que o ?page ignora)
        cursor = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        for _ in range(8):
            batch = as_list(api.get(f"/challenges/{cid}/workouts", {"date": cursor}))
            if not batch:
                break
            for w in batch:
                upsert_workout(con, cid, w)
            oldest = min((w.get("occurred_at", "") or "")[:10] for w in batch)
            if not oldest or oldest <= cursor and len(batch) < 20:
                break
            nxt = oldest
            if nxt == cursor:            # dia cheio (>20): recua 1 dia p/ nao travar
                nxt = (datetime.fromisoformat(oldest).date() - timedelta(days=1)).isoformat()
            cursor = nxt
            if cursor < str(c.get("start_date", ""))[:10]:
                break

        # (B) historico completo via ?page
        seen_first, page = None, 1
        while page <= 200:
            batch = as_list(api.get(f"/challenges/{cid}/workouts", {"page": page}))
            if not batch:
                break
            first_id = _id(batch[0])
            if first_id == seen_first:
                break
            seen_first = first_id
            for w in batch:
                upsert_workout(con, cid, w)
            if len(batch) < 20:
                break
            page += 1

        con.commit()
        total = len(con.execute(
            "SELECT 1 FROM workouts WHERE challenge_id=?", (cid,)).fetchall())
        log(f"  desafio {cid} '{c.get('name')}': {total} check-in(s) (+{total-before} novos).")
    con.close()
    log(f"Pronto. Banco: {DB_PATH}")


def dump(api: Api):
    """Salva JSON cru p/ validar os nomes de campos [INF]."""
    DUMP_DIR.mkdir(exist_ok=True)
    chs = api.get("/challenges/")
    (DUMP_DIR / "challenges.json").write_text(json.dumps(chs, indent=2, ensure_ascii=False))
    log(f"challenges.json salvo. ({len(as_list(chs))} itens)")
    first = as_list(chs)
    if first:
        cid = _id(first[0])
        for name, path in [
            ("challenge_detail", f"/challenges/{cid}"),
            ("members", f"/challenges/{cid}/members"),
            ("rankings", f"/challenges/{cid}/rankings"),
            ("feed", f"/challenges/{cid}/feed"),
        ]:
            try:
                (DUMP_DIR / f"{name}.json").write_text(
                    json.dumps(api.get(path), indent=2, ensure_ascii=False))
                log(f"{name}.json salvo.")
            except requests.HTTPError as e:
                log(f"{name}: {e}")
    log(f"Confira os JSON em {DUMP_DIR} e ajuste o mapeamento se algum campo diferir.")


def main():
    ap = argparse.ArgumentParser(description="GymRats -> SQLite (read-only).")
    ap.add_argument("--dump", action="store_true", help="salva JSON cru p/ inspeção")
    args = ap.parse_args()
    api = Api()
    log(f"Base: {BASE_URL} | token em cache: {mask(api.token)}")
    if args.dump:
        dump(api)
    else:
        sync(api)


if __name__ == "__main__":
    main()
