# Gerar e gerenciar API keys

A API key protege a **sua** API de quem não deve acessá-la. Ao contrário do
[token do GymRats](token.md), ela **não vem de lugar nenhum** — **você a gera**, quantas
quiser. O cliente (app React, dashboard, script) a manda no header `X-API-Key`.

A API aceita chaves de **duas fontes**, que funcionam em paralelo:

1. **Chave única** — a variável de ambiente `GYMRATS_API_KEY`. Simples; uma só chave.
2. **Registro multi-key** — o `apikeys.py` gera uma chave por consumidor (`app-react`,
   `dashboard`, …), guardando só o **hash**. Dá pra listar e **revogar** cada uma sem
   mexer nas outras. É o recomendado quando mais de um app/pessoa usa a API.

Pode usar as duas ao mesmo tempo: a chave única continua valendo e você adiciona chaves
do registro ao lado dela.

---

## Como funciona (resumo)

- `apikeys.py generate <nome>` cria uma chave `grk_…` aleatória (256 bits), mostra o
  **segredo uma única vez** e guarda no registro apenas o **SHA-256** dela — o texto da
  chave nunca toca o disco.
- A API, a cada request, compara o `X-API-Key` recebido com `GYMRATS_API_KEY` e com os
  hashes das chaves **ativas** do registro. Bate em alguma → libera; senão → **401**.
- `revoke` marca a chave como revogada; ela para de funcionar em segundos (a API relê o
  registro quando o arquivo muda, sem reiniciar).

Segurança: como as chaves têm alta entropia, guardar o SHA-256 é suficiente (não é senha
de humano; não precisa de bcrypt/argon2). O registro só contém hashes + metadados, então
mesmo vazado não revela nenhuma chave.

---

## CLI

```bash
# criar uma chave para um consumidor (mostra o segredo UMA vez — guarde na hora)
python apikeys.py generate app-react

# listar (nunca mostra o segredo; só nome, prefixo, data e status)
python apikeys.py list

# revogar (desativa; a API passa a recusar essa chave)
python apikeys.py revoke app-react
```

Exemplo de `generate`:

```
Chave 'app-react' criada. GUARDE AGORA — ela nao sera mostrada de novo:

    grk_ZyhrD8xVZSMhF7hx-CdTLQGrRqmC6aeUpju6eVcpS3Q

Registro: /data/apikeys.json
Entregue esta chave ao consumidor (header  X-API-Key: <chave>).
```

Exemplo de `list`:

```
NOME                 PREFIXO        CRIADA EM              STATUS
app-react            grk_ZyhrD8xV   2026-11-18T14:18:08+00:00 ativa
dashboard            grk_H-frUsvE   2026-11-18T14:18:08+00:00 revogada
```

> O `apikeys.py` só usa a biblioteca padrão do Python — roda em qualquer Python 3.8+ sem
> instalar nada.

---

## Onde fica o registro

Um JSON, escolhido nesta ordem:

1. variável `GYMRATS_API_KEYS_FILE`, se definida;
2. ao lado do banco (`GYMRATS_DB`) — **em produção (Docker) é `/data/apikeys.json`**;
3. `./apikeys.json`, ao lado do `apikeys.py` (default local).

O registro **não** é versionado (`apikeys.json` e a pasta `data/` estão no `.gitignore`).

---

## Em produção (Docker)

A API lê `/data/apikeys.json` dentro do container, que é o `./data/apikeys.json` no host
(bind mount). O jeito mais confiável de mexer nas chaves é rodar o CLI **dentro do
container** — assim escreve no caminho certo e com o usuário certo (uid 1000):

```bash
cd ~/gr-api
docker compose exec gr-api python apikeys.py generate app-react
docker compose exec gr-api python apikeys.py list
docker compose exec gr-api python apikeys.py revoke app-react
```

Não precisa reiniciar: a API detecta a mudança no arquivo e aplica na hora.

> O container `gr-api` roda a API; `gr-api-sync` roda o sync. Use o `gr-api` para o CLI
> (ambos enxergam o mesmo `/data`, então tanto faz, mas padronize no `gr-api`).

---

## Fail-closed: revogar a última chave **não** abre a API

Regra de liberação do `require_key`:

- **Nada configurado** (sem `GYMRATS_API_KEY` **e** sem arquivo de registro) → API
  **aberta** (útil só para rodar local sem fricção).
- **Registro existe** (mesmo com zero chaves ativas, ex.: você revogou a última) → a API
  **exige** chave válida e recusa todo mundo (**fail-closed**). Revogar a última chave
  **nunca** torna a API pública por acidente.
- **`GYMRATS_API_KEY` definida** → sempre exige chave válida.

Ou seja: em produção, com o registro criado e/ou a env key definida, a API nunca fica
aberta sozinha.

---

## Só multi-key (sem a chave única)

O `docker-compose.yml` hoje **exige** `GYMRATS_API_KEY` no `.env` (default seguro, compat
com o que já existe). Se quiser proteger a API **só** pelo registro:

1. No `docker-compose.yml`, torne a linha opcional:
   `GYMRATS_API_KEY: ${GYMRATS_API_KEY:-}` (remova o `:?defina…`).
2. Garanta pelo menos uma chave ativa no registro (`apikeys.py generate …`) **antes** de
   subir — senão, como o registro vai existir, a API trava tudo (fail-closed) e ninguém
   acessa.

Para a maioria dos casos, o mais simples é **manter** a `GYMRATS_API_KEY` (uma chave de
emergência/admin) **e** adicionar chaves do registro por consumidor.

---

## Boas práticas

- **Uma chave por consumidor** (`app-react`, `dashboard-casa`, `script-backup`): se uma
  vazar, você revoga só ela.
- **Rotacionar:** gere a nova, atualize o consumidor, **depois** revogue a antiga — sem
  downtime (as duas valem no intervalo).
- **Front-end (SPA):** uma key embutida num bundle React fica visível para quem inspeciona.
  Para uso pessoal num domínio seu com CORS restrito, é aceitável. Para público amplo, use
  um backend-for-frontend ou login de usuário. (Ver nota em [api-reference.md](api-reference.md).)

---

**Próximo:** [rodar local](local.md) ou, para produção, [deploy no servidor](deploy.md).
