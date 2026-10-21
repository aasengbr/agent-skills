#!/usr/bin/env bash
# Instala o AASIS num VPS Ubuntu 24.04. Serve para provedores SEM cloud-init (ex.: Hostinger) e para uso manual.
#
# Como root:   bash post-install.sh
# Sem editar depois:  AASIS_DOMAIN=... ANTHROPIC_API_KEY=... AASIS_HUD_CODE=... bash post-install.sh
#   (valores passados assim ficam no histórico do shell; se preferir, deixe em branco e edite o .env no final)
# Como script de pós-instalação da Hostinger: o conteúdo fica salvo em /post_install no servidor. Não coloque segredos
# no script; deixe em branco e preencha o .env depois, ou use AASIS_SELF_DELETE=1 para ele se apagar no fim.
set -euo pipefail

REPO_URL="${AASIS_REPO_URL:-https://github.com/aasengbr/agent-skills.git}"
BRANCH="${AASIS_BRANCH:-claude/jarvis-iron-man-assistant-ho9xxs}"
APP_DIR="${AASIS_APP_DIR:-/opt/aasis}"
DRY="${AASIS_DRY_RUN:-0}"   # 1 = só mostra o que faria (usado nos testes)

say() { echo "[aasis] $*"; }
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
have() { [ "$DRY" != 1 ] && command -v "$1" >/dev/null 2>&1; }

if [ "$DRY" != 1 ] && [ "$(id -u)" != 0 ]; then
  say "Execute como root (sudo -i)."; exit 1
fi

# 1. Pacotes
run apt-get update -y
run env DEBIAN_FRONTEND=noninteractive apt-get install -y git ufw fail2ban unattended-upgrades ca-certificates
have docker || run env DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
if [ "$DRY" = 1 ] || ! docker compose version >/dev/null 2>&1; then
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
fi
run systemctl enable --now docker fail2ban

# 2. Firewall. Libera a porta do SSH ANTES de ligar, para não trancar você para fora.
SSH_PORTS="$(sshd -T 2>/dev/null | awk '/^port /{print $2}' || true)"
SSH_PORTS="${SSH_PORTS:-22}"
run ufw default deny incoming
run ufw default allow outgoing
for p in $SSH_PORTS; do run ufw allow "${p}/tcp"; done
run ufw allow 80/tcp
run ufw allow 443/tcp
run ufw --force enable

# 3. Código
if [ -d "$APP_DIR/.git" ]; then
  run git -C "$APP_DIR" fetch origin "$BRANCH"
  run git -C "$APP_DIR" checkout "$BRANCH"
  run git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  run git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

# 4. Configuração (.env com permissão 600; nunca sobrescreve um existente; nunca imprime segredos)
VPS_DIR="$APP_DIR/aasis/deploy/vps"
ENV_FILE="$VPS_DIR/.env"
mkdir -p "$VPS_DIR"
if [ ! -f "$ENV_FILE" ]; then
  ( umask 077
    { printf 'AASIS_DOMAIN=%s\n' "${AASIS_DOMAIN:-}"
      printf 'ANTHROPIC_API_KEY=%s\n' "${ANTHROPIC_API_KEY:-}"
      printf 'AASIS_HUD_CODE=%s\n' "${AASIS_HUD_CODE:-}"; } > "$ENV_FILE" )
  chmod 600 "$ENV_FILE"
  say "Criei $ENV_FILE."
else
  say "$ENV_FILE já existe: não vou sobrescrever."
fi

missing=""
for k in AASIS_DOMAIN ANTHROPIC_API_KEY AASIS_HUD_CODE; do
  v="$(grep "^${k}=" "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"   # chave ausente = vazio, sem abortar
  [ -n "$v" ] || missing="$missing $k"
  if [ "$k" = AASIS_HUD_CODE ] && [ -n "$v" ] && [ "${#v}" -lt 20 ]; then missing="$missing AASIS_HUD_CODE(20+caracteres)"; fi
done
if [ -n "$missing" ]; then
  say "Falta preencher em $ENV_FILE:$missing"
  say "Edite (nano $ENV_FILE) e rode:  cd $VPS_DIR && docker compose up -d --build"
  exit 0
fi

# 5. Subir
cd "$VPS_DIR"
run docker compose up -d --build
say "Pronto. Aguarde alguns minutos e abra https://$(grep '^AASIS_DOMAIN=' "$ENV_FILE" | cut -d= -f2-)"
if [ "${AASIS_SELF_DELETE:-0}" = 1 ] && [ "$DRY" != 1 ]; then shred -u "$0" 2>/dev/null || rm -f "$0"; fi
