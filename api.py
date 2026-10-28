#!/usr/bin/env python3
"""
GymRats Personal REST API (FastAPI) — SOMENTE LEITURA.

Serve os dados ja sincronizados em gymrats.db (populado por sync.py) para um app
React consumir. Pronto para rodar num VPS atras de nginx.

Rodar local:
    pip install -r requirements-api.txt
    uvicorn api:app --reload --port 8000
    # docs interativas: http://127.0.0.1:8000/docs

Config por ambiente:
    GYMRATS_DB        caminho do SQLite (default: gymrats.db)
    GYMRATS_API_KEY   se definido, exige header  X-API-Key: <chave>  (recomendado no VPS)
    GYMRATS_CORS      origens permitidas separadas por virgula (default: *)
                      ex.: https://meuapp.com,https://www.meuapp.com
    GYMRATS_IMG_CACHE pasta do cache de miniaturas do /img
                      (default: /data/thumbs se /data existir, senao ./.thumbs)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

DB_PATH = os.environ.get("GYMRATS_DB", os.path.join(os.path.dirname(__file__), "gymrats.db"))
API_KEY = os.environ.get("GYMRATS_API_KEY", "")
# Fuso usado para agrupar check-ins por dia (by_day, active_days).
TZ = ZoneInfo(os.environ.get("GYMRATS_TZ", "America/Sao_Paulo"))
CORS_ORIGINS = [o.strip() for o in os.environ.get("GYMRATS_CORS", "*").split(",") if o.strip()]

# Comentarios sao buscados no GymRats sob demanda (nao sao sincronizados no banco).
GYMRATS_BASE = os.environ.get("GYMRATS_BASE_URL", "https://www.gymrats.app/api").rstrip("/")
TOKEN_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".token.json")
COMMENTS_TTL = float(os.environ.get("GYMRATS_COMMENTS_TTL", "60"))  # cache em segundos
_comments_cache: dict[int, tuple[float, list]] = {}

# Miniaturas (/img): so hosts/larguras da allowlist -> nao vira proxy aberto nem DoS de resize.
IMG_HOSTS = {"cdn.gymrats.app"}
IMG_WIDTHS = (64, 128, 256, 512, 1024)
IMG_MAX_BYTES = 15 * 1024 * 1024
IMG_CACHE_CONTROL = "public, max-age=31536000, immutable"
_img_sem = threading.Semaphore(4)  # no maximo 4 downloads/resizes simultaneos

app = FastAPI(
    title="GymRats Personal API",
    version="1.0.0",
    description="API pessoal (read-only) sobre os desafios do GymRats de que o dono participa.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------- infra ----------
def require_key(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and not secrets.compare_digest((x_api_key or "").encode(), API_KEY.encode()):
        raise HTTPException(status_code=401, detail="X-API-Key ausente ou invalida")


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _raw(row) -> dict:
    try:
        return json.loads(row["raw"]) if row["raw"] else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def num(v) -> float:
    """Coage p/ float (a API devolve numeros como string as vezes)."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def inum(v):
    return int(num(v)) if v is not None else None


def _gymrats_token() -> Optional[str]:
    """Token p/ o proxy de comentarios: env GYMRATS_TOKEN ou .token.json (local)."""
    t = os.environ.get("GYMRATS_TOKEN")
    if t:
        return t
    try:
        with open(TOKEN_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f).get("token")
    except (OSError, json.JSONDecodeError):
        return None


def _fetch_comments(wid: int) -> list:
    """Busca os comentarios de um check-in no GymRats, com cache curto em memoria."""
    now = time.time()
    hit = _comments_cache.get(wid)
    if hit and now - hit[0] < COMMENTS_TTL:
        return hit[1]
    token = _gymrats_token()
    if not token:
        raise HTTPException(503, "comentarios indisponiveis: defina GYMRATS_TOKEN na API")
    try:
        r = requests.get(f"{GYMRATS_BASE}/workouts/{wid}/comments",
                         headers={"Authorization": token, "User-Agent": "gr-api/1.0"},
                         timeout=15)
    except requests.RequestException as e:
        raise HTTPException(502, f"falha ao consultar o GymRats: {e}")
    if r.status_code == 401:
        raise HTTPException(502, "token do GymRats expirado/invalido")
    if r.status_code == 404:
        return []
    if not r.ok:
        raise HTTPException(502, f"GymRats respondeu {r.status_code}")
    data = r.json()
    items = (data.get("data") if isinstance(data, dict) else data) or []
    _comments_cache[wid] = (now, items)
    return items


# ---------- modelos (contrato p/ o React + OpenAPI) ----------
class Account(BaseModel):
    id: Optional[int] = None
    full_name: Optional[str] = None
    profile_picture_url: Optional[str] = None


class Challenge(BaseModel):
    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    group_type: Optional[str] = None
    score_by: Optional[str] = None
    teams_enabled: Optional[bool] = None
    profile_picture_url: Optional[str] = None
    member_count: Optional[int] = None
    synced_at: Optional[str] = None


class RankingEntry(BaseModel):
    rank: Optional[int] = None
    score: Optional[float] = None
    account: Account


class Member(BaseModel):
    id: Optional[int] = None
    full_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    instagram: Optional[str] = None
    tik_tok: Optional[str] = None
    twitter: Optional[str] = None


class Reaction(BaseModel):
    reaction: Optional[str] = None   # o emoji
    account: Account


class Comment(BaseModel):
    id: int
    workout_id: Optional[int] = None
    content: Optional[str] = None
    created_at: Optional[str] = None
    account: Account
    reactions: list[Reaction] = []
    media_details: Optional[Any] = None


class Workout(BaseModel):
    id: int
    challenge_id: int
    account: Account
    occurred_at: Optional[str] = None
    created_at: Optional[str] = None
    title: Optional[str] = None
    activity_type: Optional[str] = None
    duration: Optional[int] = None
    distance: Optional[float] = None
    calories: Optional[float] = None
    points: Optional[float] = None
    steps: Optional[int] = None
    description: Optional[str] = None
    photo_url: Optional[str] = None
    media: Optional[Any] = None
    reactions: list[Reaction] = []       # embutidas no check-in
    comment_count: Optional[int] = None  # conteudo em /workouts/{id}/comments


class WorkoutPage(BaseModel):
    items: list[Workout]
    total: int
    limit: int
    offset: int


# --- insights (agregados p/ o dashboard) ---
class InsightsTotals(BaseModel):
    workouts: int
    minutes: float
    athletes_active: int   # atletas com >=1 check-in


class DailyPoint(BaseModel):
    date: str              # YYYY-MM-DD no fuso GYMRATS_TZ
    workouts: int
    athletes: int          # atletas distintos no dia
    minutes: float


class Periods(BaseModel):
    madrugada: int = 0     # 0-4h
    manha: int = 0         # 5-11h
    tarde: int = 0         # 12-17h
    noite: int = 0         # 18-23h


class WeekdayDuration(BaseModel):
    weekday: int                         # 0 = segunda
    workouts: int
    avg_minutes: Optional[float] = None  # media so dos check-ins com duracao > 0


class Streak(BaseModel):
    account: Account
    longest: int
    current: int   # sequencia que termina hoje ou ontem (senao 0)


class Award(BaseModel):
    account: Account
    value: int


class DuoAward(BaseModel):
    accounts: list[Account]   # exatamente 2, ordenados por id
    value: int                # total de reacoes trocadas (a_to_b + b_to_a)
    a_to_b: int               # reacoes de accounts[0] em check-ins de accounts[1]
    b_to_a: int               # reacoes de accounts[1] em check-ins de accounts[0]


class Awards(BaseModel):
    early_bird: Optional[Award] = None
    night_owl: Optional[Award] = None
    most_reactions: Optional[Award] = None
    most_comments: Optional[Award] = None
    most_reactions_given: Optional[Award] = None
    marathoner: Optional[Award] = None      # value = minutos totais (int)
    weekend: Optional[Award] = None         # check-ins em sabado/domingo (hora local)
    dynamic_duo: Optional[DuoAward] = None


class TopWorkout(BaseModel):
    id: int
    title: Optional[str] = None
    photo_url: Optional[str] = None
    account: Account
    occurred_at: Optional[str] = None
    reactions_count: int
    comment_count: int


class EmojiCount(BaseModel):
    emoji: str
    count: int


class Insights(BaseModel):
    challenge_id: int
    tz: str
    members_total: int
    totals: InsightsTotals
    daily: list[DailyPoint]
    punchcard: list[list[int]]   # 7 x 24 (0 = segunda), hora local
    periods: Periods
    duration_by_weekday: list[WeekdayDuration]
    streaks: list[Streak]
    awards: Awards
    top_workouts: list[TopWorkout]
    emojis: list[EmojiCount]
    until: Optional[str] = None  # corte (YYYY-MM-DD) ou null


# ---------- shaping ----------
def shape_challenge(row) -> Challenge:
    r = _raw(row)
    with db() as con:
        mc = con.execute(
            "SELECT COUNT(*) FROM members WHERE challenge_id=?", (row["id"],)
        ).fetchone()[0]
    return Challenge(
        id=row["id"], name=row["name"] or r.get("name"),
        description=r.get("description"), code=r.get("code"),
        start_date=r.get("start_date"), end_date=r.get("end_date"),
        group_type=r.get("group_type"), score_by=r.get("score_by"),
        teams_enabled=r.get("teams_enabled"),
        profile_picture_url=r.get("profile_picture_url"),
        member_count=mc, synced_at=row["synced_at"],
    )


def shape_account(d: dict) -> Account:
    return Account(id=d.get("id"), full_name=d.get("full_name"),
                   profile_picture_url=d.get("profile_picture_url"))


def shape_reactions(lst) -> list[Reaction]:
    out = []
    for x in (lst or []):
        if isinstance(x, dict):
            out.append(Reaction(reaction=x.get("reaction"),
                                account=shape_account(x.get("account") or {})))
    return out


def shape_comment(d: dict) -> Comment:
    return Comment(
        id=d.get("id"), workout_id=d.get("workout_id"),
        content=d.get("content"), created_at=d.get("created_at"),
        account=shape_account(d.get("account") or {}),
        reactions=shape_reactions(d.get("reactions")),
        media_details=d.get("media_details"),
    )


def shape_workout(row) -> Workout:
    w = _raw(row)
    acc = w.get("account") or {}
    return Workout(
        id=row["id"], challenge_id=row["challenge_id"], account=shape_account(acc),
        occurred_at=w.get("occurred_at"), created_at=w.get("created_at"),
        title=w.get("title"), activity_type=w.get("activity_type") or w.get("activity"),
        duration=inum(w.get("duration")),
        distance=(num(w.get("distance")) if w.get("distance") is not None else None),
        calories=(num(w.get("calories")) if w.get("calories") is not None else None),
        points=(num(w.get("points")) if w.get("points") is not None else None),
        steps=inum(w.get("steps")), description=w.get("description"),
        photo_url=w.get("photo_url"), media=w.get("media"),
        reactions=shape_reactions(w.get("reactions")),
        comment_count=inum(w.get("comment_count")),
    )


# ---------- rotas publicas ----------
@app.get("/", tags=["meta"])
def root():
    return {"name": "GymRats Personal API", "docs": "/docs", "health": "/health"}


@app.get("/health", tags=["meta"])
def health():
    try:
        with db() as con:
            n = con.execute("SELECT COUNT(*) FROM challenges").fetchone()[0]
        return {"status": "ok", "challenges": n, "db": os.path.basename(DB_PATH)}
    except sqlite3.Error as e:
        raise HTTPException(503, f"db indisponivel: {e}")


def _img_cache_dir() -> str:
    """Lido a cada chamada (testes trocam o env); criado sob demanda."""
    d = os.environ.get("GYMRATS_IMG_CACHE") or (
        "/data/thumbs" if os.path.isdir("/data") else os.path.join(os.path.dirname(__file__), ".thumbs"))
    os.makedirs(d, exist_ok=True)
    return d


def _img_url_ok(u: str) -> bool:
    if len(u) > 512:
        return False
    try:
        p = urlsplit(u)
    except ValueError:
        return False
    # netloc exato: barra userinfo (a@host), porta (host:443) e sufixos (host.evil.com)
    return (p.scheme == "https" and p.netloc in IMG_HOSTS and len(p.path) > 1
            and not p.query and not p.fragment)


def _img_fetch(u: str) -> bytes:
    try:
        # sem redirects: um 3xx poderia levar a outro host (SSRF)
        r = requests.get(u, timeout=15, stream=True, allow_redirects=False,
                         headers={"User-Agent": "gr-api/1.0"})
    except requests.RequestException as e:
        raise HTTPException(502, f"falha ao baixar imagem: {e}")
    try:
        if r.status_code == 404:
            raise HTTPException(404, "imagem nao encontrada")
        if r.status_code != 200:
            raise HTTPException(502, f"CDN respondeu {r.status_code}")
        if not (r.headers.get("Content-Type") or "").lower().startswith("image/"):
            raise HTTPException(502, "upstream nao e imagem")
        if int(num(r.headers.get("Content-Length"))) > IMG_MAX_BYTES:
            raise HTTPException(502, "imagem grande demais")
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > IMG_MAX_BYTES:
                raise HTTPException(502, "imagem grande demais")
        return bytes(buf)
    except requests.RequestException as e:
        raise HTTPException(502, f"falha ao baixar imagem: {e}")
    finally:
        r.close()


def _img_thumb(data: bytes, w: int) -> bytes:
    try:
        im = Image.open(io.BytesIO(data))
        # proporcao do original (ja rotacionado pelo EXIF): o draft arredonda e distorce um pouco
        ow, oh = im.size
        if im.getexif().get(0x0112) in (5, 6, 7, 8):
            ow, oh = oh, ow
        if im.format == "JPEG":
            im.draft(None, (w, w))  # decodifica JPEG ja reduzido (DCT), >= w nos 2 lados
        im = ImageOps.exif_transpose(im)
        alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if alpha else "RGB")
        if ow > w:  # nunca amplia
            im = im.resize((w, max(1, round(oh * w / ow))), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "WEBP", quality=82, method=6)
        return out.getvalue()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as e:
        raise HTTPException(502, f"imagem invalida: {e.__class__.__name__}")


@app.get("/img", tags=["meta"], response_class=Response,
         responses={200: {"content": {"image/webp": {}}}, 304: {"description": "nao modificado"}})
def img(u: str = Query(..., description="URL https://cdn.gymrats.app/..."),
        w: int = Query(..., description=f"largura, uma de {IMG_WIDTHS}"),
        if_none_match: Optional[str] = Header(default=None)):
    """Miniatura WebP de uma imagem do CDN do GymRats, cacheada em disco (publico: <img> nao manda header)."""
    if not _img_url_ok(u):
        raise HTTPException(400, "u deve ser https://cdn.gymrats.app/<arquivo>")
    if w not in IMG_WIDTHS:
        raise HTTPException(400, f"w deve ser um de {list(IMG_WIDTHS)}")
    key = f"{hashlib.sha256(u.encode()).hexdigest()}_{w}"
    headers = {"Cache-Control": IMG_CACHE_CONTROL, "ETag": f'"{key}"'}
    # conteudo e funcao de (u, w) e o CDN e imutavel (uuid): ETag igual -> 304 sem tocar no disco
    if if_none_match and (if_none_match.strip() == "*" or f'"{key}"' in
                          [t.strip().removeprefix("W/") for t in if_none_match.split(",")]):
        return Response(status_code=304, headers=headers)
    d = _img_cache_dir()
    path = os.path.join(d, key + ".webp")
    if not os.path.exists(path):
        if not _img_sem.acquire(timeout=30):
            raise HTTPException(503, "ocupado, tente de novo")
        try:
            if not os.path.exists(path):  # outra thread pode ter gerado enquanto esperava
                thumb = _img_thumb(_img_fetch(u), w)
                fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(thumb)
                    os.replace(tmp, path)  # atomico: nunca serve arquivo pela metade
                except BaseException:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
        finally:
            _img_sem.release()
    return FileResponse(path, media_type="image/webp", headers=headers)


# ---------- rotas de dados (protegidas por API key, se configurada) ----------
@app.get("/challenges", response_model=list[Challenge], tags=["challenges"],
         dependencies=[Depends(require_key)])
def list_challenges():
    with db() as con:
        rows = con.execute("SELECT id,name,raw,synced_at FROM challenges").fetchall()
    out = [shape_challenge(r) for r in rows]
    out.sort(key=lambda c: c.start_date or "", reverse=True)  # mais recentes primeiro
    return out


@app.get("/challenges/{cid}", response_model=Challenge, tags=["challenges"],
         dependencies=[Depends(require_key)])
def get_challenge(cid: int):
    with db() as con:
        row = con.execute("SELECT id,name,raw,synced_at FROM challenges WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "desafio nao encontrado")
    return shape_challenge(row)


@app.get("/challenges/{cid}/members", response_model=list[Member], tags=["challenges"],
         dependencies=[Depends(require_key)])
def challenge_members(cid: int):
    with db() as con:
        rows = con.execute("SELECT raw FROM members WHERE challenge_id=? ORDER BY name", (cid,)).fetchall()
    out = []
    for r in rows:
        m = _raw(r)
        out.append(Member(id=m.get("id"), full_name=m.get("full_name"),
                          profile_picture_url=m.get("profile_picture_url"),
                          instagram=m.get("instagram"), tik_tok=m.get("tik_tok"),
                          twitter=m.get("twitter")))
    return out


@app.get("/challenges/{cid}/rankings", response_model=list[RankingEntry], tags=["challenges"],
         dependencies=[Depends(require_key)])
def challenge_rankings(cid: int):
    with db() as con:
        rows = con.execute(
            "SELECT rank,points,raw FROM rankings WHERE challenge_id=? "
            "ORDER BY COALESCE(rank,999999), points DESC", (cid,)).fetchall()
    out = []
    for r in rows:
        raw = _raw(r)
        out.append(RankingEntry(rank=r["rank"], score=r["points"],
                                account=shape_account(raw.get("account") or {})))
    return out


@app.get("/challenges/{cid}/workouts", response_model=WorkoutPage, tags=["workouts"],
         dependencies=[Depends(require_key)])
def challenge_workouts(
    cid: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    member_id: Optional[int] = Query(None, description="filtra por account_id do membro"),
    since: Optional[str] = Query(None, description="occurred_at >= (ISO date/datetime)"),
    until: Optional[str] = Query(None, description="occurred_at <= (ISO date/datetime)"),
):
    where = ["challenge_id=?"]
    args: list = [cid]
    if member_id is not None:
        where.append("account_id=?"); args.append(member_id)
    if since:
        where.append("occurred_on>=?"); args.append(since)
    if until:
        where.append("occurred_on<=?"); args.append(until)
    wsql = " AND ".join(where)
    with db() as con:
        total = con.execute(f"SELECT COUNT(*) FROM workouts WHERE {wsql}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT id,challenge_id,account_id,occurred_on,raw FROM workouts "
            f"WHERE {wsql} ORDER BY occurred_on DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()
    return WorkoutPage(items=[shape_workout(r) for r in rows],
                       total=total, limit=limit, offset=offset)


@app.get("/workouts/{wid}", response_model=Workout, tags=["workouts"],
         dependencies=[Depends(require_key)])
def get_workout(wid: int):
    """Um check-in (do banco), com reacoes embutidas e o comment_count."""
    with db() as con:
        row = con.execute(
            "SELECT id,challenge_id,account_id,occurred_on,raw FROM workouts WHERE id=?",
            (wid,)).fetchone()
    if not row:
        raise HTTPException(404, "check-in nao encontrado")
    return shape_workout(row)


@app.get("/workouts/{wid}/comments", response_model=list[Comment], tags=["workouts"],
         dependencies=[Depends(require_key)])
def workout_comments(wid: int):
    """Comentarios de um check-in. Buscados no GymRats sob demanda (cache ~60s).

    Cada comentario tem autor, texto, data e suas proprias reacoes.
    Requer GYMRATS_TOKEN configurado na API.
    """
    return [shape_comment(c) for c in _fetch_comments(wid)]


def local_dt(occurred_on: Optional[str]) -> Optional[datetime]:
    """Instante convertido p/ o fuso GYMRATS_TZ (None se vazio/invalido)."""
    if not occurred_on:
        return None
    try:
        dt = datetime.fromisoformat(occurred_on.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:  # sem fuso: trata como UTC, igual ao resto da API
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(TZ)


def local_day(occurred_on: Optional[str]) -> Optional[str]:
    """Dia de calendario (YYYY-MM-DD) do instante no fuso GYMRATS_TZ."""
    dt = local_dt(occurred_on)
    if dt is None:
        return (occurred_on or "")[:10] or None
    return dt.date().isoformat()


@app.get("/challenges/{cid}/stats", tags=["workouts"], dependencies=[Depends(require_key)])
def challenge_stats(cid: int, until: Optional[date] = None):
    """Agregados prontos para dashboard: total, check-ins por dia e por membro.

    Dias sao contados no fuso GYMRATS_TZ (default America/Sao_Paulo).
    `until` (YYYY-MM-DD): so check-ins com dia local <= until.
    """
    with db() as con:
        rows = con.execute(
            "SELECT account_id,occurred_on,raw FROM workouts WHERE challenge_id=?", (cid,)).fetchall()
        members = {m["id"]: _raw(m) for m in
                   con.execute("SELECT id,raw FROM members WHERE challenge_id=?", (cid,)).fetchall()}
    rows = _until_rows(rows, until)
    by_day: dict[str, int] = {}
    by_member: dict[int, dict] = {}
    member_days: dict[int, set[str]] = {}
    for r in rows:
        day = local_day(r["occurred_on"])
        if day:
            by_day[day] = by_day.get(day, 0) + 1
        w = _raw(r)
        acc_id = r["account_id"]
        m = by_member.setdefault(acc_id, {"account_id": acc_id, "workouts": 0, "active_days": 0,
                                          "points": 0.0, "calories": 0.0, "distance": 0.0})
        m["workouts"] += 1
        if day:
            member_days.setdefault(acc_id, set()).add(day)
        m["points"] += num(w.get("points"))
        m["calories"] += num(w.get("calories"))
        m["distance"] += num(w.get("distance"))
    for acc_id, m in by_member.items():
        m["active_days"] = len(member_days.get(acc_id, ()))
        info = members.get(acc_id) or (_raw_from_any(rows, acc_id))
        m["name"] = (info or {}).get("full_name")
    return {
        "challenge_id": cid,
        "until": until.isoformat() if until else None,
        "total_workouts": len(rows),
        "members": len(by_member),
        "by_day": [{"date": d, "count": by_day[d]} for d in sorted(by_day)],
        "by_member": sorted(by_member.values(), key=lambda x: x["workouts"], reverse=True),
    }


def _until_rows(rows, until: Optional[date]):
    """Corte por dia local <= until; sem data parseavel fica de fora quando ha corte."""
    if until is None:
        return rows
    lim = until.isoformat()
    return [r for r in rows if local_dt(r["occurred_on"]) is not None
            and local_day(r["occurred_on"]) <= lim]


def _raw_from_any(rows, acc_id) -> dict:
    for r in rows:
        if r["account_id"] == acc_id:
            return (_raw(r).get("account") or {})
    return {}


def _now() -> datetime:
    """'Agora' no fuso local (isolado p/ os testes poderem fixar)."""
    return datetime.now(TZ)


def _period(h: int) -> str:
    return "madrugada" if h < 5 else "manha" if h < 12 else "tarde" if h < 18 else "noite"


def _first_photo(w: dict) -> Optional[str]:
    """Primeira url de media; senao photo_url."""
    for m in (w.get("media") or []) if isinstance(w.get("media"), list) else []:
        u = m.get("url") if isinstance(m, dict) else (m if isinstance(m, str) else None)
        if u:
            return u
    return w.get("photo_url")


def _streaks(days: set[date], today: date) -> tuple[int, int]:
    """(maior sequencia, sequencia atual) de dias consecutivos."""
    longest = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        longest = max(longest, run)
        prev = d
    cur = 0
    d = today if today in days else today - timedelta(days=1)
    while d in days:
        cur += 1
        d -= timedelta(days=1)
    return longest, cur


@app.get("/challenges/{cid}/insights", response_model=Insights, tags=["workouts"],
         dependencies=[Depends(require_key)])
def challenge_insights(cid: int, until: Optional[date] = None):
    """Agregados ricos p/ o dashboard: serie diaria, punchcard, periodos, duracao por dia
    da semana, sequencias, premios, top check-ins e emojis.

    Tudo calculado no fuso GYMRATS_TZ (default America/Sao_Paulo).
    `until` (YYYY-MM-DD): so check-ins com dia local <= until; "hoje" das sequencias vira until.
    """
    with db() as con:
        if not con.execute("SELECT 1 FROM challenges WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "desafio nao encontrado")
        rows = con.execute(
            "SELECT id,account_id,occurred_on,raw FROM workouts WHERE challenge_id=?", (cid,)).fetchall()
        members = {m["id"]: _raw(m) for m in
                   con.execute("SELECT id,raw FROM members WHERE challenge_id=?", (cid,)).fetchall()}
    rows = _until_rows(rows, until)

    accounts: dict[int, dict] = {}       # account_id -> raw do account (workout > members)
    n_workouts: dict[int, int] = {}
    days_by_acc: dict[int, set[date]] = {}
    daily: dict[str, list] = {}          # dia -> [workouts, set(atletas), minutos]
    punch = [[0] * 24 for _ in range(7)]
    periods = Periods()
    wd_count, wd_dur = [0] * 7, [[0.0, 0] for _ in range(7)]
    early: dict[int, int] = {}
    night: dict[int, int] = {}
    reacts_recv: dict[int, int] = {}
    comments_recv: dict[int, int] = {}
    reacts_given: dict[int, int] = {}
    reactor_acc: dict[int, dict] = {}
    minutes_by: dict[int, float] = {}
    weekend: dict[int, int] = {}
    reacts_on: dict[tuple[int, int], int] = {}   # (quem reagiu, dono do check-in) -> n
    emojis: dict[str, int] = {}
    tops: list[tuple] = []
    minutes_total = 0.0

    for r in rows:
        w = _raw(r)
        aid = r["account_id"]
        acc = w.get("account") or {}
        if aid not in accounts or (acc.get("full_name") and not accounts[aid].get("full_name")):
            accounts[aid] = acc if acc.get("full_name") else (members.get(aid) or acc or {"id": aid})
        n_workouts[aid] = n_workouts.get(aid, 0) + 1
        dur = num(w.get("duration"))
        minutes_total += dur
        minutes_by[aid] = minutes_by.get(aid, 0.0) + dur
        reactions = [x for x in (w.get("reactions") or []) if isinstance(x, dict)]
        cc = inum(w.get("comment_count")) or 0
        reacts_recv[aid] = reacts_recv.get(aid, 0) + len(reactions)
        comments_recv[aid] = comments_recv.get(aid, 0) + cc
        for x in reactions:
            if x.get("reaction"):
                emojis[x["reaction"]] = emojis.get(x["reaction"], 0) + 1
            ra = x.get("account") or {}
            if ra.get("id") is not None:
                rid = inum(ra["id"])
                reacts_given[rid] = reacts_given.get(rid, 0) + 1
                reactor_acc.setdefault(rid, ra)
                if rid != aid:  # auto-reacao nao conta p/ dupla
                    reacts_on[(rid, aid)] = reacts_on.get((rid, aid), 0) + 1
        if len(reactions) + cc > 0:
            tops.append((len(reactions) + cc, r["occurred_on"] or "", r, w, len(reactions), cc))

        dt = local_dt(r["occurred_on"])
        if dt is None:
            continue
        day, wd, h = dt.date(), dt.weekday(), dt.hour
        dd = daily.setdefault(day.isoformat(), [0, set(), 0.0])
        dd[0] += 1; dd[1].add(aid); dd[2] += dur
        days_by_acc.setdefault(aid, set()).add(day)
        punch[wd][h] += 1
        setattr(periods, _period(h), getattr(periods, _period(h)) + 1)
        wd_count[wd] += 1
        if dur > 0:
            wd_dur[wd][0] += dur; wd_dur[wd][1] += 1
        if h < 7:
            early[aid] = early.get(aid, 0) + 1
        if h >= 21:
            night[aid] = night.get(aid, 0) + 1
        if wd >= 5:
            weekend[aid] = weekend.get(aid, 0) + 1

    def acc_of(aid: int) -> Account:
        a = shape_account(accounts.get(aid) or members.get(aid) or reactor_acc.get(aid) or {})
        if a.id is None:
            a.id = aid
        return a

    def name_of(aid: int) -> str:
        return (acc_of(aid).full_name or "").lower()

    def award(counts: dict[int, int]) -> Optional[Award]:
        cands = [(v, a) for a, v in counts.items() if v > 0]
        if not cands:
            return None
        v, a = min(cands, key=lambda t: (-t[0], -n_workouts.get(t[1], 0), name_of(t[1])))
        return Award(account=acc_of(a), value=v)

    # reacoes dadas: so quem e membro do desafio ou aparece nos check-ins
    reacts_given = {a: v for a, v in reacts_given.items() if a in members or a in accounts}

    def dynamic_duo() -> Optional[DuoAward]:
        """Par mais *mutuo* (maior min(a->b, b->a)), depois total, check-ins somados, nomes."""
        g = {k: v for k, v in reacts_on.items() if k[0] in members or k[0] in accounts}
        best = None
        for x, y in {tuple(sorted(k)) for k in g}:
            ab, ba = g.get((x, y), 0), g.get((y, x), 0)
            key = (-min(ab, ba), -(ab + ba), -(n_workouts.get(x, 0) + n_workouts.get(y, 0)),
                   sorted((name_of(x), name_of(y))))
            if best is None or key < best[0]:
                best = (key, x, y, ab, ba)
        if best is None or best[0][0] == 0:  # dupla tem que ser mutua
            return None
        _, x, y, ab, ba = best
        return DuoAward(accounts=[acc_of(x), acc_of(y)], value=ab + ba, a_to_b=ab, b_to_a=ba)

    today = until or _now().astimezone(TZ).date()
    streaks = []
    for aid, days in days_by_acc.items():
        lg, cur = _streaks(days, today)
        streaks.append(Streak(account=acc_of(aid), longest=lg, current=cur))
    streaks.sort(key=lambda s: (-s.longest, -s.current, (s.account.full_name or "").lower()))

    tops.sort(key=lambda t: (t[0], t[1]), reverse=True)
    top_workouts = [TopWorkout(
        id=r["id"], title=w.get("title"), photo_url=_first_photo(w),
        account=shape_account(w.get("account") or {}) if (w.get("account") or {}).get("full_name")
        else acc_of(r["account_id"]),
        occurred_at=w.get("occurred_at") or r["occurred_on"],
        reactions_count=nr, comment_count=cc) for _, _, r, w, nr, cc in tops[:3]]

    return Insights(
        challenge_id=cid, tz=str(TZ.key), members_total=len(members),
        until=until.isoformat() if until else None,
        totals=InsightsTotals(workouts=len(rows), minutes=minutes_total,
                              athletes_active=len(n_workouts)),
        daily=[DailyPoint(date=d, workouts=v[0], athletes=len(v[1]), minutes=v[2])
               for d, v in sorted(daily.items())],
        punchcard=punch, periods=periods,
        duration_by_weekday=[WeekdayDuration(
            weekday=i, workouts=wd_count[i],
            avg_minutes=(wd_dur[i][0] / wd_dur[i][1]) if wd_dur[i][1] else None) for i in range(7)],
        streaks=streaks[:5],
        awards=Awards(early_bird=award(early), night_owl=award(night),
                      most_reactions=award(reacts_recv), most_comments=award(comments_recv),
                      most_reactions_given=award(reacts_given),
                      marathoner=award({a: int(round(m)) for a, m in minutes_by.items()}),
                      weekend=award(weekend), dynamic_duo=dynamic_duo()),
        top_workouts=top_workouts,
        emojis=[EmojiCount(emoji=e, count=c)
                for e, c in sorted(emojis.items(), key=lambda t: (-t[1], t[0]))[:8]],
    )


@app.get("/meta/sync", tags=["meta"], dependencies=[Depends(require_key)])
def sync_meta():
    """Ultima sincronizacao conhecida por desafio (do sync.py)."""
    with db() as con:
        rows = con.execute("SELECT challenge_id,last_date FROM sync_state").fetchall()
        latest = con.execute("SELECT MAX(synced_at) FROM challenges").fetchone()[0]
    return {"last_synced_at": latest,
            "per_challenge": [{"challenge_id": r["challenge_id"], "last_date": r["last_date"]} for r in rows]}
