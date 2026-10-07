#!/usr/bin/env bash
# Instala o actualiza revachol-radiobot en cualquier Linux con systemd y apt (Debian/Ubuntu).
#
#   git clone https://github.com/enkirro/revachol-radiobot.git /opt/revachol-radiobot/app
#   /opt/revachol-radiobot/app/deploy/install.sh --bot discoelysium_es
#
# Opciones:
#   --bot CUENTA   crea (si no existe) la carpeta del bot para esa arroba
#   --enable       activa también el timer del bot
#
# Es idempotente: volver a ejecutarlo actualiza el código y las unidades sin
# tocar la configuración, las cookies, el dataset ni el estado del bot.
set -euo pipefail

SVC_USER=revachol-radiobot
BASE=/opt/revachol-radiobot
VENV="$BASE/venv"
BOTS_DIR="$BASE/bots"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENABLE=0
BOTS=()

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mAVISO:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bot)    [[ $# -ge 2 ]] || die "--bot necesita el nombre de la cuenta"; BOTS+=("${2#@}"); shift 2 ;;
    --enable) ENABLE=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "Opción desconocida: $1" ;;
  esac
done

for bot in "${BOTS[@]}"; do
  [[ $bot =~ ^[A-Za-z0-9_]{1,15}$ ]] || die "Cuenta no válida: '$bot' (1-15 letras, números o _)"
done
[[ $EUID -eq 0 ]] || die "Ejecuta como root."
command -v apt-get >/dev/null || die "Este instalador es para Debian/Ubuntu."
command -v systemctl >/dev/null || die "Hace falta systemd."

log "Dependencias del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ca-certificates git sqlite3 >/dev/null

log "Usuario de servicio '$SVC_USER'"
if ! id "$SVC_USER" &>/dev/null; then
  # Usuario de sistema: sin contraseña, sin shell y sin home propio.
  useradd --system --user-group --home-dir "$BOTS_DIR" --no-create-home \
          --shell /usr/sbin/nologin --comment "revachol-radiobot service" "$SVC_USER"
fi

log "Estructura en $BASE"
# Código y venv: de root, el servicio solo los lee.
install -d -m 0755 -o root -g root "$BASE"
# Carpeta de bots: el servicio entra, pero cada bot tiene la suya.
install -d -m 0750 -o root -g "$SVC_USER" "$BOTS_DIR"

log "Entorno virtual e instalación"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q --upgrade "$REPO_DIR"
# Si la versión de twifork en PyPI se queda atrás de los cambios de X, se
# instala la última del repositorio del fork.
if ! "$VENV/bin/python" -c "import twikit; twikit.Client" 2>/dev/null; then
  warn "twifork de PyPI no funciona; instalando desde GitHub"
  "$VENV/bin/pip" install -q --upgrade "twifork[impersonate] @ git+https://github.com/PawiX25/twifork.git"
fi
"$VENV/bin/python" -c "import twikit; print('    twifork', twikit.__version__)"

log "Configuración común"
if [[ ! -f "$BASE/common.env" ]]; then
  install -m 0640 -o root -g "$SVC_USER" "$REPO_DIR/deploy/common.env.example" "$BASE/common.env"
fi

for bot in "${BOTS[@]}"; do
  dir="$BOTS_DIR/$bot"
  log "Bot @$bot en $dir"
  install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$dir"
  if [[ ! -f "$dir/bot.env" ]]; then
    install -m 0640 -o root -g "$SVC_USER" "$REPO_DIR/.env.example" "$dir/bot.env"
  fi
  [[ -f "$dir/dataset.json" ]] || warn "Falta $dir/dataset.json"
done

log "Comando 'revachol-radiobot'"
install -m 0755 "$REPO_DIR/deploy/revachol-radiobot-cli.sh" /usr/local/bin/revachol-radiobot

log "Unidades systemd (revachol-radiobot@.service / revachol-radiobot@.timer)"
install -m 0644 "$REPO_DIR/deploy/systemd/revachol-radiobot@.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/deploy/systemd/revachol-radiobot@.timer" /etc/systemd/system/
systemctl daemon-reload

if [[ $ENABLE -eq 1 ]]; then
  for bot in "${BOTS[@]}"; do
    [[ -f "$BOTS_DIR/$bot/cookies.json" ]] || warn "@$bot aún no tiene cookies: el timer fallará hasta que las guardes"
    systemctl enable --now "revachol-radiobot@$bot.timer"
  done
  systemctl list-timers 'revachol-radiobot@*' --no-pager
fi

bot="${BOTS[0]:-<cuenta>}"
cat <<EOF

Instalación completada. Si es la primera vez:

  1. Dataset:   install -o $SVC_USER -g $SVC_USER -m 0640 dataset.json $BOTS_DIR/$bot/dataset.json
  2. Telegram:  nano $BASE/common.env        (configuración del bot: $BOTS_DIR/$bot/bot.env)
  3. Cookies:   revachol-radiobot $bot cookies set
  4. Probar:    revachol-radiobot $bot check && revachol-radiobot $bot post --dry-run
  5. Activar:   systemctl enable --now revachol-radiobot@$bot.timer

Logs:   journalctl -u revachol-radiobot@$bot -f
Estado: revachol-radiobot $bot status
EOF
