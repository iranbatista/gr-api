# Documentação da GymRats Sync API

Guia completo do projeto: desde **pegar as credenciais** (o token do GymRats e uma
API key) até **subir a API num servidor** e consumi-la num app.

> **Uso pessoal.** A API lê a API privada do app GymRats com o **seu próprio token**,
> só os grupos de que você é membro, só leitura, com calma (1 req/s). Não enumera nem
> altera dados de terceiros. Veja [`REPORT.md`](../REPORT.md) para como ela foi descoberta.

---

## O que é isto

Um pipeline que espelha os dados dos seus desafios do GymRats para um banco local e
os serve como uma API REST limpa (e, opcionalmente, para uma planilha do Google):

```
  GymRats (API privada)
        │   sync.py puxa (read-only, 1 req/s, incremental)
        ▼
  gymrats.db (SQLite)  ──►  api.py (FastAPI REST)  ──►  seu app / dashboard
        │
        └─────────────►  sheets.py (opcional) ──► Google Sheets
```

| Componente | Papel |
|---|---|
| `sync.py` | Ingestão: desafios, membros, rankings e check-ins → `gymrats.db`. |
| `api.py` | REST API (FastAPI): CORS, API keys, paginação, `/stats`, `/insights`, Swagger em `/docs`. |
| `apikeys.py` | Gera e gerencia as API keys que protegem a API (biblioteca + CLI). |
| `sheets.py` | Export opcional dos check-ins do desafio ativo para o Google Sheets. |
| `app.py` | Dashboard local (Flask) para olhar rápido. |
| `extract_token.py` / `login_new.py` | Obtêm o token do GymRats (via emulador ou e-mail/senha). |

---

## As duas credenciais (não confunda)

Este é o ponto que mais gera confusão. São **dois segredos diferentes**, com origens diferentes:

| | **Token do GymRats** (`GYMRATS_TOKEN`) | **API key** (`GYMRATS_API_KEY` / registro) |
|---|---|---|
| Para quê | O `sync.py` usar pra **ler** o GymRats | Proteger a **sua** API de quem não deve acessar |
| Quem emite | O **GymRats** (ao logar no app) | **Você** (gera à vontade) |
| Dá pra fabricar? | **Não** — tem que vir do login no app | **Sim** — é aleatória, você cria quantas quiser |
| Onde vem de | Emulador Android ou `login_new.py` ([doc](token.md)) | `apikeys.py` ([doc](api-keys.md)) |
| Expira? | Sim (JWT de vida longa; renova no 401) | Não (só se você revogar) |
| Quem manda | O servidor, no header `Authorization` para o GymRats | O cliente (React etc.), no header `X-API-Key` para a sua API |

Resumindo: o **token** você *consegue* do GymRats; a **API key** você *gera*. Precisa dos dois.

---

## Índice

1. **[Obter e renovar o token do GymRats](token.md)** — emulador Android, `login_new.py`, e o que fazer quando expira (401).
2. **[Gerar e gerenciar API keys](api-keys.md)** — `apikeys.py` (generate/list/revoke), modelo multi-key, fail-closed.
3. **[Rodar local](local.md)** — instalar, sincronizar, subir a API e o dashboard na sua máquina.
4. **[Deploy no servidor](deploy.md)** — Docker Compose + Caddy (HTTPS), passo a passo.
5. **[Export para Google Sheets](google-sheets.md)** — conta de serviço, opcional.
6. **[Referência da API](api-reference.md)** — todos os endpoints + exemplos de consumo no React.
7. **[Operação](operations.md)** — backup, logs, atualização e troubleshooting (401/403/503).

---

## Começo rápido (local)

```bash
python -m pip install -r requirements.txt -r requirements-api.txt

# 1) token do GymRats (ver docs/token.md)
python login_new.py                 # e-mail/senha → .token-new.json
cp .token-new.json .token.json      # o sync lê de .token.json

# 2) uma API key (ver docs/api-keys.md)
python apikeys.py generate app-local

# 3) sincroniza e sobe a API
python sync.py
set GYMRATS_API_KEY=                 # (opcional) ou use a key do registro
uvicorn api:app --port 8000         # docs: http://127.0.0.1:8000/docs
```

Para produção, vá direto para **[deploy.md](deploy.md)**.
