#!/bin/bash
# discord-notify.sh — Envoie un message sur Discord (webhook)
# Config : /etc/kubesecurebox/discord-webhook.url (une ligne = URL webhook)
# Usage : discord-notify.sh "Titre" "Corps du message"
set -euo pipefail

TITLE="${1:-Notification KubeSecureBox}"
BODY="${2:-(vide)}"
WEBHOOK_FILE="${DISCORD_WEBHOOK_FILE:-/etc/kubesecurebox/discord-webhook.url}"

if [[ ! -f "$WEBHOOK_FILE" ]]; then
  echo "Webhook Discord absent : $WEBHOOK_FILE" >&2
  exit 1
fi

WEBHOOK_URL="$(tr -d '[:space:]' < "$WEBHOOK_FILE")"
if [[ -z "$WEBHOOK_URL" ]]; then
  echo "Webhook Discord vide" >&2
  exit 1
fi

# Limite Discord : 2000 caractères
if ((${#BODY} > 1900)); then
  BODY="${BODY:0:1900}…"
fi

PAYLOAD=$(jq -n \
  --arg title "$TITLE" \
  --arg body "$BODY" \
  '{embeds: [{title: $title, description: $body, color: 5814783}]}')

curl -sfS -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" >/dev/null
