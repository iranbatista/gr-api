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
DUDA = {"id": 40, "full_name": "Duda", "profile_picture_url": None}
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


# desafio 3: maratonista, fim de semana (hora local) e dupla dinamica
WORKOUTS_3 = [
    # domingo 01:30Z = sabado 22:30 local -> fim de semana
    (301, ANA, "2026-10-25T01:30:00Z", {"duration": 30, "reactions": [
        rx("🔥", BIA), rx("💪", BIA), rx("👀", STRANGER), rx("👀", STRANGER), rx("👀", STRANGER)]}),
    # segunda 01:00Z = domingo 22:00 local -> fim de semana
    (302, ANA, "2026-10-26T01:00:00Z", {"duration": None}),
    (303, ANA, "2026-10-21T12:00:00Z", {"duration": "45",  # auto-reacoes: ignoradas na dupla
                                        "reactions": [rx("🔥", ANA)] * 5}),
    (304, ANA, "2026-10-22T12:00:00Z", {"duration": "lixo"}),
    # terca 01:00Z = segunda 22:00 local -> NAO e fim de semana
    (320, BIA, "2026-10-27T01:00:00Z", {"duration": 50, "reactions": [rx("🔥", ANA)] * 3}),
    (321, BIA, "2026-10-23T12:00:00Z", {"duration": 20, "reactions": [rx("👏", CAIO)]}),
    (310, CAIO, "2026-10-24T15:00:00Z", {"duration": 60, "reactions": [rx("💪", BIA)] * 5}),  # sabado
    # Caio -> Duda 7x, sem volta: total maior mas unilateral
    (330, DUDA, "2026-10-22T15:00:00Z", {"duration": 10, "reactions": [rx("🔥", CAIO)] * 7}),
]

# desafio 4: so reacoes unilaterais (+ auto-reacao) -> sem dupla
WORKOUTS_4 = [
    (401, ANA, "2026-10-21T12:00:00Z", {"duration": 15, "reactions": [rx("🔥", BIA), rx("🔥", BIA),
                                                                      rx("💪", ANA)]}),
    (402, BIA, "2026-10-22T12:00:00Z", {"duration": 15, "reactions": [rx("🔥", BIA)]}),
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
    for cid, members, workouts in ((3, (ANA, BIA, CAIO, DUDA), WORKOUTS_3),
                                   (4, (ANA, BIA), WORKOUTS_4)):
        con.execute("INSERT INTO challenges VALUES (?,'X',?,'2026-10-27')", (cid, json.dumps({})))
        for m in members:
            con.execute("INSERT INTO members VALUES (?,?,?,?,NULL)",
                        (cid, m["id"], m["full_name"], json.dumps(m)))
        for wid, acc, occ, extra in workouts:
            con.execute("INSERT INTO workouts VALUES (?,?,?,?,?,NULL)",
                        (wid, cid, acc["id"], occ.replace("Z", ".000000Z"),
                         json.dumps({"id": wid, "occurred_at": occ, "account": acc, **extra})))
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


def _duo(d):
    return d and ([x["full_name"] for x in d["accounts"]], d["value"], d["a_to_b"], d["b_to_a"])


def test_awards(ins):
    a = {k: (v["account"]["full_name"], v["value"]) if v else None
         for k, v in ins["awards"].items() if k != "dynamic_duo"}
    assert a == {
        "early_bird": ("Ana", 1),        # empate 1x1 e 3x3 check-ins -> nome
        "night_owl": ("Ana", 1),
        "most_reactions": ("Bia", 4),
        "most_comments": ("Bia", 3),
        "most_reactions_given": ("Ana", 3),  # 999 (fora do desafio) nao conta
        "marathoner": ("Bia", 80),           # Ana: 30 + None + "45" = 75
        "weekend": ("Ana", 1),               # domingo 12h local
    }
    # Ana -> Bia 3 (2 no #4 + 1 no #5), Bia -> Ana 1; Caio so da, nao recebe
    assert _duo(ins["awards"]["dynamic_duo"]) == (["Ana", "Bia"], 4, 3, 1)
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
    marathoner = r["awards"].pop("marathoner")  # unico check-in tem 10 min
    assert (marathoner["account"]["full_name"], marathoner["value"]) == ("Ana", 10)
    assert r["awards"] == {k: None for k in ("early_bird", "night_owl", "most_reactions",
                                             "most_comments", "most_reactions_given", "weekend",
                                             "dynamic_duo")}
    assert r["top_workouts"] == [] and r["emojis"] == [] and r["members_total"] == 0
    assert r["streaks"][0]["current"] == 1


@pytest.fixture
def awards3(client):
    r = client.get("/challenges/3/insights")
    assert r.status_code == 200, r.text
    return r.json()["awards"]


def test_marathoner_sums_durations(awards3):
    # Ana: 30 + None + "45" + "lixo" = 75 > Bia 70 > Caio 60 > Duda 10
    m = awards3["marathoner"]
    assert (m["account"]["full_name"], m["value"]) == ("Ana", 75)


def test_weekend_uses_local_time(awards3):
    # Ana: dom 01:30Z (sab local) + seg 01:00Z (dom local) = 2; Bia: ter 01:00Z = seg local -> 0
    w = awards3["weekend"]
    assert (w["account"]["full_name"], w["value"]) == ("Ana", 2)


def test_dynamic_duo_prefers_mutual(awards3):
    # Ana<->Bia 3/2 (min 2) vence Bia<->Caio 5/1 (total 6) e Caio->Duda 7/0 (unilateral);
    # 5 auto-reacoes da Ana e 3 reacoes do 999 (fora do desafio) nao contam
    d = awards3["dynamic_duo"]
    assert _duo(d) == (["Ana", "Bia"], 5, 3, 2)
    assert [x["id"] for x in d["accounts"]] == [10, 20]
    assert d["accounts"][1]["profile_picture_url"] == "bia.jpg"


def test_dynamic_duo_none_without_mutual(client):
    a = client.get("/challenges/4/insights").json()["awards"]
    assert a["dynamic_duo"] is None
    assert (a["most_reactions_given"]["account"]["full_name"], a["weekend"]) == ("Bia", None)


def test_404(client):
    assert client.get("/challenges/12345/insights").status_code == 404


def test_stats_still_works(client):
    r = client.get("/challenges/1/stats")
    assert r.status_code == 200
    j = r.json()
    assert j["total_workouts"] == 6 and j["by_day"][-1] == {"date": "2026-10-27", "count": 1}


def test_openapi_has_insights(client):
    spec = client.get("/openapi.json").json()
    assert {"Insights", "Awards", "DuoAward"} <= set(spec["components"]["schemas"])
    duo = spec["components"]["schemas"]["Awards"]["properties"]["dynamic_duo"]
    assert any(x.get("$ref", "").endswith("/DuoAward") for x in duo.get("anyOf", [duo]))
    ref = spec["paths"]["/challenges/{cid}/insights"]["get"]["responses"]["200"]["content"]
    assert ref["application/json"]["schema"]["$ref"].endswith("/Insights")
