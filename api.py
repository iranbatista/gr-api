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
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = os.environ.get("GYMRATS_DB", os.path.join(os.path.dirname(__file__), "gymrats.db"))
API_KEY = os.environ.get("GYMRATS_API_KEY", "")
# Fuso usado para agrupar check-ins por dia (by_day, active_days).
TZ = ZoneInfo(os.environ.get("GYMRATS_TZ", "America/Sao_Paulo"))
CORS_ORIGINS = [o.strip() for o in os.environ.get("GYMRATS_CORS", "*").split(",") if o.strip()]

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


class WorkoutPage(BaseModel):
    items: list[Workout]
    total: int
    limit: int
    offset: int


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


def local_day(occurred_on: Optional[str]) -> Optional[str]:
    """Dia de calendario (YYYY-MM-DD) do instante no fuso GYMRATS_TZ."""
    if not occurred_on:
        return None
    try:
        dt = datetime.fromisoformat(occurred_on.replace("Z", "+00:00"))
    except ValueError:
        return occurred_on[:10] or None
    if dt.tzinfo is None:  # sem fuso: trata como UTC, igual ao resto da API
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(TZ).date().isoformat()


@app.get("/challenges/{cid}/stats", tags=["workouts"], dependencies=[Depends(require_key)])
def challenge_stats(cid: int):
    """Agregados prontos para dashboard: total, check-ins por dia e por membro.

    Dias sao contados no fuso GYMRATS_TZ (default America/Sao_Paulo).
    """
    with db() as con:
        rows = con.execute(
            "SELECT account_id,occurred_on,raw FROM workouts WHERE challenge_id=?", (cid,)).fetchall()
        members = {m["id"]: _raw(m) for m in
                   con.execute("SELECT id,raw FROM members WHERE challenge_id=?", (cid,)).fetchall()}
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
        "total_workouts": len(rows),
        "members": len(by_member),
        "by_day": [{"date": d, "count": by_day[d]} for d in sorted(by_day)],
        "by_member": sorted(by_member.values(), key=lambda x: x["workouts"], reverse=True),
    }


def _raw_from_any(rows, acc_id) -> dict:
    for r in rows:
        if r["account_id"] == acc_id:
            return (_raw(r).get("account") or {})
    return {}


@app.get("/meta/sync", tags=["meta"], dependencies=[Depends(require_key)])
def sync_meta():
    """Ultima sincronizacao conhecida por desafio (do sync.py)."""
    with db() as con:
        rows = con.execute("SELECT challenge_id,last_date FROM sync_state").fetchall()
        latest = con.execute("SELECT MAX(synced_at) FROM challenges").fetchone()[0]
    return {"last_synced_at": latest,
            "per_challenge": [{"challenge_id": r["challenge_id"], "last_date": r["last_date"]} for r in rows]}
