# JARVIS (híbrido)

Assistente pessoal: **voz local** (wake word + Whisper + TTS do Windows) e **cérebro na nuvem** (Claude, com tool-use).

## O que é / Objetivo
Assistente falado que responde, lembra de fatos, lê/grava arquivos num workspace, abre apps permitidos e consulta o estado do PC.

## Quando e como usar
Testar sem voz (qualquer SO):
```
pip install -r requirements.txt
copy .env.example .env      # preencha ANTHROPIC_API_KEY
python -m jarvis.main --text
```
Com voz (Windows, 16 GB RAM, sem GPU: Whisper `small` em CPU/int8):
```
pip install -r requirements-voice.txt
python -m jarvis.main
```
Interface visual (HUD) em qualquer modo: acrescente `--hud` (abre `http://127.0.0.1:8765/`).
```
python -m jarvis.main --text --hud
```
![HUD](docs/hud.png)

O HUD mostra o reator de arco (cor por estado: standby, listening, thinking, speaking), CPU/RAM, log de ferramentas e transcrição. Também aceita comandos digitados e exibe um modal para autorizar ou negar ações sensíveis.

Diga **"Hey Jarvis"**, aguarde "Pois não?" e fale o comando. Diga "encerrar" (modo texto: `sair`) para sair.

## E-mail e Agenda (Google)
Ferramentas: `gmail_search`, `gmail_read`, `gmail_draft` (só rascunho), `calendar_list`, `calendar_create`. Só aparecem para o JARVIS depois da autorização.
1. No [Google Cloud Console](https://console.cloud.google.com/): crie um projeto e ative **Gmail API** e **Google Calendar API**.
2. Tela de consentimento OAuth: tipo *Externo*, modo *Teste*, adicione seu e-mail como usuário de teste.
3. Credenciais → *ID do cliente OAuth* → tipo **App para computador** → baixe o JSON como `jarvis/credentials.json`.
4. `python -m jarvis.google_tools` (abre o navegador; gera `token.json`).
5. Ajuste `google.timezone` no `config.yaml`. Exemplos: "Quais e-mails não lidos tenho?", "Agende dentista amanhã às 14h", "Rascunhe uma resposta ao e-mail X".

`credentials.json` e `token.json` ficam fora do git. O escopo `gmail.compose` do Google também permitiria enviar, mas o código **nunca chama o envio**; só `drafts.create` (coberto por teste).

## Segurança (decisões de projeto)
- E-mails, agenda e páginas web chegam ao modelo dentro de tags `untrusted` e o prompt do sistema os trata como dado, nunca como instrução (defesa contra prompt injection). É mitigação, não garantia: por isso toda escrita (`gmail_draft`, `calendar_create`, `write_file`, `open_app`) exige sua confirmação.
- Arquivos: só dentro de `safety.workspace` (bloqueia `../`).
- Apps: só a allowlist do `config.yaml`; o comando nunca vem do modelo.
- `write_file`, `open_app` pedem confirmação (`safety.confirm_tools`).
- HUD: escuta só em `127.0.0.1`, token aleatório por sessão em todas as chamadas e checagem do cabeçalho `Host` (anti DNS rebinding). Saída do modelo é inserida com `textContent` (sem XSS).
- Chave de API só em `.env` (ignorado pelo git). Texto dos comandos vai à API Anthropic; áudio nunca sai do PC.

## Estado atual (honesto)
| Função | Status |
|---|---|
| Conversa, memória, arquivos, apps, status do PC, web fetch | Implementado e testado (`pytest tests`) |
| Voz (wake word, STT, TTS) | Implementado, **não testado em microfone real** (ambiente de build sem áudio/Windows) |
| E-mail e Agenda | Implementado e testado com serviços simulados (`pytest`). **Não testado contra uma conta Google real** |
| Drive | **Não implementado** |
| Casa inteligente | **Não implementado.** Próximo passo: integrar Home Assistant |
| Visão de tela / controle de mouse | **Não implementado** (maior risco; fazer por último) |
| Interface HUD estilo Iron Man | Implementado e testado (`pytest`, renderização no Chromium sem erros de JS). Não verificado em Windows/Edge |

## Referências de design
[rezaulhreza/jarvis](https://github.com/rezaulhreza/jarvis), [llm-guy/jarvis](https://github.com/llm-guy/jarvis), [openWakeWord + Whisper](https://towardsdatascience.com/build-a-locally-running-voice-assistant-2f2ead904fe9/).
