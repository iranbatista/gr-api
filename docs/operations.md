# Operação

Manutenção do deploy: logs, atualização, backup, renovação de token e troubleshooting.

## Logs

```bash
cd ~/gr-api
docker compose ps                      # estado dos containers
docker compose logs -f gr-api          # API (uma linha por request)
docker compose logs -f gr-api-sync     # sync (ciclo de 60 min, erros do GymRats/Sheets)
```

Os logs têm rotação (`json-file`, 10 MB × 3 arquivos) — não crescem sem limite.

## Atualizar

```bash
cd ~/gr-api && git pull && docker compose up -d --build
```

## Backup do banco

O banco é só um arquivo e dá pra refazer do zero com um sync. Para guardar um snapshot
consistente (sem corromper durante uma escrita):

```bash
cd ~/gr-api
docker compose exec gr-api python -c "import sqlite3; s=sqlite3.connect('/data/gymrats.db'); s.backup(sqlite3.connect('/data/backup.db'))"
mv data/backup.db ~/gymrats-$(date +%F).db
```

## Renovar o token do GymRats

Quando o token expira, `gr-api-sync` loga **401** e a API segue servindo os últimos dados.
Gere um token novo na sua máquina (ver [token.md](token.md)) e troque no servidor:

```bash
cd ~/gr-api
sed -i '/^GYMRATS_TOKEN=/d' .env
read -rsp "Token GymRats: " T && echo "GYMRATS_TOKEN=$T" >> .env; unset T; echo
docker compose up -d        # 'restart' NÃO relê o .env; 'up -d' relê
```

## Gerenciar API keys

```bash
docker compose exec gr-api python apikeys.py list
docker compose exec gr-api python apikeys.py generate <nome>
docker compose exec gr-api python apikeys.py revoke <nome>
```

A API aplica mudanças do registro na hora, sem reiniciar. Detalhes em [api-keys.md](api-keys.md).

---

## Troubleshooting

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| **401** `X-API-Key ausente ou invalida` nas rotas de dados | Chave errada/ausente, ou foi revogada | Confira o header `X-API-Key`; veja as chaves ativas com `apikeys.py list`; gere uma nova se preciso |
| **401** no `gr-api-sync` (log) | Token do GymRats **expirou** | Renove o token (acima) |
| **403** `PERMISSION_DENIED` no export do Sheets | Planilha não compartilhada com a service account, ou Sheets API desabilitada | Ver [google-sheets.md](google-sheets.md) → Troubleshooting |
| **503** em `/health`, `gr-api` `unhealthy` | Banco ainda sem tabelas (antes do 1º sync) | Aguarde o `gr-api-sync` terminar o primeiro ciclo |
| **502** em `/workouts/{id}/comments` | `GYMRATS_TOKEN` não configurado na API, ou expirado | Garanta `GYMRATS_TOKEN` no `.env` do container `gr-api` e renove se preciso |
| **CORS** bloqueando o front | `GYMRATS_CORS` não inclui o domínio do app | Ajuste `GYMRATS_CORS` no `.env` e `docker compose up -d` |
| **API aberta** sem querer | Nem `GYMRATS_API_KEY` nem registro configurados | Defina a env key ou gere uma chave no registro (a API fica aberta só quando nada está configurado) |
| Imagens erradas/antigas no `/img` | Cache em disco | `rm -rf data/thumbs` |

## Rede / Caddy

- `gr-api` **não publica porta** no host — só o Caddy (rede `web`) alcança. Testar "de
  fora" é via `https://gr-api.SEUDOMINIO`, não `localhost:8000`.
- Problema de HTTPS/certificado: confira o DNS (registro A) e os logs do Caddy
  (`docker logs caddy`).
