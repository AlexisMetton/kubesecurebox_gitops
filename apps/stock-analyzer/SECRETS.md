# Sealed Secrets — à générer AVANT le premier déploiement
# =========================================================
#
# Postgres :
#   kubectl create secret generic postgres-credentials \
#     --namespace=stock-analyzer \
#     --from-literal=postgres-user=stockuser \
#     --from-literal=postgres-password='CHANGEME' \
#     --from-literal=postgres-db=stockdb \
#     --from-literal=database-url='postgresql://stockuser:CHANGEME@postgres.stock-analyzer.svc.cluster.local:5432/stockdb' \
#     --dry-run=client -o yaml | kubeseal -o yaml > postgres-secret-sealed.yaml
#
# Backend (FMP uniquement — auth géré côté Next.js / Tailscale) :
#   kubectl create secret generic stock-analyzer-secrets \
#     --namespace=stock-analyzer \
#     --from-literal=FMP_API_KEY='ta_cle' \
#     --dry-run=client -o yaml | kubeseal -o yaml > backend-secret-sealed.yaml
