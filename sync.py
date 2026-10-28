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

import sheets  # exportador opcional p/ Google Sheets (no-op se nao configurado)

# --- Config -----------------------------------------------------------------
# Base real (produção), confirmada ao vivo: a API fica em www.gymrats.app sob /api.
BASE_URL = os.environ.get("GYMRATS_BASE_URL", "https://www.gymrats.app/api")
EMAIL = os.environ.get("GYMRATS_EMAIL", "")
# Token JWT extraído do app (ver README). Prioridade sobre login por e-mail/senha.
ENV_TOKEN = os.environ.get("GYMRATS_TOKEN", "")
HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("GYMRATS_DB") or HERE / "gymrats.db")
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
CREATE TABLE IF NOT EXISTS sync_meta (
  challenge_id INTEGER PRIMARY KEY, last_full TEXT);
"""


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    # WAL: a API (outro processo) continua lendo enquanto o sync grava.
    con.execute("PRAGMA journal_mode=WAL")
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
# A lista de workouts e ordenada por occurred_at, e o app permite backdatar
# (strict_mode off) ate ~2 semanas. Entao um check-in retroativo entra no MEIO da
# lista e o incremental (que para no topo ja conhecido) nao o alcanca. Solucao:
# reconciliacao completa periodica do desafio ativo, que pega qualquer retroativo.
FULL_INTERVAL_H = float(os.environ.get("GYMRATS_FULL_INTERVAL_HOURS", "6"))
# Carrega (nao pula) desafios ate GRACE_DAYS apos o fim, p/ pegar backdates finais.
GRACE_DAYS = int(os.environ.get("GYMRATS_ENDED_GRACE_DAYS", "3"))


def _get_last_full(con, cid) -> Optional[datetime]:
    row = con.execute("SELECT last_full FROM sync_meta WHERE challenge_id=?", (cid,)).fetchone()
    if row and row[0]:
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None
    return None


def _set_last_full(con, cid, when: datetime) -> None:
    con.execute(
        "INSERT INTO sync_meta(challenge_id,last_full) VALUES(?,?) "
        "ON CONFLICT(challenge_id) DO UPDATE SET last_full=excluded.last_full",
        (cid, when.isoformat()))


def _full_workouts(api: Api, con, cid, c) -> None:
    """Carga completa: cursor de data recente + varredura por ?page (pega retroativos)."""
    cursor = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    start_date = str(c.get("start_date") or "")[:10]
    for _ in range(12):
        batch = as_list(api.get(f"/challenges/{cid}/workouts", {"date": cursor}))
        if not batch:
            break
        for w in batch:
            upsert_workout(con, cid, w)
        oldest = min((w.get("occurred_at", "") or "")[:10] for w in batch)
        if not oldest or (oldest <= cursor and len(batch) < 20):
            break
        nxt = oldest if oldest != cursor else (
            datetime.fromisoformat(oldest).date() - timedelta(days=1)).isoformat()
        cursor = nxt
        if start_date and cursor < start_date:
            break
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


def _incremental_workouts(api: Api, con, cid, c, known: set, today) -> None:
    """Incremental: o ?page anda pra tras de forma confiavel (o ?date 'gruda' no topo).
    1) uma chamada ?date=amanha pega o dia corrente (que o ?page ancora em ontem ignora);
    2) pagina do mais novo p/ tras e PARA na 1a pagina 100% ja conhecida.
    Tipicamente 2-4 requests por sync."""
    # (1) dia de hoje
    for w in as_list(api.get(f"/challenges/{cid}/workouts",
                             {"date": (today + timedelta(days=1)).isoformat()})):
        upsert_workout(con, cid, w)
    # (2) paginas recentes ate alcancar o historico ja salvo
    page = 1
    while page <= 200:
        batch = as_list(api.get(f"/challenges/{cid}/workouts", {"page": page}))
        if not batch:
            break
        new_in_batch = sum(1 for w in batch if _id(w) not in known)
        for w in batch:
            upsert_workout(con, cid, w)       # re-upsert tambem atualiza reacoes/contagens
        if new_in_batch == 0:                 # pagina inteira ja conhecida -> alcancamos
            break
        if len(batch) < 20:
            break
        page += 1


def sync(api: Api, force_full: bool = False):
    con = db()
    now = datetime.now(timezone.utc)
    today = now.date()
    challenges = as_list(api.get("/challenges/"))
    log(f"{len(challenges)} desafio(s)/clube(s) encontrados.")
    for c in challenges:
        cid = _id(c)
        # o item da lista ja traz name, rules, score_by, datas — nao busca detalhe.
        upsert_challenge(con, c)

        known = {r[0] for r in con.execute(
            "SELECT id FROM workouts WHERE challenge_id=?", (cid,)).fetchall()}
        end = str(c.get("end_date") or "")[:10]
        # So congela depois de GRACE_DAYS alem do fim (pega backdates finais).
        frozen = bool(end) and end < (today - timedelta(days=GRACE_DAYS)).isoformat()

        if frozen and known and not force_full:
            log(f"  desafio {cid} '{c.get('name')}': encerrado/ja sincronizado — pulado.")
            continue

        # membros e ranking (baratos — 2 requests)
        for m in as_list(api.get(f"/challenges/{cid}/members")):
            upsert_member(con, cid, m)
        for r in as_list(api.get(f"/challenges/{cid}/rankings")):
            upsert_ranking(con, cid, r)

        before = len(known)
        last_full = _get_last_full(con, cid)
        due_full = (last_full is None
                    or (now - last_full) >= timedelta(hours=FULL_INTERVAL_H))
        if force_full or not known or due_full:
            # Reconciliacao completa: pega check-ins retroativos (lista e por occurred_at).
            _full_workouts(api, con, cid, c)
            _set_last_full(con, cid, now)
            mode = "completo"
        else:
            # Barato: topo + paginas ate alcancar o conhecido.
            _incremental_workouts(api, con, cid, c, known, today)
            mode = "incremental"

        con.commit()
        total = len(con.execute(
            "SELECT 1 FROM workouts WHERE challenge_id=?", (cid,)).fetchall())
        log(f"  desafio {cid} '{c.get('name')}': {total} check-in(s) "
            f"(+{total-before} novos) [{mode}].")
        # Espelha os check-ins do desafio ativo numa planilha (se configurado).
        sheets.export_checkins(con, c, log=log)
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
    ap.add_argument("--full", action="store_true",
                    help="forca reconciliacao completa de todos os desafios (incl. encerrados)")
    args = ap.parse_args()
    api = Api()
    log(f"Base: {BASE_URL} | token em cache: {mask(api.token)}")
    if args.dump:
        dump(api)
    else:
        sync(api, force_full=args.full)


if __name__ == "__main__":
    main()
