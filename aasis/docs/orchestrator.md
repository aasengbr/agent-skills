# Orquestrador de IAs

O AASIS não depende de um só modelo. Cada pedido passa por um orquestrador que escolhe **modelo por tarefa**,
respeita **privacidade**, **troca de provedor se um falhar** e controla **limites e gasto**.

## Como funciona

1. **Tarefa:** `chat` (conversa), `deep` (botão 🧠 Profundo), `memory` (extração de memória, fase 1) e `quick`.
   Cada uma tem uma lista ordenada de modelos em `llm.routes`.
2. **Privacidade:** o pedido recebe um nível (0 normal, 1 privado, 2 sensível, 3 secreto). Cada provedor tem uma
   confiança (`local`, `trusted`, `external`) e um nível máximo (`llm.privacy.max_level`).
   **Padrão: o provedor externo só vê nível 0.** Só dois provedores são "confiáveis" por padrão: a Anthropic
   (`trusted`) e modelos rodando na sua própria máquina (`local`).
3. **Filtros por provedor:** um modelo externo **não recebe** as memórias pessoais nem as ferramentas privadas
   (e-mail, agenda, Drive, casa, tarefas, memória). Só recebe notícias, câmbio, clima, fuso, leitura de páginas.
4. **Falha segura:** erro, limite ou lentidão → tenta o próximo da lista. 3 falhas em 5 min pausam o modelo por
   5 min; chave inválida pausa por 1 h. Limite por minuto (`rpm`) e teto diário de tokens por provedor.
5. **Histórico neutro:** a conversa não está presa ao formato de nenhuma API, então o AASIS pode trocar de
   modelo no meio de uma tarefa com ferramentas.

## Honestidade sobre a privacidade

- A classificação do pedido é por **palavras-chave** e erra nos dois sentidos. Para compensar, o nível do histórico
  conta: depois de um dado privado entrar na conversa, a janela toda fica privada até ele sair do histórico.
- **Provedores externos têm as próprias políticas.** Camadas gratuitas costumam registrar prompts e podem usá-los
  para treino; alguns hospedam fora do Brasil. Confira a política de cada um antes de usar. Só mande para um
  provedor externo aquilo que você aceitaria publicar.
- Você pode afrouxar a regra em `llm.privacy.max_level`, mas **não recomendo** dar nível 1 ou 2 a provedores
  externos. Dados de saúde e finanças não devem sair da Anthropic ou da sua máquina.
- Modelos `local` só funcionam onde o Ollama roda. **Na nuvem (Render) o servidor não alcança o seu PC.**

## O que está verificado

| Item | Situação |
|---|---|
| Anthropic: IDs, preços, cache, esforço, busca na web, recusa | Documentação oficial |
| Moonshot/Kimi: `https://api.moonshot.ai/v1`, `kimi-k3` | **Só fontes de terceiros** |
| Z.ai/GLM: `https://api.z.ai/api/paas/v4`, `glm-5` | **Só terceiros**; suporte a ferramentas **não confirmado** (por isso `tools: false`) |
| NVIDIA: `https://integrate.api.nvidia.com/v1` | Terceiros. Plano gratuito é para **avaliação/protótipo**, cerca de 40 req/min, não produção |
| DeepSeek, OpenRouter, Ollama | **Não consegui consultar nada**; vão pelo padrão OpenAI-compatível que conheço |

**Nenhum provedor externo foi chamado de verdade** (o ambiente de desenvolvimento não os alcança). O código foi
testado com respostas simuladas. Por isso os IDs não ficam fixos no código: confirme o ID real no painel
**Modelos → IDs** (lista o `/models` do provedor, ao vivo) e o teste **testar** mede latência e confirma a chave.

## Configurar (tudo pelo painel de variáveis do servidor, sem terminal)

| Variável | Para quê |
|---|---|
| `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, `ZAI_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY` | Ativa o provedor |
| `NVIDIA_MODEL`, `OPENROUTER_MODEL`, `OLLAMA_MODEL` | ID do modelo (ex. OpenRouter: `vendor/modelo:free`) |

Ordem de preferência por tarefa: edite `llm.routes` em `config.yaml`. Para um provedor novo compatível com OpenAI,
basta uma linha em `providers` e outra em `models`.

## Limites conhecidos

- Sem **streaming** (a resposta aparece pronta).
- O teto de gasto conta **tokens**, não reais; preços de provedores externos não são calculados.
- Modelos externos variam muito em uso de ferramentas; por isso só recebem as públicas.
- A busca na web é da Anthropic: modelos de outros provedores não a têm.
- Sem roteamento automático por dificuldade da pergunta: `deep` é manual (🧠).
