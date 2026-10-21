# AASIS no notebook (Windows)

Roda direto no seu notebook: painel no navegador e, opcionalmente, voz. **Nunca foi executado num Windows real.** O que
existe de verificação em Windows é o CI do GitHub (veja o fim deste arquivo).

## Para que serve (e para que não serve)
- **Serve para** testar o AASIS de verdade, sem custo de hospedagem, e para usar as ferramentas do PC (abrir apps, ver a tela).
- **Não serve para remédios ou lembretes críticos:** se o notebook dormir, desligar ou perder a internet, **o lembrete não chega**.
  Para isso use o VPS ([`vps.md`](vps.md)).

## Instalar
Precisa de **Python 3.10 ou mais novo** (marque "Add to PATH" no instalador) e do Git.
```
git clone --branch claude/jarvis-iron-man-assistant-ho9xxs https://github.com/aasengbr/agent-skills.git
cd agent-skills\aasis
python -m pip install -r requirements.txt
copy env.example .env
notepad .env
```
No `.env`, coloque a `ANTHROPIC_API_KEY` logo depois do `=`, **sem espaços e sem comentário na mesma linha**.

## Rodar
```
python -m aasis.main --text --hud
```
O painel abre em `http://127.0.0.1:8765/`. Digite no campo ou use os botões. Para **parar**: `Ctrl+C` no terminal.

**Voz** ("Hey Jarvis"; a palavra de ativação é um modelo pronto, não o nome "Aasis"):
```
python -m pip install -r requirements-voice.txt
python -m aasis.main --hud
```
O Windows pode precisar de uma **voz em português** instalada (Configurações > Hora e idioma > Fala). O microfone é pedido na
primeira vez. Não testado: as bibliotecas de voz podem exigir passos extras no Windows.

## Teste com Telegram e remédios (recomendado antes de contratar servidor)
O Telegram só faz conexões de **saída**: não precisa de domínio, IP fixo nem abrir porta no roteador.
1. **Telegram (celular):** @BotFather > `/newbot` > guarde o token. Abra o seu bot e toque em **Iniciar**.
2. **`.env`** (`notepad .env`): `TELEGRAM_BOT_TOKEN=` com o token (sem espaços nem comentário na linha).
3. **chat_id:** `python -m aasis.doctor --find-chat-id` > copie a linha `TELEGRAM_CHAT_ID=...` para o `.env`.
4. **Diagnóstico:** `python -m aasis.doctor --online` > deve chegar uma mensagem de teste no celular e terminar sem `FALHA`.
5. **Rodar:** `python -m aasis.main --text --hud`. O celular recebe "AASIS online". Mantenha o terminal aberto.
6. **Deixar o notebook acordado durante o teste:** Configurações > Sistema > Energia > Suspensão = **Nunca** (na tomada), e
   deixe-o ligado na tomada. Notebook suspenso não avisa remédio.
7. **Teste de aceitação:** itens 1 a 6, 8 e 9 da tabela de [`deploy-checklist.md`](deploy-checklist.md) (use remédio **fictício**).
   Os itens 7 (derrubar o servidor) e 10 (backup) só se aplicam ao VPS.

## Celular acessando o notebook
Use o Tailscale (instalado no notebook e no celular) e siga a "Opção A" do README. Só funciona com o notebook ligado.

## Modelos locais (opcional)
Instale o Ollama, baixe um modelo pequeno e defina `OLLAMA_MODEL` no `.env`. Modelos locais podem ver dados sensíveis, porque
nada sai da máquina. Com 16 GB de RAM e sem GPU **espere lentidão e respostas mais fracas** (não medi). Serve melhor para a
memória ou como reserva do que para a conversa principal.

## O que fica desligado ou difere
- Abrir apps usa a lista do `config.yaml` (`safety.apps`), com comandos do Windows (`start msedge`, `calc`...). Ajuste à vontade.
- A captura de tela exige confirmação a cada uso e envia a imagem à API: pode desligar em `screen.enabled: false`.
- Permissões de arquivo POSIX (backup `600`) não existem no Windows; os arquivos ficam com as permissões padrão do seu usuário.

## Se algo falhar
Copie **só a mensagem de erro** e me traga. Nunca envie chaves, senhas ou o conteúdo do `.env`.

## Verificação automática em Windows
O arquivo `.github/workflows/aasis-tests.yml` roda os testes em Linux (Python 3.10 e 3.12) e em **Windows** a cada mudança em `aasis/`.
Isso cobre a lógica e o servidor, **não** a voz, o microfone, a captura de tela nem a instalação no seu Windows.
