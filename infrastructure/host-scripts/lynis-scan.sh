#!/bin/bash
# lynis-scan.sh — Audit Lynis + rapport Discord (dimanche 3h via cron root)
# Installe : sudo cp lynis-scan.sh discord-notify.sh /usr/local/bin/ && sudo chmod +x /usr/local/bin/*.sh
set -euo pipefail

HOST="$(hostname -s)"
LOG_DIR="/var/log/lynis"
REPORT_PREFIX="lynis_report"
NOTIFY="/usr/local/bin/discord-notify.sh"

mkdir -p "$LOG_DIR"

echo "Demarrage du scan Lynis sur ${HOST}..."

# Rapport horodaté (format Lynis natif + notre résumé)
REPORT_FILE="${LOG_DIR}/${REPORT_PREFIX}_$(date +%Y%m%d_%H%M%S).txt"

lynis audit system --quiet --no-colors > "$REPORT_FILE" 2>&1 || true

# Score : Lynis anglais [Hardening index : [72/100]] ou français dans notre echo
SCORE=""
if grep -qE 'Hardening index' "$REPORT_FILE"; then
  SCORE=$(grep -E 'Hardening index' "$REPORT_FILE" | tail -1 | grep -oE '[0-9]+/[0-9]+' | head -1 | cut -d/ -f1)
elif grep -qE 'Score de durcissement' "$REPORT_FILE"; then
  SCORE=$(grep -E 'Score de durcissement' "$REPORT_FILE" | tail -1 | grep -oE '[0-9]+' | head -1)
fi

if [[ -z "$SCORE" ]]; then
  SCORE="?"
fi

echo "Score de durcissement: ${SCORE}/100"
echo "Scan termine. Rapport: ${REPORT_FILE}"

# Suggestions haut niveau (max 5 lignes)
SUGGESTIONS=$(grep -E '^\s\* ' "$REPORT_FILE" 2>/dev/null | head -5 | sed 's/^\s\* /- /' || true)
if [[ -z "$SUGGESTIONS" ]]; then
  SUGGESTIONS="- (voir rapport complet sur le nœud)"
fi

MSG="**Rapport Lynis — ${HOST}**
Score de durcissement : **${SCORE}/100**
Rapport : \`${REPORT_FILE}\`

Top suggestions :
${SUGGESTIONS}"

if [[ -x "$NOTIFY" ]]; then
  "$NOTIFY" "Lynis ${HOST}" "$MSG"
else
  echo "discord-notify.sh absent — message non envoyé" >&2
fi
