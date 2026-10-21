# Roteiro de implantação e aceitação

> **Estado:** nada aqui foi executado num servidor real. O que já foi verificado: o `docker-compose.yml` valida
> (`docker compose config`), o teste ponta a ponta passa contra Telegram e IA **falsos**, e o `doctor` (abaixo)
> reprova configuração errada. **A imagem Docker nunca foi construída** (o ambiente de desenvolvimento não tinha
> o serviço do Docker), então o primeiro `docker compose up --build` é, de fato, o primeiro teste da imagem.

**Objetivo:** colocar o AASIS no ar com lembretes de remédio confiáveis e provar isso antes de depender dele.
**Quando usar:** uma vez, na primeira implantação, e de novo após mudar de servidor.

## Quem faz o quê
Você: itens com 👤 (exigem a sua conta, pagamento ou celular). O diagnóstico (`doctor`) faz o resto da conferência.

## A. Preparação (~30 min, dá para fazer pelo celular)
1. 👤 **VPS** com Ubuntu **24.04**, 1 vCPU e 1 a 2 GB de RAM, e **chave SSH** cadastrada (detalhes e Hostinger em
   [`vps.md`](vps.md)).
2. 👤 **Domínio** com registro **A** apontando para o IP do VPS (necessário para HTTPS).
3. 👤 **Bot do Telegram:** @BotFather > `/newbot` > guarde o **token**. Abra o bot e envie `/start` para ler o **chat_id**.
4. 👤 **Monitor externo:** crie uma verificação em healthchecks.io (período 5 min, tolerância 5 min), ligue o aviso ao
   seu e-mail/Telegram e guarde a URL `https://hc-ping.com/...`.
5. 👤 **Chave da Anthropic** (`ANTHROPIC_API_KEY`) e uma **frase de acesso** de 20+ caracteres (`AASIS_HUD_CODE`).

## B. Instalar (~10 min)
No servidor, como root (opção por SSH de [`vps.md`](vps.md), passo 3B):
```
git clone --branch claude/jarvis-iron-man-assistant-ho9xxs https://github.com/aasengbr/agent-skills.git /opt/aasis
cd /opt/aasis/aasis/deploy/vps
cp env.example .env && chmod 600 .env && nano .env
```
Preencha no `.env`: `AASIS_DOMAIN`, `ANTHROPIC_API_KEY`, `AASIS_HUD_CODE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`HEALTHCHECK_URL` (sem espaços e sem comentário na mesma linha). Depois:
```
docker compose up -d --build
docker compose ps          # aasis deve ficar "healthy" em até ~1 min
```

## C. Diagnóstico (obrigatório antes de confiar)
```
docker compose exec aasis python -m aasis.doctor --serve --online
```
- Termina com **0 falhas**. Avisos precisam ser lidos: cada um diz o que fica de fora.
- Com `--online` ele envia **1 mensagem de teste** ao seu Telegram e **1 ping** ao monitor. Se a mensagem não chegar:
  token errado (falha `401`), ou você não enviou `/start` ao bot (falha `400/403`).
- Ele nunca imprime chaves, token nem a URL do monitor: pode colar a saída aqui sem risco.
- Depois, no HUD (`https://SEU-DOMINIO`): painel **Modelos > testar** (é o único teste real da API de IA).

## D. Teste de aceitação (3 dias, com remédio **fictício**)
Cadastre pelo Telegram: "cadastre Vitamina Teste 1 comprimido às HH:MM e HH:MM, tenho 6 comprimidos" (horários
próximos, escolha 2).

| # | Teste | Passa quando |
|---|---|---|
| 1 | Aviso no horário | A mensagem chega com os botões Tomei / Adiar / Pular em até 1 min do horário |
| 2 | Sem responder | O aviso se repete (a cada 5 min) e depois de 3 h chega "Dose não confirmada" |
| 3 | Tomei | O estoque cai 1; tocar de novo no botão não cai de novo |
| 4 | Adiar | O aviso volta ~10 min depois |
| 5 | Estoque baixo | Chega o aviso "Estoque baixo" (6 un. com 2 doses/dia = 3 dias) |
| 6 | App fechado | Feche o HUD e o Telegram em segundo plano: o aviso ainda toca no celular |
| 7 | Servidor cai | `docker compose stop aasis`: em até ~10 min o healthchecks.io avisa você. Suba de novo com `docker compose start aasis` |
| 8 | Reinício | `docker compose restart aasis`: nenhum comando antigo é repetido; o remédio continua cadastrado |
| 9 | Estranho | Peça a outra pessoa para escrever ao bot: ela não recebe nada além do próprio chat_id em `/start` |
| 10 | Backup | `docker compose exec aasis ls /data/backups` mostra um arquivo do dia |

Só depois de **todos** passarem por 3 dias cadastre remédio real. Registre qualquer atraso: o objetivo do projeto é
aviso em até 60 s do horário, medido ponta a ponta.

## E. Se algo falhar
- Rode o diagnóstico de novo e leia a linha com `FALHA`.
- Logs: `docker compose logs --tail 100 aasis` (o AASIS não registra token nem conteúdo de saúde). Cole aqui a saída do
  `doctor` e as linhas do log; eu corrijo e você atualiza com `git pull && docker compose up -d --build`.
- Desfazer: `docker compose down` (os dados ficam no volume `aasis-data`; `down -v` apaga tudo, não use sem querer).

## O que continua não verificado
Imagem Docker, certificado HTTPS (Caddy) no seu domínio, API real da Anthropic, Telegram real, healthchecks.io real,
IDs de modelos de terceiros e o comportamento no celular. Este roteiro existe para medir isso.
