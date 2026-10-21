# AASIS em VPS (sempre ligado)

Um VPS é um servidor virtual alugado que fica **ligado 24 h**. É o caminho recomendado para o que depende de o AASIS
estar de pé a qualquer hora (lembretes de remédio, avisos, Telegram). **Nada aqui rodou num VPS real**: veja
"O que não foi verificado" no fim.

## Render (PaaS) ou VPS?

| | Render e similares | VPS |
|---|---|---|
| Esforço | Baixo (só painel web) | Médio (você administra o servidor) |
| Sempre ligado | Só em plano pago (o gratuito "dorme") | Sim |
| Disco persistente | Em geral plano pago | Incluso |
| Segurança do sistema | A plataforma cuida | **Você cuida** (atualizações, SSH, firewall) |
| Custo | Maior para o mesmo uso | Geralmente menor (confira o preço atual do provedor) |
| Bom para | Testar | Uso diário, remédios, avisos |

Mudar de um para o outro é simples: a memória é um arquivo (`aasis.db`) e há backup diário.

## Qual provedor escolher

**Nunca testei nenhum provedor.** As fontes abaixo são comparativos de blogs, **muitos de empresas que vendem VPS e se
colocam em primeiro lugar**; trate como indício e **confira o preço no site do provedor** (promoções anuais sobem na
renovação). Critérios que importam para o AASIS: Ubuntu 24.04; campo **user-data/cloud-init** (para a instalação sem
terminal); **snapshots**; **2 GB de RAM** (a construção da imagem Docker em 1 GB pode faltar memória, não testei);
cobrança por hora ou mensal sem fidelidade.

| Opção | O que as fontes dizem | Observação |
|---|---|---|
| **Vultr** (datacenter em São Paulo) | A partir de cerca de US$ 5 a 6/mês; plano de ~US$ 10 com 2 GB ([fonte](https://deployhandbook.com/best/cheap-vps-hosting)); cobrança por hora | Cobra em dólar (cartão internacional, com IOF). Cloud-init: conheço o suporte, **não verifiquei** |
| **Hetzner** | Melhor custo-benefício nos comparativos (cerca de € 4,5 por 2 vCPU/2 GB) ([fonte](https://dev.to/stargazermedia/what-are-cheapest-cloud-servers-out-there-in-2025-comparison-377a)) | Pelo que conheço **sem datacenter no Brasil** (não confirmado nas fontes): mais latência, irrelevante para um assistente |
| **Hostinger** (veja a seção abaixo) / **Serverspace** (Brasil, em reais) | Entrada a partir de ~R$ 24 a 30/mês ([fonte](https://serverspace.com.br/about/blog/melhores-vps-brasil-2026/), [fonte](https://guiadohost.com/2026/05/12/vps-barato-no-brasil-2026-compare-6-planos-de-entrada-e-descubra-o-melhor-custo-beneficio/)) | Pagamento em reais; **não sei se têm cloud-init**: use a instalação por SSH (opção B) |
| **Oracle Cloud "Always Free"** | A documentação oficial diz que instâncias gratuitas **podem ser recuperadas se ficarem ociosas** (CPU abaixo de 20% por 7 dias) ([doc](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm)) | **Não use para o AASIS**: ele fica ocioso quase sempre, o pior caso para esse risco |
| AWS Lightsail, DigitalOcean | Mais caros pelo mesmo plano, segundo as fontes | Sem vantagem clara aqui |

**Recomendação:** comece por um provedor **cobrado por hora** (ex.: Vultr em São Paulo, plano de 2 GB) e rode uma
semana: se o AASIS e o monitor se comportarem bem, fica; se não, apaga e perde centavos. Troca de provedor é fácil
porque a memória é um arquivo com backup. Se você **só consegue pagar em reais (Pix/boleto)**, use um provedor brasileiro
com instalação por SSH.

## Hostinger: o que verifiquei

**Parecer: serve, com ressalvas.** Só consegui ler resumos de páginas oficiais (o ambiente não abre o site deles), e **nunca
contratei nem testei**.

| Ponto | O que as fontes dizem | Situação |
|---|---|---|
| Datacenter no Brasil | Existe página "VPS in Brazil" ([oficial](https://www.hostinger.com/vps/servers/brazil)) | Confirmado pelo título da página |
| Ubuntu 24.04 e Docker | Tutorial oficial: trocar o SO ("Change OS") por modelo com Docker, ~10 min ([oficial](https://www.hostinger.com/tutorials/how-to-install-docker-on-ubuntu)) | Fonte oficial |
| **cloud-init** | **Não achei suporte.** Eles têm **script de pós-instalação**: shell rodado como root após instalar o SO, salvo em `/post_install` (log em `/post_install.log`, máx. 48 KB) ([oficial](https://www.hostinger.com/support/11080657-using-post-install-scripts-with-hostinger-api/)) | Só vi o caminho **por API**; **não confirmei** se o painel (hPanel) oferece isso. **Meu `cloud-init.yaml` não serve para a Hostinger.** Use `post-install.sh` |
| Backups | Automático **semanal**; diário só em plano pago; 1 snapshot manual por vez; **restaurar apaga tudo que está no servidor e os snapshots** e não dá para cancelar ([oficial](https://www.hostinger.com/support/1583232-how-to-back-up-or-restore-a-vps-server/)) | Fontes divergem na retenção do snapshot (1 ou 20 dias): confira no hPanel |
| Firewall | Nada encontrado | **Não verificado**; o `post-install.sh` configura o `ufw` no próprio servidor |
| Preço (KVM 2: 2 vCPU, 8 GB, 100 GB) | Página oficial em dólar: US$ 8,99/mês, renovação US$ 14,99 por 2 anos. Blogs brasileiros: R$ 34 a 44 no início e **renovação perto de R$ 77,99** ([fonte](https://pt.hostadvice.com/hosting-company/hostinger-reviews/vps-pricing/)) | **Fontes divergem**; contratos longos são cobrados **adiantados** |
| Uptime, suporte, reclamações | Não achei dados | **Não verificado**: veja o Reclame Aqui antes de contratar |

**Plano:** o AASIS é leve. Os planos de entrada (cerca de 4 GB de RAM, segundo as fontes) bastam; não precisa do de 8 GB.

**Antes de pagar, confira na tela de compra:** (1) quanto é cobrado **hoje** e por quanto tempo; (2) o **preço de renovação**;
(3) a **garantia de reembolso** e o prazo; (4) se há **Pix/boleto**. Prefira o prazo mais curto que couber no seu bolso até
confirmar que o AASIS roda bem.

**Instalação na Hostinger (por SSH ou terminal do painel, como root):**
```
git clone --branch claude/jarvis-iron-man-assistant-ho9xxs https://github.com/aasengbr/agent-skills.git /opt/aasis
bash /opt/aasis/aasis/deploy/vps/post-install.sh
nano /opt/aasis/aasis/deploy/vps/.env      # preencha AASIS_DOMAIN, ANTHROPIC_API_KEY, AASIS_HUD_CODE
cd /opt/aasis/aasis/deploy/vps && docker compose up -d --build
```
O script instala o que falta, liga o firewall (libera a porta do SSH **antes** de ligá-lo) e cria o `.env` com permissão 600.
Se ele estiver **sem os três valores**, para e diz o que falta; se estiver completo, já sobe o AASIS.
Como script de pós-instalação da Hostinger, o texto fica salvo no servidor: **deixe os segredos em branco** ou use
`AASIS_SELF_DELETE=1`. Testado em modo simulado (`AASIS_DRY_RUN=1`), **não num servidor real**.

**Cuidado com o backup da Hostinger:** restaurar substitui o servidor inteiro. O backup diário do próprio AASIS (arquivo
`aasis.db`) é mais fácil de restaurar sem perder o resto; mantenha uma cópia fora do servidor.

## O que este pacote faz

- **`deploy/vps/docker-compose.yml`:** sobe o AASIS e o **Caddy** (HTTPS automático com Let's Encrypt). O AASIS fica
  numa rede interna: **só as portas 80 e 443 do Caddy ficam abertas**; a porta do app não é publicada.
- **Só o seu domínio é aceito** (`AASIS_ALLOWED_HOSTS`), o código de acesso tem 20+ caracteres e há bloqueio por IP.
- **`deploy/vps/cloud-init.yaml`:** instala tudo sozinho quando o VPS é criado, **sem terminal**: Docker, firewall
  (`ufw`: só 22, 80, 443), `fail2ban` (bloqueia tentativas de SSH) e atualizações de segurança.
- **Backup diário** do banco em `/data/backups` (14 cópias, permissão 600), feito pelo próprio AASIS.

## Passo a passo

### 1. Criar o VPS
Ubuntu **24.04**, 1 vCPU e 1 a 2 GB de RAM bastam (o AASIS chama IAs pela internet; **um VPS pequeno não roda modelos
locais úteis**). Escolha uma região próxima. **Cadastre uma chave SSH** na criação, em vez de senha.

### 2. Domínio (obrigatório para HTTPS, e o microfone do Chrome exige HTTPS)
- **Domínio próprio:** crie um registro **A** apontando para o IP do VPS.
- **Subdomínio gratuito** (ex.: serviços de DNS dinâmico): aponte para o IP.
- Há serviços que transformam o IP em nome (formato `1-2-3-4.sslip.io`), mas **não testei** o certificado nesse caso.

### 3. Instalar
**Opção A: pelo painel, sem terminal.** Abra `deploy/vps/cloud-init.yaml`, troque os `<PREENCHA>` e cole no campo
"User data" / "cloud-init" ao criar o VPS. **Atenção:** o provedor guarda esse texto, então a chave da API fica
visível no painel dele. Se preferir, deixe as chaves em branco e preencha depois (opção B).
Repositório **privado**? O `git clone` do arquivo precisa de um token na URL, ou clone depois.

**Opção B: por SSH** (no celular serve um app de SSH, ex.: Termius):
```
git clone --branch claude/jarvis-iron-man-assistant-ho9xxs https://github.com/aasengbr/agent-skills.git /opt/aasis
cd /opt/aasis/aasis/deploy/vps
cp env.example .env && nano .env        # preencha AASIS_DOMAIN, ANTHROPIC_API_KEY, AASIS_HUD_CODE
docker compose up -d --build
```
Leva alguns minutos na primeira vez. Depois: `https://SEU-DOMINIO`, digite o código e use o painel **Modelos → testar**.

### 4. Conectar Google, Home Assistant e outros provedores
Mesmas variáveis do README principal, no arquivo `.env`. Depois de editar: `docker compose up -d`.

### 5. Telegram, remédios e monitor externo
Coloque `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` e `HEALTHCHECK_URL` no mesmo `.env` (passo a passo em [`telegram-meds.md`](telegram-meds.md)). É isto que torna o lembrete de remédio confiável: chega com o app fechado, e o monitor externo avisa se o servidor cair.

## Segurança: o que já vem e o que é com você

**Já vem:** firewall fechado (22/80/443), app sem porta pública, HTTPS, bloqueio de SSH por tentativas, atualizações
de segurança automáticas, contêiner sem privilégios extras, só o seu domínio aceito.

**É com você:**
- Usar **chave SSH** e não senha; guardar o `.env` e o `AASIS_HUD_CODE` como senhas.
- Aplicar atualizações do sistema que exigem reinício (`sudo reboot` de vez em quando).
- **Dados sensíveis** (saúde, finanças) ficam em texto no disco do VPS, que o provedor administra. Se isso for
  inaceitável, ative a criptografia de disco do provedor, se houver. O AASIS ainda não criptografa o banco.
- **Quem tiver o código de acesso controla suas ferramentas** (e-mail, Drive, casa).

## Operação

| Tarefa | Comando (em `/opt/aasis/aasis/deploy/vps`) |
|---|---|
| Ver estado | `docker compose ps` |
| Logs | `docker compose logs -f aasis` |
| Atualizar | `git -C /opt/aasis pull && docker compose up -d --build` |
| Reiniciar | `docker compose restart aasis` |

### Backup e restauração
- O AASIS copia o banco todo dia para `/data/backups` (volume Docker). Isso **não protege se o VPS for perdido**:
  ative **snapshots** do provedor e leve cópias para fora. Como o banco tem dados pessoais, **criptografe antes**.
- Copiar para fora: `docker compose cp aasis:/data/backups ./backups`.
- Restaurar (**não testado**): pare o app (`docker compose stop aasis`), copie o arquivo escolhido para `/data/aasis.db`
  dentro do volume com dono `aasis` e suba de novo. Se isso parecer arriscado, faça o primeiro teste de restauração
  **antes** de precisar dele.

### Monitoramento: o ponto que mais importa para remédio
Se o servidor cair, **ninguém avisa que não avisou**. Cadastre um monitor externo gratuito que consulte
`https://SEU-DOMINIO/healthz` a cada poucos minutos e avise você por e-mail. Lembre que `/healthz` só prova que o
processo responde, não que os lembretes estão chegando.

## Limites e honestidade
- Servidor único: se cair, fica fora até você agir (por isso o monitor e os snapshots).
- Você vira o administrador: sem atualizar o sistema, o servidor envelhece.
- O custo de VPS e de cada serviço muda; **confira os preços atuais**, não usei números.
- Sem Ollama/modelos locais em VPS pequeno.

## O que não foi verificado
- **Nenhum deploy em VPS real.** O `docker-compose.yml` foi validado com `docker compose config` (e isso revelou e
  corrigiu um defeito no `env.example`), mas **a imagem não foi construída nem executada** (sem daemon Docker no
  ambiente de desenvolvimento).
- **`Caddyfile` não foi validado** (o Caddy não existe lá); `flush_interval -1` vem do que conheço do Caddy 2.
- `post-install.sh` foi testado só em modo simulado (ordem dos comandos, firewall antes de ligar, `.env` 600, sem vazar
  segredos); **nunca rodou num Ubuntu real**, e `docker-compose-v2` pode já estar instalado no modelo com Docker.
- `cloud-init.yaml` é YAML válido, mas os pacotes `docker.io` e `docker-compose-v2` na Ubuntu 24.04 e a emissão do
  certificado **não foram testados**.
- O Caddy deve sobrescrever o `X-Forwarded-For` de clientes externos (o AASIS usa o último IP dessa lista para o
  bloqueio por IP); confirme isso no seu ambiente.
