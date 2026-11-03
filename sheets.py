#!/usr/bin/env python3
"""
Exporta os check-ins do desafio ATIVO para uma aba do Google Sheets.

Opcional e isolado: se as variaveis de ambiente nao estiverem configuradas, o
sync roda normalmente e isto nao faz nada. Nenhuma falha aqui derruba o sync.

Autenticacao: conta de servico (service account), nao login interativo — porque
o sync roda sozinho dentro do container, sem ninguem logado. Passos:

  1. No Google Cloud (console.cloud.google.com), crie um projeto e habilite a
     "Google Sheets API".
  2. Crie uma Service Account e baixe a chave JSON.
  3. Compartilhe a planilha com o e-mail da service account (…@…iam.gserviceaccount.com)
     como EDITOR.
  4. Defina as variaveis de ambiente:
       GYMRATS_SHEETS_ID             = o ID da planilha (trecho do URL entre /d/ e /edit)
       GOOGLE_APPLICATION_CREDENTIALS = caminho do JSON da chave (ex.: /secrets/gsa.json)

A cada sync, a aba "<nome do desafio> - Check-ins" e reescrita por completo.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # py<3.9
    ZoneInfo = None

# Colunas da planilha, na ordem. (chave interna, cabecalho)
COLUMNS = [
    ("date", "Data"),
    ("time", "Hora"),
    ("member", "Membro"),
    ("activity", "Atividade"),
    ("duration", "Duração (min)"),
    ("distance", "Distância"),
    ("calories", "Calorias"),
    ("points", "Pontos"),
    ("description", "Descrição"),
    ("reactions", "Reações"),
    ("comments", "Comentários"),
    ("id", "ID"),
]

# Caracteres proibidos em nome de aba do Sheets.
_BAD_TAB = set(':\\/?*[]')


def _enabled() -> tuple[str, str] | None:
    sid = os.environ.get("GYMRATS_SHEETS_ID")
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if sid and creds:
        return sid, creds
    return None


def _is_active(c: dict) -> bool:
    """Desafio em andamento: comecou e ainda nao terminou (fim vazio = sem fim)."""
    today = date.today().isoformat()
    start = str(c.get("start_date") or "")[:10]
    end = str(c.get("end_date") or "")[:10]
    return (not start or start <= today) and (not end or end >= today)


def _tab_title(name: str | None) -> str:
    base = (name or "Desafio").strip()
    base = "".join(ch for ch in base if ch not in _BAD_TAB)
    title = f"{base} - Check-ins"
    return title[:100] or "Check-ins"  # Sheets limita a 100 chars


def _num(v):
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        return int(f) if f.is_integer() else round(f, 2)
    except (ValueError, TypeError):
        return v


def _parse_tz(tz):
    """tz do check-in -> tzinfo. Aceita IANA ('America/Sao_Paulo') ou offset ('-03:00')."""
    if not tz:
        return None
    tz = str(tz).strip()
    if tz and tz[0] in "+-" and ":" in tz:        # offset fixo, ex. -03:00
        try:
            sign = -1 if tz[0] == "-" else 1
            hh, mm = tz[1:].split(":")
            return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
        except ValueError:
            return None
    if ZoneInfo:                                   # nome IANA (precisa tzdata)
        try:
            return ZoneInfo(tz)
        except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError etc.
            return None
    return None


def _local_dt(raw: dict):
    """occurred_at (UTC) convertido pro fuso do proprio check-in (campo 'tz')."""
    occ = raw.get("occurred_at") or raw.get("created_at")
    if not occ:
        return None
    s = str(occ).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tzinfo = _parse_tz(raw.get("tz"))
    if tzinfo is not None:
        try:
            dt = dt.astimezone(tzinfo)
        except Exception:  # noqa: BLE001
            pass
    return dt


def _workout_points(raw: dict, score_by: str | None, seen_days: set, ldt):
    """Pontos por check-in DERIVADOS da regra do desafio (a API nao manda 'points').
    - workouts: 1 por check-in
    - duration: a duracao (min) do check-in
    - days:     1 no 1o check-in do dia (fuso local) por pessoa; 0 nos demais
    - outro/desconhecido: em branco
    """
    if score_by == "workouts":
        return 1
    if score_by == "duration":
        return _num(raw.get("duration"))
    if score_by == "days":
        acc = (raw.get("account") or {}).get("id") or raw.get("gym_rats_user_id")
        day = ldt.date().isoformat() if ldt else str(raw.get("occurred_at") or "")[:10]
        key = (acc, day)
        if key in seen_days:
            return 0
        seen_days.add(key)
        return 1
    return ""


def _row(raw: dict, member_name: str, points, ldt) -> list:
    d = ldt.date().isoformat() if ldt else ""
    t = ldt.strftime("%H:%M") if ldt else ""
    acc = raw.get("account") or {}
    name = acc.get("full_name") or member_name or ""
    reactions = raw.get("reactions")
    n_react = len(reactions) if isinstance(reactions, list) else 0
    values = {
        "date": d,
        "time": t,
        "member": name,
        # 'activity_type' vem vazio da API; a atividade real e o titulo digitado.
        "activity": (raw.get("title") or raw.get("activity_type") or "").replace("\n", " ").strip(),
        "duration": _num(raw.get("duration")),
        "distance": _num(raw.get("distance")),
        "calories": _num(raw.get("calories")),
        "points": points,
        "description": (raw.get("description") or "").replace("\n", " ").strip(),
        "reactions": n_react,
        "comments": _num(raw.get("comment_count")) or 0,
        "id": raw.get("id"),
    }
    return [values[k] for k, _ in COLUMNS]


def _build_values(con, cid: int, score_by: str | None = None) -> list[list]:
    """Cabecalho + uma linha por check-in, mais novos no topo (occurred_on DESC)."""
    names = {r[0]: r[1] for r in con.execute(
        "SELECT id, name FROM members WHERE challenge_id=?", (cid,)).fetchall()}
    rows = con.execute(
        "SELECT account_id, raw FROM workouts WHERE challenge_id=? "
        "ORDER BY occurred_on DESC, id DESC", (cid,)).fetchall()
    out = [[h for _, h in COLUMNS]]
    seen_days: set = set()   # p/ score_by='days': 1 ponto por (pessoa, dia)
    for account_id, raw_json in rows:
        try:
            raw = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        ldt = _local_dt(raw)
        pts = _workout_points(raw, score_by, seen_days, ldt)
        out.append(_row(raw, names.get(account_id, ""), pts, ldt))
    return out


def export_checkins(con, c: dict, log=print) -> None:
    """Se configurado e o desafio estiver ativo, reescreve a aba de check-ins.

    Nunca levanta excecao: qualquer problema vira log e o sync segue.
    """
    cfg = _enabled()
    if not cfg:
        return                      # feature desligada
    if not _is_active(c):
        return                      # so o desafio ativo vai pra planilha
    sheet_id, creds_path = cfg
    cid = c.get("id") or c.get("challenge_id")
    try:
        import gspread  # import tardio: so quando a feature esta ligada
    except ImportError:
        log("  [sheets] gspread nao instalado — pulando export (pip install gspread).")
        return
    try:
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(sheet_id)
        title = _tab_title(c.get("name"))
        values = _build_values(con, cid, c.get("score_by"))
        try:
            ws = sh.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=title,
                                  rows=max(len(values) + 10, 100),
                                  cols=len(COLUMNS))
        ws.clear()
        ws.update(range_name="A1", values=values, value_input_option="RAW")
        # metadados discretos numa celula ao lado do cabecalho? nao: mantemos simples.
        log(f"  [sheets] aba '{title}': {len(values) - 1} check-in(s) exportados.")
    except Exception as e:  # noqa: BLE001 — isolar o sync de qualquer falha do Sheets
        # gspread 6.x as vezes embrulha o 403 num PermissionError com str() vazio;
        # mostramos tipo + corpo HTTP quando houver, p/ a causa nunca sumir no log.
        detail = str(e) or type(e).__name__
        resp = getattr(e, "response", None) or getattr(getattr(e, "__cause__", None), "response", None)
        if resp is not None:
            try:
                detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
            except Exception:  # noqa: BLE001
                pass
        log(f"  [sheets] falha ao exportar (sync segue normal): {detail}")
