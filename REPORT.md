# GymRats — Engenharia reversa para sincronização automática

**Objetivo:** alimentar um dashboard pessoal, sozinho, com os dados dos grupos/desafios
dos quais **você já é membro**. Só leitura.

**Data da análise:** 2026-10-27
**Artefato analisado:** `GymRats · Fitness challenge_2026.10.25_APKPure.xapk`
(`com.hasz.gymrats.app`, base APK 55 MB + split `arm64_v8a`).

> **Como ler este documento:** cada afirmação é marcada como
> **[OBS]** observado diretamente no artefato, ou **[INF]** inferência a confirmar
> na primeira execução. Nada aqui foi testado contra a API com a sua conta.

---

## 1. Caminho escolhido

| Passo do seu plano | Resultado |
|---|---|
| **1. API oficial / webhooks** | ❌ Não existe nada público. Site, help center e subdomínios não expõem API, developers nem webhooks. A única "exportação" é um recurso manual do **GymRats Pro** ("Export group data") — exatamente o fluxo que você **não** quer. |
| **2. API do app via versão web** | ❌ Não há versão web. `api/app/web.gymrats.app` não resolvem. O app é **nativo** (React Native). Não há front-end web para instrumentar. |
| **3. Análise estática do APK** | ✅ **Caminho adotado.** O app é **React Native + Hermes**; a lógica (endpoints, auth) está no bundle JS `assets/index.android.bundle` (bytecode Hermes v98). A **tabela de strings do Hermes fica em claro**, o que revelou host, endpoints e nomes de campos sem precisar de DEX/JADX nem de runtime. |

**Conclusão:** a automação é viável consumindo a **mesma API REST privada** que o app usa.
É um backend **Elixir/Phoenix hospedado no Gigalixir**.

---

## 2. Backend e host  ⚠️ CORRIGIDO após teste ao vivo

O bundle tem uma lista de ambientes com rótulos:
- **[OBS]** `Staging (gym-rats-api-pre-production.gigalixirapp.com)` — **desativado**:
  hoje responde "Gigalixir - Default Backend" (nenhum app GymRats rodando ali).
- **[OBS]** `Production (www.gymrats.app)` — **este é o real**.

**[OBS] Base de produção, confirmada ao vivo:** `https://www.gymrats.app/api`
(a API fica no mesmo domínio do site, sob o prefixo `/api`).
- `GET https://www.gymrats.app/api/challenges/` → **401**
  `{"error":"...Try signing in again.","status":"failure"}` (rota existe, exige token).
- Envelope de resposta: `{"status":"success"|"failure", ...}` / `{"data": ...}`.
- **[OBS]** CDN de mídia/fotos: `https://cdn.gymrats.app`.
- **[OBS]** Framework: Phoenix/Elixir, rotas REST snake_case, chat via WebSocket
  (`wss://.../socket`, Phoenix Channels).
- **[OBS]** `api.gymrats.app` → NXDOMAIN (não existe). Não use.

---

## 3. Autenticação (o ponto crítico)

### Endpoints de login — **[OBS]** (existem, caminhos sob `/api`)
- `POST /api/accounts/sign_in` (e-mail/senha) — **[OBS]** existe, mas o **formato do corpo
  não é** `{email,password}` cru (isso retorna **400 Bad Request**). O formato exato do
  payload **não foi determinado** (não dá para inferir com segurança do bytecode Hermes, e
  sondar várias combinações contra um endpoint de login é brute-force — não foi feito).
- `POST /api/accounts/phone_sign_in` + `/api/accounts/phone_confirmation` (SMS/OTP).
- Também há `signInWithGoogle` (login social). **[OBS]** Sem Firebase/Auth0 embutido
  (nenhuma `AIza...`/`firebaseapp.com` no bundle).

### Como o token é enviado — **[OBS]**
- Header `Authorization` com o **token cru** (não há `"Bearer "` no bundle → **sem** prefixo).
- **[INF]** JWT do Guardian (Phoenix).

### Duração e renovação — **[OBS]**
- **Não existe** `refresh_token` nem endpoint de refresh. `POST /token` é registro de
  **device/push token** (notificações), não renovação.
- **[INF]** Token **único, de vida longa** (Guardian: semanas a meses). Sem renovação
  silenciosa.

### Estratégia adotada — **autenticação por token (JWT) extraído do app**
Como (a) não há refresh token e (b) o corpo exato do `sign_in` é desconhecido, reproduzir
o login é frágil e arriscado para a conta. A via **limpa e 100% read-only**: reaproveitar
o **JWT que o app já guarda** depois que você loga nele.

1. Você extrai o JWT do app **uma vez** e cola no frontend (ou define `GYMRATS_TOKEN`).
2. O token é cacheado localmente (fora do git) e reusado em todas as leituras.
3. No **401** (token expirou — raro, é de vida longa), o app avisa e você cola um novo.

**Como obter o JWT (duas opções):**
- **Captura de tráfego (recomendado, celular sem root):** rode o app atrás de um proxy
  (mitmproxy / HTTP Toolkit), navegue, e copie o header `Authorization` das chamadas a
  `www.gymrats.app/api`. ⚠️ Pode haver **certificate pinning** — não contornar sem combinar.
  Bônus: essa captura também revela o **corpo exato do `sign_in`**, caso você queira
  automação total (login programático) no futuro.
- **Root / emulador:** ler o DataStore do app em
  `/data/data/com.hasz.gymrats.app/files/datastore/`.

---

## 4. Endpoints de leitura (os que interessam ao dashboard)

Todos **GET**, com header `Authorization: <token>`. Rotas **[OBS]** no bundle;
parâmetros de query **[INF]** (confirmar no 1º uso).

| Dado | Endpoint | Observações |
|---|---|---|
| Meus desafios/clubes | `GET /challenges/` | lista desafios e clubes de que participo |
| Detalhe + **regras de pontuação** | `GET /challenges/:id` | inclui `challenge_settings`, `scoring_limit_settings`, `verification_settings` |
| Membros | `GET /challenges/:id/members` | lista de membros |
| Membro (detalhe) | `GET /challenges/:id/members/:memberId` | |
| **Ranking / placar** | `GET /challenges/:id/rankings` | leaderboard do desafio |
| Ranking por período | `GET /challenges/:id/periodic_rankings` | semanal/diário conforme o desafio |
| Estatísticas do grupo | `GET /challenges/:id/group_stats` | |
| **Feed de check-ins** | `GET /feed` e `GET /challenges/:id/...` | check-ins com foto/descrição |
| Check-in (detalhe) | `GET /workouts/:workoutId` | data/hora, atividade, duração, distância, calorias, pontos, fotos |
| Métricas de check-in | `GET /metrics/check_ins`, `GET /metrics/daily` | agregados |
| Contadores | `GET /check_ins_counters`, `GET /bests/...` | |

**[OBS]** No app, um "check-in" é um **workout**. **[OBS]** Filtros de query presentes no
bundle: `challenge_id`, `account_id`, `member_id`, `start_date`, `end_date`, `date`.
→ Dá para fazer **sync incremental por data** (`start_date`/`end_date`) e por membro.

**[OBS]** Paginação: há `cursor` e `?offset=` no bundle → provável paginação por cursor
e/ou offset nas listas grandes (feed/workouts).

### Modelo de dados (campos observados no bundle) — **[OBS]**
- **Workout/check-in:** `date`/`day`, tipo de atividade (`activity_types`, `platform_activities`),
  `number_of_workouts`, `total_workouts`, calorias (`most_calories`), `best_sets`,
  curtidas (`most_liked`), salvos (`most_saved`), fotos (via `cdn.gymrats.app`), descrição.
- **Ranking:** entradas por membro/equipe (`workouts_team`), pontos, posição.
- **Challenge:** regras de verificação e limites de pontuação nas `*_settings`.

> Os nomes exatos de cada campo JSON você confirma na 1ª resposta real (o protótipo
> faz dump do JSON cru do primeiro desafio para você inspecionar).

---

## 5. Limites e etiqueta

- **Só leitura:** o protótipo usa **apenas GET** (+ o `POST /accounts/sign_in` para
  autenticar). Nunca cria/edita/apaga check-ins, comentários ou nada.
- **Só seus grupos:** itera a partir de `GET /challenges/` (o que a sua conta vê).
  Nada de enumerar IDs de terceiros.
- **Rate limit [INF]:** não há número no bundle. Trate com calma: **1 req/s**, sem rajadas,
  e respeite `429`/`Retry-After` se vier.
- **Intervalo de sync sugerido:** a cada **30–60 min** é mais que suficiente para um
  desafio (check-ins não chegam em segundos). Comece com **60 min**.
- **Segredos:** senha no `keyring` do SO; JWT em cache fora do git; **tokens mascarados**
  em todo log.

---

## 6. Arquitetura proposta (simples e autônoma)

```
                 keyring (SO)          cache/token.json (fora do git)
                 e-mail+senha                 JWT
                     │                          │
                     ▼                          ▼
  ┌──────────────────────────────────────────────────────┐
  │  sync.py  (roda a cada 60 min: Agendador do Windows)   │
  │  1. garante JWT (login se faltar/401)                  │
  │  2. GET /challenges  → para cada desafio:              │
  │       GET detalhe, members, rankings                  │
  │       GET workouts/feed  (incremental por start_date) │
  │  3. UPSERT em SQLite (idempotente por id)             │
  │  4. grava last_sync por desafio (cursor de data)      │
  └───────────────────────┬──────────────────────────────┘
                          ▼
                   gymrats.db (SQLite)
                          ▼
             dashboards (Grafana / Metabase / app próprio)
```

- **Agendamento:** Agendador de Tarefas do Windows chamando `python sync.py`
  (ou um loop com `schedule`). Autônomo.
- **Banco:** SQLite (`gymrats.db`) — tabelas `challenges`, `members`, `workouts`,
  `rankings`, mais `sync_state` (guarda o último `start_date` sincronizado por desafio
  para buscar **só o que mudou**).
- **Incremental:** por `start_date`/`end_date`. Fallback: buscar últimos N dias sempre e
  deixar o UPSERT deduplicar por `id`.
- **Dashboards:** aponte Grafana/Metabase para o SQLite, ou construa telas próprias.

Protótipo pronto em **`sync.py`** (ver seção 7).

---

## 7. Protótipo mínimo

Arquivos neste diretório:
- `sync.py` — sincronizador (login auto, GET read-only, UPSERT SQLite, incremental, token mascarado).
- `requirements.txt` — `requests`, `keyring`.

**Primeira execução (uma vez, para guardar a senha no cofre do SO):**
```powershell
pip install -r requirements.txt
python -c "import keyring; keyring.set_password('gymrats','SEU_EMAIL','SUA_SENHA')"
$env:GYMRATS_EMAIL = "SEU_EMAIL"
python sync.py --dump   # loga, lista desafios e salva o JSON cru p/ você conferir os campos
```
Depois, rodadas normais:
```powershell
python sync.py          # incremental, popula o SQLite
```

> O `--dump` existe para você **validar os [INF]**: ele imprime/salva o JSON real do
> primeiro desafio e de um workout, para você confirmar os nomes exatos dos campos e,
> se preciso, ajustar o mapeamento. Faça 1 request por vez, sem pressa.

---

## 8. Resumo em uma linha

Não há API oficial nem versão web; o app é React Native/Hermes e conversa com uma API
REST Phoenix em **`https://www.gymrats.app/api`**, autenticada por **JWT de vida longa sem
refresh token** no header `Authorization` (token cru, sem `Bearer`). A automação viável:
**extrair o JWT do app uma vez, cachear localmente e reusar**, puxando os dados read-only a
cada ~60 min para um SQLite que alimenta os dashboards; no 401 (raro), extrair um token novo.
