"""Testes do corte opcional `until` em /stats e /insights sobre um SQLite temporario."""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

import pytest

# GYMRATS_DB precisa estar definido ANTES de importar a api (se outro teste ja importou, usa o db dele)
os.environ.setdefault("GYMRATS_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("GYMRATS_TZ", "America/Sao_Paulo")
os.environ["GYMRATS_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_insights import SCHEMA, ANA, BIA, rx  # noqa: E402

CID = 70
# (id, account, occurred_on, extra)
WORKOUTS = [
    (7001, ANA, "2026-10-25T15:00:00.000000Z", {"duration": 10}),                      # dom 25
    (7002, ANA, "2026-10-26T12:00:00.000000Z", {"duration": 20, "reactions": [rx("🔥", BIA)]}),
    # 01:30Z de 28 = 22:30 local de 27 -> entra com until=27
    (7003, ANA, "2026-10-28T01:30:00.000000Z", {"duration": 30, "comment_count": 1}),
    # 12:00Z de 28 = 28 local -> fora com until=27; pesa muito nos premios/top
    (7004, ANA, "2026-10-28T12:00:00.000000Z", {"duration": 500, "comment_count": 9,
                                                "reactions": [rx("💪", BIA)] * 5}),
    (7005, BIA, "2026-10-20T12:00:00.000000Z", {"duration": 15}),
    (7006, BIA, "lixo", {"duration": 15}),                                              # sem data
]


@pytest.fixture(scope="module")
def client():
    con = sqlite3.connect(api.DB_PATH)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO challenges VALUES (?,'Corte',?,'2026-10-27')", (CID, json.dumps({})))
    for m in (ANA, BIA):
        con.execute("INSERT INTO members VALUES (?,?,?,?,NULL)", (CID, m["id"], m["full_name"], json.dumps(m)))
    for wid, acc, occ, extra in WORKOUTS:
        con.execute("INSERT INTO workouts VALUES (?,?,?,?,?,NULL)",
                    (wid, CID, acc["id"], occ, json.dumps({"id": wid, "account": acc, **extra})))
    con.commit(); con.close()
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(api, "_now", lambda: datetime(2026, 12, 1, 12, 0, tzinfo=api.TZ))


def stats(c, **q):
    r = c.get(f"/challenges/{CID}/stats", params=q); assert r.status_code == 200; return r.json()


def ins(c, **q):
    r = c.get(f"/challenges/{CID}/insights", params=q); assert r.status_code == 200; return r.json()


def test_stats_sem_until_conta_tudo(client):
    s = stats(client)
    assert s["until"] is None
    assert s["total_workouts"] == 6


def test_stats_corte(client):
    s = stats(client, until="2026-10-27")
    assert s["until"] == "2026-10-27"
    assert s["total_workouts"] == 4  # 7004 (dia 28) e 7006 (sem data) fora
    assert [d["date"] for d in s["by_day"]] == ["2026-10-20", "2026-10-25", "2026-10-26", "2026-10-27"]
    ana = next(m for m in s["by_member"] if m["account_id"] == ANA["id"])
    assert ana["workouts"] == 3 and ana["active_days"] == 3
    bia = next(m for m in s["by_member"] if m["account_id"] == BIA["id"])
    assert bia["workouts"] == 1


def test_stats_corte_exato_inclui_dia_28(client):
    s = stats(client, until="2026-10-28")
    assert s["total_workouts"] == 5  # sem o "lixo"
    assert s["by_day"][-1] == {"date": "2026-10-28", "count": 1}


def test_insights_sem_until(client):
    j = ins(client)
    assert j["until"] is None
    assert j["totals"]["workouts"] == 6
    assert j["awards"]["most_reactions"]["value"] == 6
    assert j["streaks"][0]["current"] == 0  # hoje fixado em 2026-12-01


def test_insights_corte(client):
    j = ins(client, until="2026-10-27")
    assert j["until"] == "2026-10-27"
    assert j["members_total"] == 2
    assert j["totals"]["workouts"] == 4
    assert [d["date"] for d in j["daily"]] == ["2026-10-20", "2026-10-25", "2026-10-26", "2026-10-27"]


def test_insights_corte_sem_lixo_nem_futuros(client):
    j = ins(client, until="2026-10-27")
    assert sum(sum(row) for row in j["punchcard"]) == 4  # 7006 sem data nao entra em nada
    assert j["totals"]["workouts"] == 4
    assert j["totals"]["minutes"] == 75
    assert sum(w["workouts"] for w in j["duration_by_weekday"]) == 4


def test_insights_premios_e_top_ate_o_corte(client):
    j = ins(client, until="2026-10-27")
    a = j["awards"]
    assert a["most_reactions"]["value"] == 1 and a["most_reactions"]["account"]["id"] == ANA["id"]
    assert a["most_comments"]["value"] == 1
    assert a["marathoner"]["value"] == 60 and a["marathoner"]["account"]["id"] == ANA["id"]
    assert a["dynamic_duo"] is None
    assert {w["id"] for w in j["top_workouts"]} == {7002, 7003}
    assert j["emojis"] == [{"emoji": "🔥", "count": 1}]


def test_insights_streak_na_data_do_corte(client):
    j = ins(client, until="2026-10-27")
    ana = next(s for s in j["streaks"] if s["account"]["id"] == ANA["id"])
    assert ana["longest"] == 3 and ana["current"] == 3  # hoje = until, nao _now()
    j = ins(client, until="2026-10-29")  # sem treino em 28? tem (7004): 4 dias, hoje=29 -> ontem conta
    ana = next(s for s in j["streaks"] if s["account"]["id"] == ANA["id"])
    assert ana["longest"] == 4 and ana["current"] == 4


def test_until_invalido_422(client):
    for ep in ("stats", "insights"):
        assert client.get(f"/challenges/{CID}/{ep}", params={"until": "2026-13-01"}).status_code == 422


def test_data_nao_parseavel_so_conta_sem_until(client):
    assert stats(client)["total_workouts"] == 6
    assert stats(client, until="2026-12-31")["total_workouts"] == 5
    assert ins(client)["totals"]["workouts"] == 6
    assert ins(client, until="2026-12-31")["totals"]["workouts"] == 5
