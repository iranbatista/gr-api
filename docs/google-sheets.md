# Export para Google Sheets (opcional)

A cada sync, os check-ins do **desafio ativo** podem ser espelhados numa aba
`"<nome do desafio> - Check-ins"` de uma planilha sua, já formatada (cabeçalho, zebra,
filtro, formatos de número, larguras). É **opcional**: sem as variáveis abaixo, o sync
roda igual e isto não faz nada. Uma falha no Sheets **nunca** derruba o sync.

Como o sync roda sozinho no container (sem login interativo), a autenticação é por
**conta de serviço** — a chave não expira como o JWT do GymRats.

## O que vai pra planilha

Uma linha por check-in do desafio ativo, colunas:
`Data · Hora · Membro · Atividade · Duração (min) · Distância · Calorias · Pontos ·
Descrição · Reações · Comentários · ID`.

- **Data/Hora** no fuso de cada check-in (não em UTC).
- **Pontos** são derivados da regra de pontuação do desafio (`score_by`: dias / workouts /
  duração), já que a API não manda pontos por check-in.
- A aba é **reescrita por completo** a cada sync. Virou o mês → desafio novo → aba nova.

## Configurar

1. **Google Cloud** (grátis): em `console.cloud.google.com`, crie um projeto e em
   *APIs & Services → Library* habilite a **Google Sheets API**.
2. *APIs & Services → Credentials → Create credentials → Service account*. Depois de
   criada, abra-a em *Keys → Add key → JSON* e baixe o arquivo.
3. Crie a planilha no Google Sheets e **compartilhe** com o e-mail da service account
   (algo como `nome@projeto.iam.gserviceaccount.com`) como **Editor**.
4. Pegue o **ID da planilha**: no URL `.../spreadsheets/d/`**`ESSE_TRECHO`**`/edit`.
5. No servidor, coloque a chave e configure o `.env`:

   ```bash
   cd ~/gr-api && mkdir -p secrets
   # cole o conteúdo do JSON em secrets/gsa.json (ex.: via nano/scp)
   chmod 600 secrets/gsa.json
   grep -q '^GYMRATS_SHEETS_ID=' .env || echo "GYMRATS_SHEETS_ID=COLE_O_ID_AQUI" >> .env
   grep -q '^GOOGLE_APPLICATION_CREDENTIALS=' .env || echo "GOOGLE_APPLICATION_CREDENTIALS=/secrets/gsa.json" >> .env
   docker compose up -d --build        # rebuild: entra o gspread e o sheets.py
   docker compose logs -f gr-api-sync  # procure "[sheets] aba ... exportados"
   ```

Precisa dos **dois** (`GYMRATS_SHEETS_ID` + `GOOGLE_APPLICATION_CREDENTIALS`) para ligar.

## Segurança

O `secrets/` e `*.gsa.json` estão no `.gitignore` — a chave nunca vai pro repositório. O
volume `./secrets:/secrets:ro` (em `docker-compose.yml`) monta a chave no container
só-leitura. Se não for usar Sheets, pode remover esse volume.

## Local

Mesma ideia: defina `GYMRATS_SHEETS_ID` e `GOOGLE_APPLICATION_CREDENTIALS` apontando para
o JSON na sua máquina, instale o `gspread` (já em `requirements.txt`) e rode `python sync.py`.

## Troubleshooting

- **403 `PERMISSION_DENIED`** ao exportar → a planilha não foi compartilhada com o
  `client_email` da service account como Editor, **ou** a Google Sheets API não está
  habilitada no projeto GCP. O log da API mostra o corpo do erro HTTP.
- **Aba não aparece** → confira se o desafio está **ativo** (entre start e end date); só o
  ativo é exportado.
