# Deploy da GymRats API no VPS (Docker + Caddy)

Arquitetura:

```
  gr-api-sync (loop 60min)  ──►  data/gymrats.db (SQLite)  ──►  gr-api (FastAPI)  ──►  Caddy+HTTPS  ──►  seu app React
     puxa do GymRats                                              :8000 na rede "web"     :443
```

Dois containers da mesma imagem, compartilhando `./data` (bind mount):

- **`gr-api-sync`:** roda `sync.py`, dorme 1h, repete. Grava no SQLite.
- **`gr-api`:** FastAPI servindo o SQLite como REST, com API key e CORS. Está na
  rede externa `web` e **não publica porta**: só o Caddy alcança.
- **Caddy** (já existente na VPS): reverse proxy + HTTPS automático.

Os dois rodam como uid 1000 dentro do container.

---

## 1. DNS

Crie um registro **A** `gr-api.<seu domínio>` apontando para o IP da VPS. Faça
isso primeiro: o Caddy só consegue o certificado depois que o DNS propaga.

## 2. Código + segredos

Tudo sem abrir editor. Não sobrescreve o que já existir no `.env`.

```bash
cd ~ && git clone https://github.com/iranbatista/gr-api.git && cd gr-api
touch .env && chmod 600 .env
grep -q '^GYMRATS_API_KEY=' .env || echo "GYMRATS_API_KEY=$(openssl rand -hex 32)" >> .env
grep -q '^GYMRATS_CORS=' .env    || echo "GYMRATS_CORS=https://seuapp.com" >> .env   # troque pelo domínio do React
# cole o JWT (não aparece na tela) e dê Enter:
read -rsp "Token GymRats: " T && echo "GYMRATS_TOKEN=$T" >> .env; unset T; echo
cat .env    # confira as três linhas
```

- `GYMRATS_TOKEN`: o JWT extraído do app (ver README). A renovação continua sendo
  feita na sua máquina via emulador (ver final deste arquivo).
- `GYMRATS_API_KEY`: a chave que o React manda no header `X-API-Key`.
- `GYMRATS_CORS`: o(s) domínio(s) do app React, separados por vírgula.

## 3. Subir

```bash
mkdir -p data && sudo chown 1000:1000 data     # os containers rodam como uid 1000
docker compose up -d --build
docker compose ps                              # gr-api (healthy) e gr-api-sync (Up)
docker compose logs -f gr-api-sync             # acompanhe o 1º sync; Ctrl+C sai do log
```

Até o primeiro sync terminar, o `/health` responde 503 (banco ainda sem tabelas)
e o `gr-api` aparece como `unhealthy`. Isso se resolve sozinho.

## 4. Caddy

Adicione o bloco do `Caddyfile.example` (com seu domínio) no `~/caddy/Caddyfile`.
Sem editor de terminal:

```bash
cp ~/caddy/Caddyfile ~/caddy/Caddyfile.bak     # backup
printf '\ngr-api.SEUDOMINIO {\n\treverse_proxy gr-api:8000\n}\n' >> ~/caddy/Caddyfile   # troque SEUDOMINIO
docker exec caddy caddy validate --config /etc/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Se o `validate` reclamar, volte o backup: `cp ~/caddy/Caddyfile.bak ~/caddy/Caddyfile`.

## 5. Testar

```bash
curl https://gr-api.SEUDOMINIO/health
curl -H "X-API-Key: SUA_CHAVE" https://gr-api.SEUDOMINIO/challenges
```
Docs interativas (Swagger): `https://gr-api.SEUDOMINIO/docs`

## Atualizar

```bash
cd ~/gr-api && git pull && docker compose up -d --build
```

## Backup

O banco é só um arquivo, e dá pra refazer do zero com um sync. Se quiser guardar:

```bash
docker compose exec gr-api python -c "import sqlite3; s=sqlite3.connect('/data/gymrats.db'); s.backup(sqlite3.connect('/data/backup.db'))"
mv data/backup.db ~/gymrats-$(date +%F).db
```

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | status (público) |
| GET | `/challenges` | seus desafios |
| GET | `/challenges/{id}` | um desafio |
| GET | `/challenges/{id}/members` | membros |
| GET | `/challenges/{id}/rankings` | ranking (rank, score, account) |
| GET | `/challenges/{id}/workouts` | check-ins (com reações embutidas) — `?limit=&offset=&member_id=&since=&until=` |
| GET | `/workouts/{id}` | um check-in (reações + `comment_count`) |
| GET | `/workouts/{id}/comments` | comentários do check-in — busca no GymRats sob demanda (cache ~60s); **requer `GYMRATS_TOKEN` na API** |
| GET | `/challenges/{id}/stats` | agregados p/ dashboard (por dia, por membro) — `?until=YYYY-MM-DD` corta por dia local |
| GET | `/challenges/{id}/insights` | agregados ricos p/ dashboard: série diária, punchcard 7×24, períodos, duração por dia da semana, sequências, prêmios, top check-ins, emojis (fuso `GYMRATS_TZ`) — `?until=YYYY-MM-DD` corta por dia local |
| GET | `/meta/sync` | última sincronização |
| GET | `/img?u=<url>&w=<64\|128\|256\|512\|1024>` | miniatura WebP de uma imagem do CDN (público; só `https://cdn.gymrats.app/...` e larguras da lista, senão 400). Gerada uma vez e cacheada em `data/thumbs` (`Cache-Control: immutable` + `ETag`/304). Limpar o cache: `rm -rf data/thumbs` |

> **Reações** já vêm embutidas em cada check-in (campo `reactions`: quem reagiu + emoji).
> **Comentários** não são sincronizados no banco (seriam ~1 request por check-in); o endpoint
> `/workouts/{id}/comments` os busca no GymRats na hora, com cache curto. Por isso o container
> `gr-api` também recebe o `GYMRATS_TOKEN` (ver `docker-compose.yml`).

Todas (menos `/health` e `/img`) exigem header `X-API-Key` se `GYMRATS_API_KEY` estiver setado.
`/img` é público porque `<img src>` não manda header; a allowlist de host/largura evita que vire
proxy aberto ou alvo de resize arbitrário. Ex.: `<img src={`${API}/img?u=${encodeURIComponent(url)}&w=128`} width={40} height={40}>`
(use ~2–3× o tamanho exibido para telas retina).

---

## Consumindo no React

```js
const API = "https://gr-api.seudominio.com.br";
const KEY = import.meta.env.VITE_GYMRATS_KEY;   // guarde a chave em env, nao no codigo

const api = (path) =>
  fetch(`${API}${path}`, { headers: { "X-API-Key": KEY } }).then((r) => {
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  });

// exemplos
const challenges = await api("/challenges");
const ranking    = await api(`/challenges/${id}/rankings`);
const page       = await api(`/challenges/${id}/workouts?limit=50&offset=0`);
const stats      = await api(`/challenges/${id}/stats`);
```

> Observação de segurança: uma API key embutida num app React (SPA) fica visível pra quem
> inspecionar o bundle. Para uso pessoal num domínio seu e CORS restrito, é aceitável. Se
> for abrir pra mais gente, coloque um backend-for-frontend ou autenticação de usuário.

---

## Renovação do token (quando expirar → 401 no sync)

O JWT é de vida longa. Quando expirar, `docker compose logs gr-api-sync` mostra o 401
e a API continua servindo os últimos dados sincronizados.

1. Na sua máquina, reabra o app no emulador e extraia um token novo
   (`adb pull … authentication.preferences_pb` + `python extract_token.py …`).
2. Na VPS, troque o token no `.env` e recrie o sync. `restart` não relê o `.env`; `up -d` relê:
   ```bash
   cd ~/gr-api
   sed -i '/^GYMRATS_TOKEN=/d' .env
   read -rsp "Token GymRats: " T && echo "GYMRATS_TOKEN=$T" >> .env; unset T; echo
   docker compose up -d
   ```

> Dica: dá pra automatizar essa renovação com um emulador no próprio VPS, mas é bem mais
> trabalhoso. Para uso pessoal, renovar manualmente a cada expiração costuma bastar.
