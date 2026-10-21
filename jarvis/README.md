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

### Drive
`drive_search`, `drive_read` (Docs, Planilhas e Apresentações exportados como texto; arquivos de texto) e `drive_create_doc` (cria um Google Doc novo). Escrita só em arquivos criados pelo JARVIS (escopo `drive.file`). **Quem já autorizou o Google antes precisa rodar `python -m jarvis.google_tools` de novo** (novos escopos; o código detecta e reautoriza).

## Visão de tela
`screen_look`: captura o monitor principal, reduz para `screen.max_side` e envia ao modelo. Exemplos: "O que há na minha tela?", "Explique este erro". **Somente visão: não existe controle de mouse ou teclado.** A imagem sai do PC para a API, por isso pede confirmação a cada uso e pode ser desligada (`screen.enabled: false`). Texto dentro da imagem é tratado como dado não confiável. Requer `mss` e `pillow` (já no `requirements.txt`).

## Casa inteligente (Home Assistant)
Ferramentas: `ha_list`, `ha_state`, `ha_call`. Aparecem só com `HA_TOKEN` no `.env` e `home_assistant.url` no `config.yaml`.
1. No Home Assistant: Perfil → Segurança → **Token de acesso de longa duração**; cole em `HA_TOKEN`.
2. Ajuste `url` (padrão `http://homeassistant.local:8123`).
3. Exemplos: "Apague a luz da sala", "Coloque o ar em 22 graus", "O que está ligado em casa?"

Faixas de risco (`config.yaml`): `auto_domains` (luz, interruptor, ventilador, mídia, cena) executam direto; `confirm_domains` (clima, persiana, aspirador) pedem confirmação; **qualquer outro domínio (fechaduras, alarme, scripts, automações, câmeras) é bloqueado e nem aparece para o modelo.** Em `ha_call` o `entity_id` precisa pertencer ao domínio informado, o serviço é validado por regex, e `entity_id`/`area_id`/`device_id` dentro de `data` são descartados.

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
| Drive | Implementado e testado com serviços simulados. **Não testado contra conta real** |
| Visão de tela | Implementada e testada com captura simulada (confirmação, redução, aviso de dado não confiável). **Captura real (`mss`) não testada** em Windows |
| Casa inteligente | Implementada e testada com Home Assistant simulado (faixas de risco, bloqueios, confirmação). **Não testada contra um Home Assistant real** |
| Controle de mouse/teclado | **Não implementado** (decisão deliberada: maior risco) |
| Interface HUD estilo Iron Man | Implementado e testado (`pytest`, renderização no Chromium sem erros de JS). Não verificado em Windows/Edge |

## Referências de design
[rezaulhreza/jarvis](https://github.com/rezaulhreza/jarvis), [llm-guy/jarvis](https://github.com/llm-guy/jarvis), [openWakeWord + Whisper](https://towardsdatascience.com/build-a-locally-running-voice-assistant-2f2ead904fe9/).
