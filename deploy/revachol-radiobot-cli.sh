#!/bin/sh
# /usr/local/bin/revachol-radiobot — ejecuta revachol-radiobot para una cuenta concreta,
# como el usuario de servicio y con la configuración de esa cuenta.
#
#   revachol-radiobot <cuenta> <comando> [opciones]
#   revachol-radiobot discoelysium_es stats
set -eu

BASE=/opt/revachol-radiobot
SVC_USER=revachol-radiobot
BIN="$BASE/venv/bin/revachol-radiobot"

usage() {
  cat <<EOF
Uso: revachol-radiobot <cuenta> <comando> [opciones]

Comandos:
  post [--dry-run]     publica una entrada (lo que hace el timer)
  check [--offline]    comprueba dataset, cookies y sesión de X
  status [HORAS]       resumen: stats + próxima ejecución + resultados de las
                       últimas HORAS (24 por defecto) según el journal
  stats [--json]       progreso, último tuit y autonomía
  preview [-n N]       muestra entradas al azar tal y como se publicarían
  cookies set          guarda auth_token y ct0 (se piden sin eco)
  notify-test          envía un aviso de prueba por Telegram
  import-log FICHERO   marca como publicados los tuits de un log antiguo

Bot configurado:
EOF
  for d in "$BASE"/bots/*/; do
    [ -f "${d}bot.env" ] && echo "  @$(basename "$d")"
  done 2>/dev/null || true
  exit "${1:-0}"
}

[ $# -ge 1 ] || usage 1
case "$1" in -h|--help|help) usage 0 ;; esac

ACCOUNT=${1#@}
shift
[ $# -ge 1 ] || usage 1
DIR="$BASE/bots/$ACCOUNT"

if [ "$(id -u)" -ne 0 ] && [ "$(id -un)" != "$SVC_USER" ]; then
  echo "Ejecuta como root (o como $SVC_USER)." >&2
  exit 1
fi
if [ ! -f "$DIR/bot.env" ]; then
  echo "No existe el bot @$ACCOUNT ($DIR/bot.env)." >&2
  echo "Créalo con: $BASE/app/deploy/install.sh --bot $ACCOUNT" >&2
  exit 1
fi

cd "$DIR"

run() {
  if [ "$(id -u)" -eq 0 ]; then
    runuser -u "$SVC_USER" -- env RADIOBOT_HOME="$DIR" RADIOBOT_ACCOUNT="$ACCOUNT" "$BIN" "$@"
  else
    env RADIOBOT_HOME="$DIR" RADIOBOT_ACCOUNT="$ACCOUNT" "$BIN" "$@"
  fi
}

# El CLI carga los --env-file en orden y el primero gana: bot.env > common.env.
envrun() {
  if [ -f "$BASE/common.env" ]; then
    run --env-file "$DIR/bot.env" --env-file "$BASE/common.env" "$@"
  else
    run --env-file "$DIR/bot.env" "$@"
  fi
}

status() {
  hours=${1:-24}
  case "$hours" in ''|*[!0-9]*) echo "Uso: revachol-radiobot $ACCOUNT status [HORAS]" >&2; exit 1 ;; esac
  unit="revachol-radiobot@$ACCOUNT"
  since="$hours hours ago"

  envrun stats
  echo
  systemctl list-timers "$unit.timer" --no-pager
  echo
  echo "Ejecuciones de las últimas $hours h:"
  journalctl -u "$unit.service" --since "$since" --no-pager -o short \
    | grep -E "Resultado:|ERROR|WARNING|Failed|status=" || echo "  (ninguna)"
  ok=$(journalctl -u "$unit.service" --since "$since" --no-pager -o cat | grep -c "Resultado: OK" || true)
  total=$(journalctl -u "$unit.service" --since "$since" --no-pager -o cat | grep -c "Resultado:" || true)
  echo
  echo "Publicadas con éxito: $ok de $total ejecuciones (lo esperado son ~$((hours * 2 / 3)))"
}

case "$1" in
  status)
    shift
    status "$@"
    ;;
  cookies)
    # Los valores se leen aquí, en la terminal de root, sin eco, y se pasan por
    # stdin (nunca por argumentos, que serían visibles en `ps`).
    if [ "${2:-}" = "set" ]; then
      printf 'Copia los valores desde el navegador (DevTools > Application > Cookies > x.com).\n'
      stty -echo 2>/dev/null || true
      printf 'auth_token: '; read -r auth_token; printf '\n'
      printf 'ct0: '; read -r ct0; printf '\n'
      stty echo 2>/dev/null || true
      printf '%s\n%s\n' "$auth_token" "$ct0" | envrun "$@" --stdin
    else
      envrun "$@"
    fi
    ;;
  *)
    envrun "$@"
    ;;
esac
