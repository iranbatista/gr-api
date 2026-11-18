# gr-api

API REST pessoal (somente leitura) sobre os desafios do **GymRats** de que você participa,
com um sincronizador que alimenta um SQLite e um dashboard local.

> Uso pessoal. Consome a API privada do app GymRats com o **seu próprio token**.
> Não enumera dados de terceiros; só os grupos de que você é membro.

## 📖 Documentação completa

A documentação de ponta a ponta — de como **pegar as credenciais** até **subir a API num
servidor** — está em **[`docs/`](docs/README.md)**:

| | |
|---|---|
| [Visão geral + as duas credenciais](docs/README.md) | [Token do GymRats](docs/token.md) · [API keys](docs/api-keys.md) |
| [Rodar local](docs/local.md) | [Deploy (Docker + Caddy)](docs/deploy.md) |
| [Google Sheets](docs/google-sheets.md) | [Referência da API](docs/api-reference.md) · [Operação](docs/operations.md) |

## Componentes

| Arquivo | O que é |
|---|---|
| `sync.py` | Ingestão: puxa desafios, membros, rankings e check-ins do GymRats → `gymrats.db` (SQLite). Read-only, 1 req/s, incremental. |
| `api.py` | **REST API** (FastAPI) que serve o SQLite. CORS, API keys, paginação, filtros, `/stats`, `/insights`, Swagger em `/docs`. |
| `apikeys.py` | Gera e gerencia as API keys que protegem a API (`generate`/`list`/`revoke`; guarda só o hash). |
| `sheets.py` | Export opcional dos check-ins do desafio ativo para o Google Sheets. |
| `app.py` + `templates/` | Dashboard local (Flask) para visualizar rápido em `127.0.0.1:5000`. |
| `extract_token.py` / `login_new.py` | Obtêm o JWT do GymRats (via emulador / via e-mail e senha). |
| `Dockerfile` + `docker-compose.yml` | Deploy na VPS: container da API + container do sync (loop de 60 min), SQLite em `./data`. |
| `Caddyfile.example` | Bloco para o Caddy da VPS (reverse proxy + HTTPS). |
| `docs/` | **Documentação completa** (começe por [`docs/README.md`](docs/README.md)). |
| `REPORT.md` | Como a API foi descoberta (engenharia reversa do app). |

## Rodar local

```bash
python -m pip install -r requirements.txt -r requirements-api.txt

# 1) obter o token do GymRats (ver docs/token.md) e salvar em .token.json
python login_new.py && cp .token-new.json .token.json

# 2) (opcional) gerar uma API key (ver docs/api-keys.md)
python apikeys.py generate app-local

# 3) sincronizar os dados
python sync.py

# 4a) API REST
uvicorn api:app --port 8000      # docs: http://127.0.0.1:8000/docs

# 4b) ou o dashboard local
python app.py                    # http://127.0.0.1:5000
```

Detalhes em [`docs/local.md`](docs/local.md).

## Configuração (env)

Veja `.env.example`. Principais: `GYMRATS_TOKEN`, `GYMRATS_API_KEY`, `GYMRATS_CORS`,
`GYMRATS_DB`. API keys por consumidor (registro multi-key) em [`docs/api-keys.md`](docs/api-keys.md).

## Deploy no servidor

Veja [`docs/deploy.md`](docs/deploy.md): Docker Compose (API + sync de 60 em 60 min) atrás
do Caddy, API keys, e a [referência da API](docs/api-reference.md) com exemplos no React.

## Segredos

`.token.json`, `gymrats.db`, `data/`, `.env`, `datastore_dump/` e dumps **não** são versionados
(ver `.gitignore`). Nunca comite seu token nem o banco (contém dados dos membros do grupo).
