"""Testes do /challenges/{cid}/insights (e sanidade do /stats) sobre um SQLite temporario."""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

import pytest

# GYMRATS_DB precisa estar definido ANTES de importar a api
_tmp = tempfile.mkdtemp()
DB = os.path.join(_tmp, "test.db")
os.environ["GYMRATS_DB"] = DB
os.environ["GYMRATS_TZ"] = "America/Sao_Paulo"
os.environ["GYMRATS_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

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

ANA = {"id": 10, "full_name": "Ana", "profile_picture_url": "ana.jpg"}
BIA = {"id": 20, "full_name": "Bia", "profile_picture_url": "bia.jpg"}
CAIO = {"id": 30, "full_name": "Caio", "profile_picture_url": None}
STRANGER = {"id": 999, "full_name": "Fora"}


def rx(emoji, acc):
    return {"reaction": emoji, "account": acc}


# (id, account, occurred_at, raw extra); Bia sem "account" no raw -> fallback p/ members
WORKOUTS = [
    # 2026-10-27 22:30 local (terca), noite, night_owl
    (1, ANA, "2026-10-28T01:30:00Z", {"duration": 30, "title": "Corrida", "photo_url": "w1.jpg",
                                      "reactions": [rx("🔥", BIA), rx("💪", CAIO)], "comment_count": 1}),
    # 2026-10-26 06:00 local (segunda), manha, early_bird; duracao nula
    (2, ANA, "2026-10-26T09:00:00Z", {"duration": None, "reactions": [rx("🔥", STRANGER)]}),
    # 2026-10-25 12:00 local (domingo), tarde; duracao string; score 0 -> fora do top
    (3, ANA, "2026-10-25T15:00:00Z", {"duration": "45", "reactions": [], "comment_count": 0}),
    # 2026-10-20 03:00 local (terca), madrugada, early_bird; duracao 0
    (4, None, "2026-10-20T06:00:00Z", {"duration": 0, "title": "Treino", "photo_url": "p.jpg",
                                       "media": [{"url": "m.jpg"}],
                                       "reactions": [rx("🔥", ANA), rx("👏", ANA), rx("👏", CAIO)],
                                       "comment_count": 3}),
    (5, None, "2026-10-21T12:00:00Z", {"duration": 60, "reactions": [rx("🔥", ANA)]}),  # quarta 09h
    (6, None, "2026-10-22T12:00:00Z", {"duration": 20}),                                # quinta 09h
]


@pytest.fixture(scope="module")
def client():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO challenges VALUES (1,'Desafio',?,'2026-10-27')", (json.dumps({"name": "Desafio"}),))
    con.execute("INSERT INTO challenges VALUES (2,'Vazio',?,'2026-10-27')", (json.dumps({}),))
    for m in (ANA, BIA, CAIO):
        con.execute("INSERT INTO members VALUES (1,?,?,?,NULL)", (m["id"], m["full_name"], json.dumps(m)))
    for wid, acc, occ, extra in WORKOUTS:
        raw = {"id": wid, "occurred_at": occ, **extra}
        if acc:
            raw["account"] = acc
        con.execute("INSERT INTO workouts VALUES (?,1,?,?,?,NULL)",
                    (wid, (acc or BIA)["id"], occ.replace("Z", ".000000Z"), json.dumps(raw)))
    # desafio 2: um check-in ao meio-dia, sem reacoes/comentarios
    con.execute("INSERT INTO workouts VALUES (100,2,10,'2026-10-27T15:00:00.000000Z',?,NULL)",
                (json.dumps({"id": 100, "account": ANA, "duration": 10}),))
    con.commit(); con.close()
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(api, "_now", lambda: datetime(2026, 10, 27, 23, 0, tzinfo=api.TZ))


@pytest.fixture
def ins(client):
    r = client.get("/challenges/1/insights")
    assert r.status_code == 200, r.text
    return r.json()


def test_local_dt_bucketing():
    dt = api.local_dt("2026-10-28T01:30:00.000000Z")
    assert (dt.date().isoformat(), dt.weekday(), dt.hour) == ("2026-10-27", 1, 22)
    assert api.local_day("2026-10-28T01:30:00Z") == "2026-10-27"
    assert api.local_dt(None) is None and api.local_dt("lixo") is None


def test_totals_and_members(ins):
    assert ins["challenge_id"] == 1 and ins["tz"] == "America/Sao_Paulo"
    assert ins["members_total"] == 3
    assert ins["totals"] == {"workouts": 6, "minutes": 155.0, "athletes_active": 2}


def test_daily(ins):
    assert [d["date"] for d in ins["daily"]] == [
        "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-25", "2026-10-26", "2026-10-27"]
    assert ins["daily"][-1] == {"date": "2026-10-27", "workouts": 1, "athletes": 1, "minutes": 30.0}


def test_punchcard_and_periods(ins):
    pc = ins["punchcard"]
    assert len(pc) == 7 and all(len(row) == 24 for row in pc)
    assert sum(map(sum, pc)) == 6
    assert pc[1][22] == 1 and pc[1][3] == 1 and pc[0][6] == 1 and pc[6][12] == 1
    assert pc[2][9] == 1 and pc[3][9] == 1
    assert ins["periods"] == {"madrugada": 1, "manha": 3, "tarde": 1, "noite": 1}


def test_duration_by_weekday(ins):
    d = {x["weekday"]: x for x in ins["duration_by_weekday"]}
    assert len(ins["duration_by_weekday"]) == 7
    assert d[1] == {"weekday": 1, "workouts": 2, "avg_minutes": 30.0}  # ignora a duracao 0
    assert d[0] == {"weekday": 0, "workouts": 1, "avg_minutes": None}  # so duracao nula
    assert d[6]["avg_minutes"] == 45.0 and d[2]["avg_minutes"] == 60.0
    assert d[4] == {"weekday": 4, "workouts": 0, "avg_minutes": None}


def test_streaks(ins):
    s = [(x["account"]["full_name"], x["longest"], x["current"]) for x in ins["streaks"]]
    assert s == [("Ana", 3, 3), ("Bia", 3, 0)]
    assert ins["streaks"][1]["account"]["id"] == 20  # resolvido via members


def test_streak_current_from_yesterday(client, monkeypatch):
    monkeypatch.setattr(api, "_now", lambda: datetime(2026, 10, 28, 8, 0, tzinfo=api.TZ))
    s = client.get("/challenges/1/insights").json()["streaks"]
    assert (s[0]["account"]["full_name"], s[0]["current"]) == ("Ana", 3)
    monkeypatch.setattr(api, "_now", lambda: datetime(2026, 10, 29, 8, 0, tzinfo=api.TZ))
    s = client.get("/challenges/1/insights").json()["streaks"]
    assert [x["current"] for x in s] == [0, 0]


def test_awards(ins):
    a = {k: (v["account"]["full_name"], v["value"]) if v else None for k, v in ins["awards"].items()}
    assert a == {
        "early_bird": ("Ana", 1),        # empate 1x1 e 3x3 check-ins -> nome
        "night_owl": ("Ana", 1),
        "most_reactions": ("Bia", 4),
        "most_comments": ("Bia", 3),
        "most_reactions_given": ("Ana", 3),  # 999 (fora do desafio) nao conta
    }
    assert ins["awards"]["most_reactions"]["account"]["profile_picture_url"] == "bia.jpg"


def test_top_workouts(ins):
    t = ins["top_workouts"]
    assert [x["id"] for x in t] == [4, 1, 2]  # 2 e 5 empatam em 1 -> mais recente
    assert t[0]["photo_url"] == "m.jpg" and t[1]["photo_url"] == "w1.jpg"
    assert (t[0]["reactions_count"], t[0]["comment_count"]) == (3, 3)
    assert t[0]["account"]["full_name"] == "Bia"


def test_emojis(ins):
    assert ins["emojis"] == [{"emoji": "🔥", "count": 4}, {"emoji": "👏", "count": 2},
                             {"emoji": "💪", "count": 1}]


def test_empty_awards(client):
    r = client.get("/challenges/2/insights").json()
    assert r["awards"] == {k: None for k in ("early_bird", "night_owl", "most_reactions",
                                             "most_comments", "most_reactions_given")}
    assert r["top_workouts"] == [] and r["emojis"] == [] and r["members_total"] == 0
    assert r["streaks"][0]["current"] == 1


def test_404(client):
    assert client.get("/challenges/12345/insights").status_code == 404


def test_stats_still_works(client):
    r = client.get("/challenges/1/stats")
    assert r.status_code == 200
    j = r.json()
    assert j["total_workouts"] == 6 and j["by_day"][-1] == {"date": "2026-10-27", "count": 1}


def test_openapi_has_insights(client):
    spec = client.get("/openapi.json").json()
    assert "Insights" in spec["components"]["schemas"]
    ref = spec["paths"]["/challenges/{cid}/insights"]["get"]["responses"]["200"]["content"]
    assert ref["application/json"]["schema"]["$ref"].endswith("/Insights")
