# AASIS: cérebro e memória (estudo e projeto)

Status: **Fases 0 e 1 implementadas** (ver seção 12). Fases 2 a 4 continuam só como projeto. Itens marcados com 🔎
foram verificados em fonte oficial ou no código; os demais são decisões de projeto ou estimativas minhas.

## 1. Princípio

A memória é a característica central. Ela precisa ser **correta** (não inventar nem trocar um dado de saúde),
**disponível** (o dado certo no contexto sem o usuário pedir) e **segura** (saúde e finanças são os dados mais
sensíveis que o sistema terá). Velocidade vem depois de correção.

## 2. Diagnóstico da memória atual 🔎 (verificado no código)

| # | Defeito | Consequência |
|---|---|---|
| 1 | Tabela plana `facts(id, text, created)` | Sem tipo, data de validade, origem, sensibilidade |
| 2 | Só os **30 fatos mais recentes** entram no prompt (`all_for_prompt(limit=30)`) | Com 31+ fatos, uma **alergia** antiga sai do contexto |
| 3 | Busca por `LIKE '%texto%'` | "carro" não encontra "veículo"; acentos e sinônimos falham |
| 4 | Sem substituição de estado | "estacionei no nível 2" e depois "nível 3" coexistem; o modelo adivinha |
| 5 | **Sem ferramenta de esquecer** (`forget` existe, mas não é exposta) | O usuário não consegue apagar nada pelo AASIS |
| 6 | Só grava se o modelo decidir chamar `remember`; sem confirmação nem origem | Sem aprendizado automático; um e-mail malicioso poderia induzir uma gravação |
| 7 | O registro de ações guarda os argumentos (`json.dumps(args)`) | O texto de `remember` (ex.: "tomo Losartana") **vai para a tabela de auditoria** |
| 8 | Lembretes sem recorrência, sem "tomei", entregues só com o app aberto | **Inadequado para remédio** |
| 9 | Sem backup nem criptografia em repouso | Perda do disco = perda de tudo; dados de saúde em claro |

## 3. Referências de projeto

Resumo de sistemas existentes (fontes de blog e comparativos, **não benchmarks confiáveis**: os próprios
comparativos avisam que os números de recall de fornecedores divergem; valide na sua carga):
[Mem0 × Letta × Zep](https://dev.to/plur9/mem0-vs-letta-vs-zep-which-should-you-use-for-agent-memory-1n8m),
[comparativo 2026](https://www.agenticwire.news/article/mem0-zep-letta-agent-memory).

- **Mem0:** extrai fatos da conversa e os guarda num índice vetorial; operações adicionar/atualizar/ignorar.
- **Zep/Graphiti:** cada fato tem **janela de validade** (quando passou a valer e quando foi substituído). É a
  ideia certa para "onde estacionei": o estado novo fecha o anterior sem apagar o histórico.
- **Letta/MemGPT:** núcleo sempre no contexto + memória de arquivo consultada sob demanda.
- **Ferramenta de memória da Anthropic** 🔎 (`memory_20250818`, comandos `view/create/str_replace/insert/delete/rename`
  sobre `/memories`, executada **no cliente**): a documentação diz que a API acrescenta ao prompt a instrução
  "ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE", e lista como responsabilidade do app: validar
  caminhos (path traversal), limitar tamanho, expirar e **filtrar dados sensíveis**
  ([documentação](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)).

**Decisão:** não adotar a ferramenta de memória em arquivos como base. Motivos: (a) obriga uma ida à memória a cada
turno, o que **aumenta a latência**; (b) arquivos de texto livre são ruins para dados críticos e estruturados
(horário de remédio, local do carro, saldo); (c) sensibilidade, validade e auditoria ficam difíceis de impor.
Ela pode entrar depois só para **notas livres**.

## 4. Taxonomia (tipos de memória)

| Tipo | O que guarda | Exemplo | Regra |
|---|---|---|---|
| `profile` (núcleo) | Dados estáveis e críticos | alergias, condições, contatos de emergência, médicos | **Sempre no prompt** (≤ ~800 tokens), revisado com você |
| `fact` | Fatos atemporais | "Maria é minha esposa" | Recuperado por relevância |
| `state` | Estado atual de **um** valor | localização do carro, saldo informado, estoque de remédio | Novo valor **fecha** o anterior (histórico preservado) |
| `event` | Algo que aconteceu, datado | "tomei dipirona às 14h", "consulta em 12/11" | Recuperado por data |
| `schedule` (prospectiva) | O que **precisa acontecer no futuro** | remédio às 8h e 20h, conta que vence dia 10 | Gera avisos com confirmação |
| `preference` | Como você gosta das coisas | "respostas curtas" | Sempre aplicada, baixa sensibilidade |
| `monitor` | Condição vigiada | "avisar se saldo previsto < 0" | Avaliada periodicamente |

Campos comuns: `id, kind, slot, content, data(JSON), sensitivity(normal|sensível|secreto), source(user|inferido|
e-mail|web|ferramenta), importance(1–5), valid_from, valid_to, supersedes_id, status(ativo|pendente|arquivado),
created_at, last_used_at, expires_at`. Busca: **SQLite FTS5** com remoção de acentos (disponível sem dependências),
ordenação por relevância × recência × importância; embeddings locais só numa fase posterior (não enviar dados a
terceiros para indexar).

## 5. Como aprende e grava

1. **Explícito** ("lembre que…"): grava na hora, origem `user`, e confirma em uma frase.
2. **Implícito** (aprender sozinho): depois de responder, **uma chamada barata** (Haiku 5.5) lê **somente as
   mensagens do usuário** e propõe 0–N memórias em JSON. Regras:
   - normal + origem do usuário → grava e avisa discretamente ("Anotei que prefere respostas curtas");
   - **saúde ou finanças → pede confirmação antes** ("Posso anotar que toma Losartana 50 mg às 8h?");
   - **qualquer coisa vinda de e-mail, web, agenda ou ferramenta nunca vira fato**: no máximo fica `pendente`,
     visível, esperando sua aprovação (defesa contra envenenamento da memória por prompt injection);
   - antes de gravar, procura parecidos e decide ADICIONAR, ATUALIZAR, SUBSTITUIR (estados) ou IGNORAR.
3. **Consolidação** (rotina noturna): junta duplicatas, arquiva o que não é usado há tempo (nunca `profile` nem
   `schedule`), e mostra "o que aprendi esta semana" para você aprovar ou corrigir.
4. **Esquecer e exportar:** "esqueça X", "o que você sabe sobre mim?", exportar tudo em JSON; tela **Memória** no HUD.
5. **Bloqueio de segredos:** o AASIS recusa gravar senhas, números de cartão (checagem Luhn), CVV. Use um gerenciador
   de senhas.

## 6. Como lê ("sempre disponível")

A cada mensagem, **antes** de chamar o modelo, o AASIS monta o contexto: núcleo `profile` + estados ativos
(`car_location`, medicações do dia) + top-K memórias relevantes para aquela mensagem + lembretes próximos. Assim
"onde estacionei?" é respondido **sem ferramenta e sem ida extra à API**. A ferramenta `memory_search` fica para
buscas mais profundas. O modelo é instruído: sem memória sobre saúde, **dizer que não sabe**, nunca deduzir.

## 7. Casos de uso do pedido

### 7.1 Remédios (prospectiva, **crítico para segurança**)
- Dados: nome, dose **como o usuário informou**, horários, dias, início/fim, estoque.
- Fluxo: aviso no horário → botões **Tomei / Adiar 10 min / Pular** → grava `event` → se não confirmar, reenvia e
  escala; estoque desce a cada dose e avisa ("restam 3 dias").
- **O AASIS não é dispositivo médico**: não sugere dose, não ajusta horário, não avalia interações; no máximo
  orienta confirmar com médico ou farmacêutico.
- **Confiabilidade:** hoje o aviso só chega com o app aberto. Para remédio isso **não basta**. Exige (a) servidor
  sempre ligado (plano gratuito que "dorme" não serve), (b) canal que alcance o celular com o app fechado:
  **Telegram** (recomendado: confiável, gratuito, bidirecional, também vira cliente do AASIS) ou Web Push, e
  (c) monitoramento externo, porque se o servidor cair **ninguém avisa que não avisou**.

### 7.2 "Onde estacionei meu carro"
Botão 📍 "Estacionei aqui" no HUD (API de geolocalização do navegador, exige HTTPS e permissão) + nota por voz ou
texto ("nível 2, vaga 214"). Vira `state car_location` com histórico. A resposta traz a nota, "há 3 h" e um link de
mapa. **Limite honesto:** em garagem coberta o GPS erra muito; a **nota textual é o dado confiável**.

### 7.3 Saldo negativo
Não há caminho simples e seguro para ler o saldo do banco automaticamente. Opções, da mais segura para a mais arriscada:
- **A. Manual + projeção (recomendada para começar):** você informa o saldo e as contas a pagar (valor e vencimento);
  o AASIS projeta e avisa antes ("em 10/11 o saldo previsto fica negativo em R$ 340"). Sem credenciais, sem integração.
- **B. E-mails do banco (Gmail, só leitura):** detecta avisos de saldo ou limite. Depende de o seu banco enviar esses
  e-mails; e-mail falso (phishing) pode gerar alarme falso, então **só alerta, nunca age**.
- **C. Open Finance via agregador autorizado:** leitura de saldos com seu consentimento. Tem custo, cadastro regulado
  e guarda tokens sensíveis. **Não recomendo agora.** Nunca pedir senha do banco nem "raspar" o app do banco.

## 8. Segurança e privacidade

- **Classes:** `normal`, `sensível` (saúde, finanças) e `secreto` (nunca vai ao modelo).
- **Criptografia de campo** para `sensível` e `secreto` (chave em variável de ambiente, não no disco) e **backup
  cifrado diário** no seu Drive (escopo `drive.file`), para o caso de perda do disco.
- **Auditoria sem conteúdo:** para ferramentas de memória, registrar só tipo, id e status (corrige o defeito 7).
- **Terceiros:** os trechos de memória usados numa resposta **são enviados à API da Anthropic**. Confira a política de
  retenção da sua conta; alguns modelos exigem retenção de 30 dias (Fable 5.1, segundo a documentação do skill).
- **LGPD** 🔎: o art. 4º, I exclui o tratamento por pessoa natural para fins **exclusivamente particulares e não
  econômicos**, e dado de saúde é "sensível" pelo art. 5º, II
  ([texto da lei](https://planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709compilado.htm)). **Para o seu uso
  pessoal, a LGPD em princípio não se aplica. Se o AASIS virar produto para terceiros, dados de saúde passam a
  exigir base legal e cuidados do art. 11.** Esta é uma leitura minha de trechos da lei; confirme com advogado
  antes de comercializar.

## 9. O cérebro: modelos 🔎 (documentação oficial, consultada em 2026-10-21)

| Modelo | ID | Preço (US$/MTok entrada / saída) | Latência relativa | Papel proposto |
|---|---|---|---|---|
| Haiku 5.5 | `claude-haiku-5-5` | a partir de 0,10 / 0,50 | **a mais rápida** | Extração de memória, triagem, classificação, comandos simples |
| Sonnet 5.5 | `claude-sonnet-5-5` | 2 / 10 | rápida | **Conversa e ferramentas (padrão atual)** |
| Opus 5.5 | `claude-opus-5-5` | 4 / 20 | moderada | "Modo profundo": análise financeira, planejamento, viagens complexas |
| Fable 5.1 | `claude-fable-5-1` | 10 / 50 | mais lenta | Só tarefas muito difíceis, sob demanda |

Todos: janela de 1M tokens, saída até 128K, texto e imagem de entrada, texto de saída. **Nenhum modelo faz áudio
nativamente**: voz continua sendo STT/TTS à parte (navegador no celular; Whisper local no PC).

Preço de leitura de cache: 10% do preço de entrada, e **5% no Opus 5.5 e Sonnet 5.5**.

Outras "IAs" do sistema: STT/TTS (voz), visão do próprio Claude (receitas e fotos, fase futura), busca na web
(já implementada) e, opcionalmente, embeddings locais. **Não recomendo outro provedor de LLM**: aumenta custo,
complexidade e a superfície de privacidade sem ganho claro.

### "Em tempo" (resposta rápida): problemas encontrados no código 🔎
1. **Cache nunca acerta:** o `system` começa com `Agora: <hora e minuto>`, que muda a cada minuto. A ordem de
   renderização é tools → system → messages, então tudo depois do carimbo é recalculado. Correção: bloco estável
   com `cache_control` e o conteúdo volátil (hora, memórias recuperadas) depois do ponto de cache. Verificar por
   `usage.cache_read_input_tokens`.
2. **Esforço de raciocínio padrão alto:** o Sonnet 5.5 pensa com esforço `high` por padrão (🔎); para comandos e
   conversa curta, `low` ou `medium` reduz latência e custo. O código não define esforço.
3. **Sem streaming:** a resposta só aparece pronta. Transmitir ao HUD melhora muito a sensação de velocidade.
4. **Sem tratamento de `refusal`:** esses modelos podem recusar com `stop_reason: "refusal"`; hoje o AASIS devolveria
   texto vazio. Tratar com mensagem clara (a documentação também prevê *fallbacks* no servidor).
5. **Roteamento:** comandos simples ("acender luz", "tomei") não precisam do modelo grande.
6. Pré-recuperação de memória evita idas de ferramenta (seção 6).

Ordem de grandeza de custo (**estimativa minha**, 100 interações/dia): conversa com Sonnet 5.5, ~6 mil tokens de
entrada e 400 de saída por turno ≈ US$ 1,6/dia sem cache; extração de memória com Haiku 5.5 ≈ US$ 0,03/dia. Com o
teto atual de 500 mil tokens/dia, ~80 turnos grandes bateriam no limite: **o teto precisará ser revisto**.

## 10. Como saber que funciona (testes)

- Gravar → perguntar depois (recuperação) em conversas simuladas, incluindo >30 fatos com alergia no início.
- Substituição: "nível 2" → "nível 3" → a resposta é "nível 3" e o histórico guarda os dois.
- Nada de e-mail ou web vira fato; sensível exige confirmação; auditoria sem conteúdo.
- Lembrete de remédio entregue até 60 s do horário, medido de ponta a ponta, **no canal real**.
- Métricas em uso: correções do usuário ("não é isso"), aprovações e rejeições de pendentes, latência de recuperação.

## 11. Fases propostas

| Fase | Entrega | Depende de |
|---|---|---|
| **0 – Correções** | Ferramenta de esquecer; auditoria sem conteúdo; núcleo sempre no prompt; tratar `refusal`; cache e esforço corrigidos | nada |
| **1 – Memória nova** | Esquema tipado, FTS5, pré-recuperação, estados (carro), extração com Haiku + confirmação, tela Memória, migração dos fatos atuais, testes | Fase 0 |
| **2 – Remédios** | Agenda de doses, "tomei", estoque, escalonamento, **canal confiável (Telegram)** | servidor sempre ligado (pago) |
| **3 – Finanças** | Saldo manual, contas a pagar, projeção; avisos de e-mail do banco (opcional) | Fase 1 |
| **4 – Robustez** | Consolidação noturna, criptografia de campo, backup cifrado no Drive, embeddings locais | Fase 1 |

**Aviso:** o AASIS ainda nunca rodou fora de testes simulados. Antes de confiar nele com saúde, o deploy real e a
medição de entrega dos avisos são obrigatórios.

## 12. O que foi implementado (Fases 0 e 1) e o que ficou de fora

| Item do projeto | Situação |
|---|---|
| Ferramenta de esquecer (com confirmação que mostra o conteúdo) | Feito: `memory_forget` |
| Auditoria sem conteúdo | Feito: ferramentas privadas registram só campos neutros (ids, datas), nunca texto |
| Perfil sempre no prompt (a alergia não some mais) | Feito: testado com 200 fatos novos |
| Tratar recusa (`refusal`) | Feito (o turno recusado não fica no histórico) |
| Cache do prompt e esforço de raciocínio | Feito: prefixo estável com `cache_control`, hora e memórias depois; esforço por modelo |
| Esquema tipado (perfil, fato, estado, evento, preferência), FTS5 sem acentos, ranking | Feito |
| Estados que substituem o anterior mantendo histórico (carro, etc.) | Feito (`slot`) |
| Pré-recuperação por pedido, filtrada pela confiança do provedor | Feito |
| Aprender sozinho (uma chamada barata por frase pessoal, em segundo plano) | Feito: `Brain.learn` |
| Confirmação para saúde, finanças e perfil | Feito (ferramenta e aprendizado automático: ficam **pendentes** até aprovar) |
| Nada de e-mail, web ou notícia vira fato | Feito: turno "não confiável" grava só como pendente |
| Bloqueio de senhas e cartões (Luhn) | Feito |
| Tela Memória (ver, aprovar, apagar, exportar) e 📍 Estacionei aqui | Feito |
| Migração dos fatos antigos | Feito (uma vez) |

**Ficou de fora** (e continua só no projeto): consolidação noturna, criptografia de campo, backup no Drive, embeddings,
`schedule`/`monitor` (remédios, contas), canal Telegram, streaming.

**Limites conhecidos**
- A busca é por palavra (com prefixo e uma lista pequena de sinônimos). "Onde moro?" **não** acha "Mora na rua X" porque
  "moro" e "mora" não compartilham prefixo. Embeddings resolveriam; não estão feitos.
- A classificação de sensibilidade é por palavras-chave. Passei a tratar verbos de medicação ("tomo") e unidades de
  dose ("50 mg") como saúde, mas um nome de remédio sozinho ainda passa. Por isso o aprendizado automático **nunca**
  usa provedor externo (piso "pessoal"), e a regra de conservadorismo gera falsos positivos ("tomo café").
- Dados sensíveis **não estão criptografados em repouso**: ficam em texto no SQLite do servidor.
- Nada disto rodou em Android real nem contra uma API real: os testes usam provedores simulados.
