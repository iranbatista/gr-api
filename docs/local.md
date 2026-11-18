# Rodar local

Para desenvolver, testar ou usar na sua máquina, sem Docker.

## 1. Instalar dependências

```bash
python -m pip install -r requirements.txt -r requirements-api.txt
```

- `requirements.txt` — sync + dashboard + export Sheets (`requests`, `flask`, `gspread`…).
- `requirements-api.txt` — a API (`fastapi`, `uvicorn`, `Pillow`…).
- `requirements-dev.txt` — testes (`pytest`), opcional.

## 2. Token do GymRats

Obtenha o token (ver [token.md](token.md)) e deixe-o em `.token.json`:

```bash
python login_new.py && cp .token-new.json .token.json   # e-mail/senha
# ou, via emulador: python extract_token.py datastore_dump/authentication.preferences_pb
```

## 3. Sincronizar

Puxa desafios, membros, rankings e check-ins para `gymrats.db` (incremental, 1 req/s):

```bash
python sync.py
```

Rode quando quiser atualizar. O banco é idempotente (UPSERT por id).

## 4a. API REST

```bash
# (opcional) proteger com uma chave do registro:
python apikeys.py generate app-local

uvicorn api:app --reload --port 8000
```

- Docs interativas (Swagger): <http://127.0.0.1:8000/docs>
- Sem `GYMRATS_API_KEY` e sem registro, a API fica **aberta** em local (sem fricção).
- Com uma chave, mande `X-API-Key: <chave>` nas chamadas de dados.

Teste:

```bash
curl http://127.0.0.1:8000/health
curl -H "X-API-Key: grk_..." http://127.0.0.1:8000/challenges
```

## 4b. Dashboard local (Flask)

Alternativa visual rápida, sem precisar de front-end:

```bash
python app.py        # http://127.0.0.1:5000
```

## Variáveis úteis (local)

| Variável | Default | Para quê |
|---|---|---|
| `GYMRATS_DB` | `./gymrats.db` | caminho do SQLite |
| `GYMRATS_API_KEY` | — | chave única (se quiser proteger) |
| `GYMRATS_API_KEYS_FILE` | `./apikeys.json` | registro multi-key |
| `GYMRATS_CORS` | `*` | origens permitidas (CORS) |
| `GYMRATS_TZ` | `America/Sao_Paulo` | fuso para agrupar check-ins por dia |
| `GYMRATS_TOKEN` | lê `.token.json` | token p/ `/workouts/{id}/comments` |

## Testes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

---

**Próximo:** [deploy no servidor](deploy.md).
