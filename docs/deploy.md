# Deploy no servidor (Docker + Caddy)

Arquitetura em produção:

```
  gr-api-sync (loop 60min)  ──►  data/gymrats.db (SQLite)  ──►  gr-api (FastAPI)  ──►  Caddy+HTTPS  ──►  seu app
     puxa do GymRats                                              :8000 na rede "web"     :443
```

Dois containers da mesma imagem, compartilhando `./data` (bind mount):

- **`gr-api-sync`** — roda `sync.py`, dorme 1h, repete. Grava no SQLite.
- **`gr-api`** — FastAPI servindo o SQLite como REST, com API key e CORS. Fica na rede
  externa `web` e **não publica porta**: só o Caddy alcança.
- **Caddy** (já existente na VPS) — reverse proxy + HTTPS automático.

Os dois rodam como uid 1000 dentro do container.

---

## 1. DNS

Crie um registro **A** `gr-api.<seu domínio>` apontando para o IP da VPS. Faça isso
primeiro: o Caddy só consegue o certificado depois que o DNS propaga.

## 2. Código + segredos

Sem abrir editor. Não sobrescreve o que já existir no `.env`.

```bash
cd ~ && git clone https://github.com/iranbatista/gr-api.git && cd gr-api
touch .env && chmod 600 .env
grep -q '^GYMRATS_API_KEY=' .env || echo "GYMRATS_API_KEY=$(openssl rand -hex 32)" >> .env
grep -q '^GYMRATS_CORS=' .env    || echo "GYMRATS_CORS=https://seuapp.com" >> .env   # domínio do app
# cole o JWT (não aparece na tela) e dê Enter:
read -rsp "Token GymRats: " T && echo "GYMRATS_TOKEN=$T" >> .env; unset T; echo
cat .env    # confira as linhas
```

- `GYMRATS_TOKEN` — o JWT do GymRats (ver [token.md](token.md)). A renovação é feita na
  sua máquina (emulador/login) e trocada aqui quando expira.
- `GYMRATS_API_KEY` — a chave única (de emergência/admin). O `openssl rand -hex 32` gera
  uma aleatória. Você pode criar chaves por consumidor depois (passo 5).
- `GYMRATS_CORS` — domínio(s) do app, separados por vírgula.

## 3. Subir

```bash
mkdir -p data && sudo chown 1000:1000 data     # os containers rodam como uid 1000
docker compose up -d --build
docker compose ps                              # gr-api (healthy) e gr-api-sync (Up)
docker compose logs -f gr-api-sync             # acompanhe o 1º sync; Ctrl+C sai do log
```

Até o primeiro sync terminar, `/health` responde 503 (banco ainda sem tabelas) e o
`gr-api` aparece `unhealthy`. Resolve sozinho.

## 4. Caddy

Adicione o bloco do `Caddyfile.example` (com seu domínio) no `~/caddy/Caddyfile`:

```bash
cp ~/caddy/Caddyfile ~/caddy/Caddyfile.bak     # backup
printf '\ngr-api.SEUDOMINIO {\n\treverse_proxy gr-api:8000\n}\n' >> ~/caddy/Caddyfile   # troque SEUDOMINIO
docker exec caddy caddy validate --config /etc/caddy/Caddyfile
docker exec caddy caddy reload   --config /etc/caddy/Caddyfile
```

Se o `validate` reclamar: `cp ~/caddy/Caddyfile.bak ~/caddy/Caddyfile`.

## 5. API keys por consumidor (recomendado)

Além da chave única do `.env`, gere uma chave por app/pessoa — dá pra revogar cada uma
sem afetar as outras (ver [api-keys.md](api-keys.md)):

```bash
docker compose exec gr-api python apikeys.py generate app-react   # mostra o segredo 1x
docker compose exec gr-api python apikeys.py list
```

Entregue a chave ao consumidor. A API aplica na hora, sem reiniciar.

## 6. Testar

```bash
curl https://gr-api.SEUDOMINIO/health
curl -H "X-API-Key: SUA_CHAVE" https://gr-api.SEUDOMINIO/challenges
```

Swagger: `https://gr-api.SEUDOMINIO/docs`.

---

## Atualizar

```bash
cd ~/gr-api && git pull && docker compose up -d --build
```

## Export para Google Sheets (opcional)

Veja [google-sheets.md](google-sheets.md).

## Operação (backup, logs, troubleshooting, renovar token)

Veja [operations.md](operations.md).
