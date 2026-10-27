#!/usr/bin/env python3
"""Extrai o JWT do authentication.preferences_pb (DataStore) e grava em .token.json.
Nao imprime o token: so uma versao mascarada. Uso: python extract_token.py <arquivo.pb>"""
import json, os, re, sys

src = sys.argv[1] if len(sys.argv) > 1 else "datastore_dump/authentication.preferences_pb"
here = os.path.dirname(os.path.abspath(__file__))
data = open(src, "rb").read()

# JWT: tres segmentos base64url separados por ponto; primeiro comeca com eyJ
m = re.findall(rb"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}", data)
if not m:
    # fallback: qualquer token longo apos uma chave "token"
    m = re.findall(rb"[A-Za-z0-9_.\-]{40,}", data)
if not m:
    sys.exit("Nao achei token no arquivo. Confira se esta logado no app.")

tok = max(m, key=len).decode("ascii")
cache = os.path.join(here, ".token.json")
with open(cache, "w") as f:
    json.dump({"token": tok}, f)
try:
    os.chmod(cache, 0o600)
except OSError:
    pass

masked = f"{tok[:6]}…{tok[-4:]} (len={len(tok)})"
print(f"Token extraido e salvo em .token.json: {masked}")
print(f"Formato JWT: {'sim' if tok.startswith('eyJ') and tok.count('.')==2 else 'nao (verificar)'}")
