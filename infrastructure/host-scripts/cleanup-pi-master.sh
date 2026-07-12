#!/bin/bash
# cleanup-pi-master.sh — Retire OpenCode, Telegram bot et stock-analyzer hôte
# Conserve : Claude Code, claude-bridge, obsidian-sync, qmd, cron scripts
#
# Usage (sur pi-master, user picluster) :
#   bash cleanup-pi-master.sh
#
# Prérequis : source ~/.bashrc (nvm) pour npm uninstall
set -euo pipefail

echo "=== Nettoyage pi-master (hôte) ==="
echo "Conservé : Claude Code, claude-bridge, obsidian-sync, qmd, ~/scripts"

# OpenCode (processus user, pas systemd)
if pgrep -u "$(id -u)" -f "opencode serve" >/dev/null 2>&1; then
  echo "Arrêt opencode serve..."
  pkill -u "$(id -u)" -f "opencode serve" || true
  sleep 2
fi

# npm global
if [[ -f "$HOME/.nvm/nvm.sh" ]]; then
  # shellcheck source=/dev/null
  source "$HOME/.nvm/nvm.sh"
fi

for pkg in opencode-ai opencode-telegram-bridge; do
  if npm list -g "$pkg" >/dev/null 2>&1; then
    echo "npm uninstall -g $pkg"
    npm uninstall -g "$pkg"
  fi
done

# Projets hôte (app métier = pods k8s uniquement)
DIRS=(
  "$HOME/telegram-bot"
  "$HOME/.opencode-telegram-bridge"
  "$HOME/stock_analyzer"
  "/opt/stock-analyzer"
)

for d in "${DIRS[@]}"; do
  if [[ -e "$d" ]]; then
    echo "Suppression $d"
    rm -rf "$d"
  fi
done

# Vérification claude-bridge (conservé)
if systemctl is-active --quiet claude-bridge.service 2>/dev/null; then
  echo "OK : claude-bridge actif"
else
  echo "Note : claude-bridge.service inactif (stock-analyzer k8s en a besoin)"
fi

echo ""
echo "=== Terminé ==="
echo "Vérifier : npm list -g --depth=0"
echo "           systemctl status claude-bridge obsidian-sync"
echo "           ls ~ /opt"
