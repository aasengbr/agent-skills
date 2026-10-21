# Telegram e remédios (Fase 2)

> **Estado:** implementado e testado só com simulações (Telegram falso, IA falsa). **Nunca rodou com o Telegram real
> nem com um remédio real.** Antes de depender disso para saúde, faça o teste de aceitação da seção 6.

## 1. O que é e para que serve
- **Canal Telegram:** o AASIS conversa com você pelo Telegram (um bot privado só seu). Serve para **alertas que chegam
  com o app fechado** e para usar o AASIS pelo celular sem abrir o HUD.
- **Agenda de remédios:** nome, dose (como você informou), horários, dias, período e estoque. No horário o bot avisa com
  três botões: **Tomei / Adiar 10 min / Pular**. Sem resposta, reenvia; passadas 3 h marca **dose perdida** e avisa.
- **Monitor externo (heartbeat):** o AASIS "dá sinal de vida" a um serviço externo; se o servidor cair, **o serviço
  avisa você**. Dentro do servidor ninguém consegue avisar que o próprio servidor caiu.

**O AASIS não é dispositivo médico.** Guarda e lembra o que você informou. Não sugere dose, horário, troca nem
combinação de remédios; para dúvida clínica, fale com médico ou farmacêutico.

## 2. Quando usar
- Use o Telegram se o AASIS roda em servidor sempre ligado (VPS) ou no notebook que fica ligado. Sem servidor ligado,
  nenhum aviso sai, com Telegram ou sem.
- Sem Telegram, lembretes e doses só aparecem **com o HUD aberto**: não é confiável para remédio.

## 3. Configuração (uma vez, ~5 min, pelo celular)
1. No Telegram, abra o **@BotFather** > `/newbot` > escolha nome e usuário. Ele devolve o **token**.
2. Abra o seu bot e envie `/start`. Ele responde com o seu **chat_id** (número).
3. No `.env` do AASIS (no VPS: `deploy/vps/.env`):
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```
4. Reinicie o AASIS. Você recebe "AASIS online". Se não receber, confira token e chat_id.
5. (Recomendado) Monitor externo: crie uma verificação no healthchecks.io (plano gratuito), com período de 5 min e
   tolerância de 5 min, ligue o aviso ao seu e-mail/Telegram e coloque a URL no `.env`:
   `HEALTHCHECK_URL=https://hc-ping.com/xxxxxxxx-...` (precisa ser `https`).

Opções em `config.yaml`:
```yaml
telegram:
  enabled: true
  detail: full        # full = nome e dose no aviso | minimal = só "seu remédio" e o horário
meds:
  missed_after_min: 180   # depois disso a dose vira "perdida"
  resend_s: 300           # reenvio enquanto não confirmar
  max_attempts: 6
  low_stock_days: 5       # avisa quando o estoque dura <= 5 dias
heartbeat:
  interval_s: 300
```

## 4. Como usar
- Converse normalmente: "cadastre Losartana 50 mg às 8h e 20h, tenho 30 comprimidos", "tomei o losartana",
  "o que falta tomar hoje?", "atualize o estoque do losartana para 12", "remova a Metformina".
- Cadastrar e remover **pedem confirmação** (botões Sim/Não no próprio Telegram, com o conteúdo exato).
- "Tomei" dá baixa de estoque **uma vez** (tocar duas vezes não duplica). Dose perdida ainda pode ser registrada em até
  12 h.
- Lembretes comuns também chegam no Telegram, com botão **OK**.

## 5. Segurança e privacidade (leia)
- Só o **seu chat privado** é atendido. Outros usuários e grupos são ignorados; o único retorno a um estranho é o id do
  chat dele em resposta a `/start` (no máximo 1 vez por hora).
- Confirmações usam código de uso único; código antigo ou forjado não faz nada.
- O token não aparece em logs, erros nem mensagens (testado). Quem tiver o token controla o bot: guarde só no `.env`.
- **Mensagens de bot no Telegram não têm criptografia ponta a ponta:** o Telegram consegue ver o conteúdo. Se isso
  incomoda para dado de saúde, use `telegram.detail: minimal` (o aviso não traz nome nem dose).
- Dados de remédio são **sensíveis**: só vão a provedores de IA com permissão para esse nível (veja `orchestrator.md`)
  e a auditoria não registra nome nem dose.
- Telegram fora do ar não perde avisos: o item fica pendente e é reenviado no ciclo seguinte (15 s).
- Dose cadastrada depois do horário do dia não gera "dose perdida" falsa; servidor desligado gera registro honesto de
  doses perdidas (janela de ontem e hoje).

## 6. Limites conhecidos e teste de aceitação
- **Depende de servidor ligado e de internet.** O heartbeat avisa quando o AASIS cai; não evita a queda.
- Um bot só pode ser lido por **uma** instância (`getUpdates`); duas instâncias com o mesmo token disputam e geram
  erro 409 (o AASIS espera e tenta de novo).
- Apenas texto e botões. Sem voz, foto ou arquivo pelo Telegram.
- Sem interação medicamentosa, sem cálculo de dose, sem receita: de propósito.
- **Aceitação antes de confiar (faça por 3 dias):** cadastre um remédio fictício com horários próximos; confira
  (a) aviso em até 1 min do horário, (b) reenvio sem resposta, (c) "Tomei" baixa o estoque 1 vez, (d) dose perdida
  avisa após 3 h, (e) derrube o servidor de propósito e confirme que o healthchecks.io avisa você.
