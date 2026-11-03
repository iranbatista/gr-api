#!/usr/bin/env python3
"""Login no GymRats (email/senha) -> salva o JWT num arquivo. Senha oculta.
Nao imprime o token: so uma versao mascarada.

Uso:
  python login_new.py [arquivo_saida]     # default: .token-new.json

Nada e versionado (.token* esta no .gitignore).
"""
import getpass
import json
import os
import sys

import requests

BASE = os.environ.get("GYMRATS_BASE_URL", "https://www.gymrats.app/api")
OUT = sys.argv[1] if len(sys.argv) > 1 else ".token-new.json"
UA = "gymrats-personal-sync/0.1 (read-only)"

email = input("Email da conta nova: ").strip()
pw = getpass.getpass("Senha (nao aparece ao digitar): ")
if not email or not pw:
    sys.exit("Email e senha sao obrigatorios.")

try:
    r = requests.post(
        f"{BASE}/accounts/sign_in",
        json={"email": email, "password": pw},
        headers={"Content-Type": "application/json", "User-Agent": UA},
        timeout=30,
    )
except requests.RequestException as e:
    sys.exit(f"Falha de rede: {e}")

if not r.ok:
    sys.exit(f"Login falhou: HTTP {r.status_code} — {r.text[:200]}")

try:
    data = r.json()
except ValueError:
    sys.exit(f"Resposta nao-JSON (HTTP {r.status_code}): {r.text[:200]}")

tok = data.get("token") or (data.get("data") or {}).get("token")
if not tok:
    sys.exit(f"Login OK, mas nao achei 'token'. Chaves da resposta: {list(data)}")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"token": tok, "email": email}, f)
try:
    os.chmod(OUT, 0o600)
except OSError:
    pass

masked = f"{tok[:6]}…{tok[-4:]} (len={len(tok)})"
print(f"Token salvo em {OUT}: {masked}")
print(f"Formato JWT: {'sim' if tok.startswith('eyJ') and tok.count('.') == 2 else 'verificar'}")
