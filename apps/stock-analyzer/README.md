# Stock Analyzer — déploiement GitOps
# ====================================
# Repo app : https://github.com/AlexisMetton/stock_analyzer (privé)
# Images   : ametton/stock-analyzer-backend, ametton/stock-analyzer-frontend (CI GitHub Actions)
# Accès    : https://stocks.kubesecurebox.com (Tailscale, DNS A → IP Tailscale Pi master)
#            / → frontend Next.js, /api → backend FastAPI, /api/auth → frontend
#
# Claude (hôte pi-master uniquement) :
#   - L'app métier tourne en **pods** (backend, frontend, postgres).
#   - Seul **claude-bridge** (systemd, port 9999) reste sur l'hôte :
#     http://100.66.46.6:9999 — appelé par le backend k8s pour Claude Code.
#   - Ne pas garder ~/stock_analyzer ni /opt/stock-analyzer sur le master (legacy).
