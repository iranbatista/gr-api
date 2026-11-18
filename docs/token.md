# Obter e renovar o token do GymRats

O `sync.py` lê o GymRats autenticado com **o seu token** — um **JWT de vida longa, sem
refresh token**, mandado no header `Authorization` (cru, sem `Bearer`). Ele não pode ser
fabricado: tem que vir de um login de verdade no app. Há duas formas de obtê-lo.

> Por que não dá pra "gerar" esse token: ele é assinado pelo backend do GymRats (Guardian/
> Phoenix). Só o servidor deles consegue emitir um válido, e só emite quando você loga.
> Veja o racional completo em [`REPORT.md`](../REPORT.md) §3.

Onde o token é usado depois de obtido:
- **local:** salvo em `.token.json` (fora do git), lido pelo `sync.py` e pela API.
- **servidor:** na variável `GYMRATS_TOKEN` do `.env` (ver [deploy.md](deploy.md)).

Em qualquer saída, o token aparece **mascarado** (`eyJ…abcd`). Nunca comite `.token*` nem `.env`.

---

## Opção A — e-mail e senha (`login_new.py`) · mais simples

Se a sua conta é de e-mail/senha, esta é a via direta. O script faz
`POST /accounts/sign_in` e salva o JWT; a senha é digitada oculta e **não** é gravada.

```bash
python login_new.py                 # pergunta e-mail e senha → .token-new.json
cp .token-new.json .token.json      # o sync e a API leem de .token.json
```

Saída esperada (mascarada):

```
Token salvo em .token-new.json: eyJhbG…9f2c (len=712)
Formato JWT: sim
```

> Se o login falhar com **HTTP 400/401**, confira e-mail/senha. Contas criadas via
> **Google/Apple ou telefone (SMS)** não têm senha de e-mail — use a Opção B.

---

## Opção B — extrair do app (emulador Android) · funciona para qualquer conta

Reaproveita o JWT que o app **já guardou** depois que você logou nele (inclusive login
social/telefone). É a via 100% read-only recomendada quando não há senha de e-mail.

Pré-requisitos: Android SDK (emulador headless serve) com um AVD, e o app GymRats
instalado e **logado** nesse emulador.

```bash
# 1) confirme o emulador conectado
adb devices

# 2) abra o app e faça login nele (uma vez), depois puxe o DataStore.
#    MSYS_NO_PATHCONV=1 impede o Git Bash de reescrever o caminho /data/...
MSYS_NO_PATHCONV=1 adb -e exec-out run-as com.hasz.gymrats.app \
  cat files/datastore/authentication.preferences_pb > datastore_dump/authentication.preferences_pb

# 3) extraia o JWT do arquivo binário para .token.json (mascarado na saída)
python extract_token.py datastore_dump/authentication.preferences_pb
```

> **Em emulador com root** (Google APIs / não-Play), dá para ler direto o arquivo:
> `MSYS_NO_PATHCONV=1 adb pull /data/data/com.hasz.gymrats.app/files/datastore/authentication.preferences_pb datastore_dump/`.
> O `run-as` acima evita precisar de root e funciona em builds debug/aberto.

O `extract_token.py` procura no arquivo o padrão de um JWT (`eyJ…` com três segmentos) e
pega o maior. Se não achar, confirme que você **está logado** no app.

> **Captura de tráfego (alternativa sem root):** rodar o app atrás de um proxy
> (mitmproxy / HTTP Toolkit) e copiar o header `Authorization`. Pode haver **certificate
> pinning** — não contorne proteções sem necessidade. Ver [`REPORT.md`](../REPORT.md) §3.

---

## Renovação (quando expira → 401 no sync)

O JWT dura semanas/meses. Quando expira, o `sync.py` falha com **401**, loga, e a API
**continua servindo** os últimos dados já sincronizados (nada quebra; só para de atualizar).

Para renovar:

1. Gere um token novo pela Opção A ou B acima (na sua máquina).
2. **Local:** sobrescreva `.token.json` e rode `python sync.py` de novo.
3. **Servidor:** troque no `.env` e recrie o sync (ver [operations.md](operations.md) →
   "Renovar o token"). `docker compose restart` **não** relê o `.env`; `up -d` relê.

> Dá para automatizar a renovação com um emulador no próprio servidor, mas é bem mais
> trabalhoso. Para uso pessoal, renovar manualmente a cada expiração costuma bastar.

---

**Próximo:** [gerar e gerenciar API keys](api-keys.md) — o outro segredo, que **você** cria.
