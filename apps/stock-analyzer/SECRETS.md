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
# Backend (FMP + auth) :
#   HASH=$(docker compose run --rm backend python scripts/generate_auth_env.py 'TON_MOT_DE_PASSE' | grep AUTH_PASSWORD_HASH | cut -d= -f2)
#   JWT=$(docker compose run --rm backend python scripts/generate_auth_env.py 'x' | grep JWT_SECRET | cut -d= -f2)
#
#   kubectl create secret generic stock-analyzer-secrets \
#     --namespace=stock-analyzer \
#     --from-literal=FMP_API_KEY='ta_cle' \
#     --from-literal=AUTH_EMAIL='alexis.metton@gmail.com' \
#     --from-literal=AUTH_USERNAME='Alexis' \
#     --from-literal=AUTH_PASSWORD_HASH="$HASH" \
#     --from-literal=JWT_SECRET="$JWT" \
#     --dry-run=client -o yaml | kubeseal -o yaml > backend-secret-sealed.yaml
#
# Puis décommenter dans kustomization.yaml et push.
