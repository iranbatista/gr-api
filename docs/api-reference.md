# Referência da API

Base: `https://gr-api.SEUDOMINIO` (produção) ou `http://127.0.0.1:8000` (local).
Documentação interativa (Swagger, gerada do código): **`/docs`**.

## Autenticação

Toda rota de **dados** exige o header `X-API-Key: <chave>` quando há auth configurada
(ver [api-keys.md](api-keys.md)). `/health` e `/img` são **públicos**.

```
X-API-Key: grk_ZyhrD8xVZSMhF7hx-CdTLQGrRqmC6aeUpju6eVcpS3Q
```

Sem chave (ou chave inválida) numa rota protegida → **401**.

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | status do banco (público) |
| GET | `/challenges` | seus desafios (mais recentes primeiro) |
| GET | `/challenges/{id}` | um desafio |
| GET | `/challenges/{id}/members` | membros |
| GET | `/challenges/{id}/rankings` | ranking (`rank`, `score`, `account`) |
| GET | `/challenges/{id}/workouts` | check-ins (reações embutidas) — `?limit=&offset=&member_id=&since=&until=` |
| GET | `/workouts/{id}` | um check-in (reações + `comment_count`) |
| GET | `/workouts/{id}/comments` | comentários do check-in — busca no GymRats sob demanda (cache ~60s); **requer `GYMRATS_TOKEN` na API** |
| GET | `/challenges/{id}/stats` | agregados p/ dashboard (por dia, por membro) — `?until=YYYY-MM-DD` |
| GET | `/challenges/{id}/insights` | agregados ricos: série diária, punchcard 7×24, períodos, duração por dia da semana, sequências, prêmios, top check-ins, emojis — `?until=YYYY-MM-DD` |
| GET | `/meta/sync` | última sincronização conhecida |
| GET | `/img?u=<url>&w=<64\|128\|256\|512\|1024>` | miniatura WebP de imagem do CDN (público; só `https://cdn.gymrats.app/...` e larguras da lista) |

Notas:

- **Paginação** em `/workouts`: `limit` (1–1000, default 100) + `offset`. A resposta traz
  `items`, `total`, `limit`, `offset`.
- **Filtros** em `/workouts`: `member_id` (account_id), `since`/`until` (data/datetime ISO).
- **Reações** já vêm embutidas em cada check-in (`reactions`: quem reagiu + emoji).
- **Comentários** não são sincronizados (seriam ~1 request por check-in); `/workouts/{id}/comments`
  os busca no GymRats na hora, com cache curto. Por isso o container `gr-api` também recebe
  o `GYMRATS_TOKEN`.
- **`/img`** é cacheado em disco (`data/thumbs`, `Cache-Control: immutable` + `ETag`/304).
  É público porque `<img src>` não manda header; a allowlist de host/largura evita proxy
  aberto ou resize arbitrário. Limpar cache: `rm -rf data/thumbs`. Use ~2–3× o tamanho
  exibido (telas retina):
  `<img src={`${API}/img?u=${encodeURIComponent(url)}&w=128`} width={40} height={40}>`.
- Os fusos de `/stats` e `/insights` seguem `GYMRATS_TZ` (default `America/Sao_Paulo`).

---

## Consumindo no React

```js
const API = "https://gr-api.seudominio.com.br";
const KEY = import.meta.env.VITE_GYMRATS_KEY;   // a chave vai em env, não no código

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
const insights   = await api(`/challenges/${id}/insights`);
```

### Imagens (miniaturas)

```jsx
const thumb = (url, w = 128) =>
  `${API}/img?u=${encodeURIComponent(url)}&w=${w}`;

<img src={thumb(member.profile_picture_url, 128)} width={40} height={40} />
```

> **Segurança (SPA):** uma API key embutida num bundle React fica visível para quem
> inspeciona o código. Para uso pessoal num domínio seu com CORS restrito, é aceitável.
> Para abrir a mais gente, use um backend-for-frontend ou autenticação de usuário, e trate
> a key como segredo de servidor. Veja também as boas práticas em [api-keys.md](api-keys.md).

---

## Contrato / tipos

Os modelos de resposta (OpenAPI) são gerados pelo FastAPI e ficam em **`/docs`** (Swagger)
e **`/openapi.json`** — útil para gerar tipos TypeScript no cliente (ex.: `openapi-typescript`).
