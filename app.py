#!/usr/bin/env python3
"""
GymRats — frontzinho local (Flask). SÓ LEITURA.

Roda na sua máquina: faz login, sincroniza e mostra os dados num dashboard
em http://127.0.0.1:5000 . Reaproveita toda a lógica de sync.py.
Credenciais e token NUNCA saem do seu PC.

Uso:
  pip install -r requirements.txt
  python app.py
  # abra http://127.0.0.1:5000 no navegador
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

import sync  # reusa Api, db, sync(), dump(), helpers

app = Flask(__name__)

# estado em memória (só nesta sessão do processo)
_state = {"last_sync": None, "syncing": False, "log": []}
_lock = threading.Lock()


def _log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    _state["log"].append(line)
    _state["log"][:] = _state["log"][-50:]
    print(line, flush=True)


def _api() -> sync.Api:
    return sync.Api()


@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    api = _api()
    return jsonify({
        "base_url": sync.BASE_URL,
        "email": sync.EMAIL or None,
        "logged_in": bool(api.token),
        "token_masked": sync.mask(api.token),
        "last_sync": _state["last_sync"],
        "syncing": _state["syncing"],
        "log": _state["log"][-15:],
    })


@app.route("/api/login", methods=["POST"])
def login():
    """Autentica por TOKEN colado (JWT extraído do app). Testa contra /challenges/."""
    data = request.get_json(force=True)
    token = (data.get("token") or "").strip()
    base = (data.get("base_url") or "").strip()
    if base:
        sync.BASE_URL = base.rstrip("/")
    if not token:
        return jsonify({"ok": False, "error": "Cole o token (JWT) do app."}), 400
    # valida o token com uma leitura real
    try:
        api = sync.Api()
        api.token = token
        api.get("/challenges/")           # 401 -> TokenExpired
        sync.save_token(token)            # cacheia só se válido
        _log(f"Token aceito ({sync.mask(token)})")
        return jsonify({"ok": True, "token_masked": sync.mask(token)})
    except sync.TokenExpired:
        _log("Token recusado (401).")
        return jsonify({"ok": False, "error": "Token inválido/expirado (401). Extraia um novo do app."}), 401
    except Exception as e:  # noqa: BLE001
        _log(f"Falha ao validar token: {e}")
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/api/logout", methods=["POST"])
def logout():
    try:
        sync.TOKEN_CACHE.unlink(missing_ok=True)
    except OSError:
        pass
    _log("Logout (token descartado).")
    return jsonify({"ok": True})


@app.route("/api/sync", methods=["POST"])
def do_sync():
    if _state["syncing"]:
        return jsonify({"ok": False, "error": "Sync já em andamento."}), 409

    def run():
        _state["syncing"] = True
        try:
            _log("Sincronizando…")
            # usa o logger do sync.py redirecionado p/ nosso buffer
            orig = sync.log
            sync.log = _log
            try:
                sync.sync(_api())
            finally:
                sync.log = orig
            _state["last_sync"] = datetime.now(timezone.utc).isoformat()
            _log("Sync concluído.")
        except Exception as e:  # noqa: BLE001
            _log(f"Erro no sync: {e}")
        finally:
            _state["syncing"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})


# ---- leitura do SQLite para o dashboard ----
def _rows(sql, args=()):
    con = sync.db()
    con.row_factory = __import__("sqlite3").Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


@app.route("/api/challenges")
def challenges():
    out = []
    for c in _rows("SELECT id,name,raw,synced_at FROM challenges ORDER BY name"):
        raw = json.loads(c["raw"]) if c["raw"] else {}
        out.append({
            "id": c["id"], "name": c["name"] or raw.get("name"),
            "synced_at": c["synced_at"],
            "members": raw.get("member_count") or raw.get("members_count"),
            "start": raw.get("start_date"), "end": raw.get("end_date"),
            "raw": raw,
        })
    return jsonify(out)


@app.route("/api/challenges/<int:cid>/rankings")
def rankings(cid):
    rows = _rows(
        "SELECT account_id,rank,points,raw FROM rankings WHERE challenge_id=? "
        "ORDER BY COALESCE(rank, 9999), points DESC", (cid,))
    for r in rows:
        r["raw"] = json.loads(r["raw"]) if r.get("raw") else {}
        acc = r["raw"].get("account") or {}
        r["name"] = acc.get("full_name") or acc.get("name") or f"acc {r['account_id']}"
    return jsonify(rows)


@app.route("/api/challenges/<int:cid>/workouts")
def workouts(cid):
    rows = _rows(
        "SELECT id,account_id,occurred_on,raw FROM workouts WHERE challenge_id=? "
        "ORDER BY occurred_on DESC LIMIT 5000", (cid,))
    for r in rows:
        r["raw"] = json.loads(r["raw"]) if r.get("raw") else {}
    return jsonify(rows)


if __name__ == "__main__":
    print("GymRats UI -> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
