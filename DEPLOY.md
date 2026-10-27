# Deploy da GymRats API no VPS

Arquitetura:

```
  sync.py (timer 60min)  ──►  gymrats.db (SQLite)  ──►  api.py (FastAPI/uvicorn)  ──►  nginx+HTTPS  ──►  seu app React
     puxa do GymRats                                      :8000 (so localhost)         :443
```

- **Ingestão:** `sync.py` roda a cada 60 min (systemd timer), puxa do GymRats e grava no SQLite.
- **API:** `api.py` (FastAPI) serve o SQLite como REST, com API key e CORS.
- **nginx:** reverse proxy + HTTPS; só ele fica exposto. A API escuta só em `127.0.0.1`.

---

## 1. Servidor (Ubuntu/Debian)

```bash
sudo adduser --system --group gymrats
sudo mkdir -p /opt/gymrats && sudo chown gymrats:gymrats /opt/gymrats
sudo apt update && sudo apt install -y python3-venv nginx
```

Copie para `/opt/gymrats`: `api.py`, `sync.py`, `requirements.txt`, `requirements-api.txt`.

```bash
cd /opt/gymrats
python3 -m venv venv
./venv/bin/pip install -r requirements.txt -r requirements-api.txt
```

## 2. Configuração (.env)

```bash
cp .env.example .env && nano .env     # preencha GYMRATS_TOKEN, GYMRATS_API_KEY, GYMRATS_CORS, GYMRATS_DB
chmod 600 .env
```

- `GYMRATS_TOKEN` — o JWT extraído do app (ver README; renovação continua sendo feita na sua máquina via emulador).
- `GYMRATS_API_KEY` — gere uma chave longa: `openssl rand -hex 32`.
- `GYMRATS_CORS` — o(s) domínio(s) do seu app React.
- `GYMRATS_DB=/opt/gymrats/gymrats.db`.

## 3. Primeira carga

```bash
cd /opt/gymrats && set -a && . ./.env && set +a && ./venv/bin/python sync.py
```

## 4. Serviços (systemd)

```bash
sudo cp deploy/gymrats-api.service   /etc/systemd/system/
sudo cp deploy/gymrats-sync.service  /etc/systemd/system/
sudo cp deploy/gymrats-sync.timer    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gymrats-api.service     # sobe a API
sudo systemctl enable --now gymrats-sync.timer      # agenda o sync de 60 em 60 min
# conferir:
systemctl status gymrats-api
systemctl list-timers | grep gymrats
```

## 5. nginx + HTTPS

```bash
sudo cp deploy/nginx-gymrats.conf /etc/nginx/sites-available/gymrats
sudo ln -s /etc/nginx/sites-available/gymrats /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-available/gymrats   # ajuste server_name
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.seudominio.com
```

## 6. Testar

```bash
curl https://api.seudominio.com/health
curl -H "X-API-Key: SUA_CHAVE" https://api.seudominio.com/challenges
```
Docs interativas (Swagger): `https://api.seudominio.com/docs`

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | status (público) |
| GET | `/challenges` | seus desafios |
| GET | `/challenges/{id}` | um desafio |
| GET | `/challenges/{id}/members` | membros |
| GET | `/challenges/{id}/rankings` | ranking (rank, score, account) |
| GET | `/challenges/{id}/workouts` | check-ins — `?limit=&offset=&member_id=&since=&until=` |
| GET | `/challenges/{id}/stats` | agregados p/ dashboard (por dia, por membro) |
| GET | `/meta/sync` | última sincronização |

Todas (menos `/health`) exigem header `X-API-Key` se `GYMRATS_API_KEY` estiver setado.

---

## Consumindo no React

```js
const API = "https://api.seudominio.com";
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

O JWT é de vida longa, mas quando expirar:
1. Na sua máquina, reabra o app no emulador e extraia um token novo
   (`adb pull … authentication.preferences_pb` + `python extract_token.py …`).
2. Copie o novo valor para `GYMRATS_TOKEN` no `/opt/gymrats/.env` do VPS.
3. `sudo systemctl start gymrats-sync.service` (ou espere o próximo ciclo).

> Dica: dá pra automatizar essa renovação com um emulador no próprio VPS, mas é bem mais
> trabalhoso. Para uso pessoal, renovar manualmente a cada expiração costuma bastar.
