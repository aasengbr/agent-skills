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
