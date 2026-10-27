# gr-api

API REST pessoal (somente leitura) sobre os desafios do **GymRats** de que você participa,
com um sincronizador que alimenta um SQLite e um dashboard local.

> Uso pessoal. Consome a API privada do app GymRats com o **seu próprio token**.
> Não enumera dados de terceiros; só os grupos de que você é membro.

## Componentes

| Arquivo | O que é |
|---|---|
| `sync.py` | Ingestão: puxa desafios, membros, rankings e check-ins do GymRats → `gymrats.db` (SQLite). Read-only, 1 req/s, incremental. |
| `api.py` | **REST API** (FastAPI) que serve o SQLite. CORS, API key, paginação, filtros, `/stats`, Swagger em `/docs`. |
| `app.py` + `templates/` | Dashboard local (Flask) para visualizar rápido em `127.0.0.1:5000`. |
| `extract_token.py` | Extrai o JWT do `authentication.preferences_pb` (DataStore do app) para `.token.json`. |
| `deploy/` | systemd (API + timer de sync) e nginx para subir num VPS. |
| `DEPLOY.md` | Guia de deploy no VPS + exemplo de consumo no React. |
| `REPORT.md` | Como a API foi descoberta (engenharia reversa do app). |

## Rodar local

```bash
python -m pip install -r requirements.txt -r requirements-api.txt

# 1) obter o token (ver REPORT.md / DEPLOY.md): extrair do app e salvar em .token.json
python extract_token.py datastore_dump/authentication.preferences_pb

# 2) sincronizar os dados
python sync.py

# 3a) API REST
uvicorn api:app --port 8000      # docs: http://127.0.0.1:8000/docs

# 3b) ou o dashboard local
python app.py                    # http://127.0.0.1:5000
```

## Configuração (env)

Veja `.env.example`. Principais: `GYMRATS_TOKEN`, `GYMRATS_API_KEY`, `GYMRATS_CORS`, `GYMRATS_DB`.

## Deploy no VPS

Veja [`DEPLOY.md`](DEPLOY.md): venv + systemd (API e timer de sync de 60 min) + nginx/HTTPS,
e um exemplo de `fetch` para consumir no React.

## Segredos

`.token.json`, `gymrats.db`, `.env`, `datastore_dump/` e dumps **não** são versionados
(ver `.gitignore`). Nunca comite seu token nem o banco (contém dados dos membros do grupo).
