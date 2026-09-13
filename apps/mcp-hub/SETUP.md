# MCP Hub — admin UI, audit, Claude Desktop / Claude.ai (iOS)

## Exposition

**Public HTTPS** via Cloudflare Tunnel : `https://mcp.kubesecurebox.com`

Auth applicative : **Bearer token** (scopes). Sans token valide → 403 sur `/mcp` et `/v1/*`.

### Cloudflare Zero Trust (une fois)

1. Zero Trust → Networks → Tunnels → ton tunnel `cloudflared`
2. **Public Hostname** :
   - Subdomain / Domain : `mcp` / `kubesecurebox.com`
   - Service : **HTTP** → même cible que n8n/website (ingress-nginx ClusterIP, ex. `http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:80`)
3. DNS : laisser Cloudflare gérer le CNAME tunnel — **supprimer** tout A record Tailscale sur `mcp.kubesecurebox.com`
4. (Recommandé) **Access** application seulement sur le path `/admin*` (protéger l’UI). **Ne pas** mettre Access sur `/mcp` ni `/v1` : les serveurs Anthropic doivent y accéder avec le Bearer.

### Vérif publique

```bash
curl -sS https://mcp.kubesecurebox.com/health
# {"status":"ok","service":"mcp-hub"}

curl -sS -o /dev/null -w "%{http_code}\n" https://mcp.kubesecurebox.com/mcp
# 403 sans token
```

---

## Claude.ai + iOS (custom connector)

Claude.ai attend souvent un flux **OAuth** (les en-têtes Bearer seuls sont buggés / incomplets).

1. Admin UI → token dédié `claude-ios` (`rag:read`, `skills:read`, `activity:read` ; ajouter `pentest:lab` seulement si besoin lab)
2. claude.ai → Connectors → **Add custom connector**
3. URL : `https://mcp.kubesecurebox.com/mcp`
4. Auth : **Se connecter maintenant** (OAuth)
5. Client OAuth : **S’enregistrer automatiquement** (DCR) — ou identité Claude
6. Transport : HTTP streamable
7. Au Connect : une page Hub demande de **coller le secret du token** → Autoriser
8. Si « autorisé mais erreur de connexion » : déployer le fix `/mcp`→`/mcp/` (sans 307), puis **Se reconnecter**
9. iPhone : même compte → activer le connecteur

Cause connue Claude.ai : un redirect `307 /mcp → /mcp/` fait **perdre** le header Authorization.

Vérifs OAuth :
```bash
curl -sS https://mcp.kubesecurebox.com/.well-known/oauth-protected-resource
curl -sS https://mcp.kubesecurebox.com/.well-known/oauth-authorization-server
```

---

## Admin UI

https://mcp.kubesecurebox.com/admin

1. Colle un token **admin**
2. Gère tokens (créer, scopes, révoquer) / skills / journal / **allowlist pentest**  
   — scopes modifiables sans régénérer le secret (`PATCH`)

---

## Pentest lab (`pentest:lab`)

Scans = Jobs éphémères dans le ns `pentest-lab` (VPN Proton dans le pod). Le hub notifie Discord (webhook optionnel) à chaque `scan_start` / refus.

### Prérequis ops

1. Déployer `apps/pentest-lab` (Argo) + secret `proton-openvpn` (voir `apps/pentest-lab/README.md`)
2. Image `ghcr.io/alexismetton/pentest-runner:latest` (build auto via GitHub Actions sur push `apps/pentest-lab/`)
3. Optionnel : clé `DISCORD_WEBHOOK_URL` dans le secret `mcp-hub-secrets`
4. Admin UI → ajouter domaines/CIDR à l’allowlist
5. Token client avec scope **`pentest:lab`** (pas `admin`)

### Tools MCP / REST

| Tool / path | Rôle |
|-------------|------|
| `pentest_tools_list` / `GET /v1/pentest/tools` | nmap, nikto, wpscan, whatweb, httpx, sslscan |
| `pentest_allowlist_list` / `GET /v1/pentest/allowlist` | lecture allowlist |
| `scan_start` / `POST /v1/pentest/scans` | lance un Job |
| `scan_status` / `scan_report` / `GET …/scans/{id}` | statut + logs |
| `scan_cancel` / `DELETE …/scans/{id}` | annule |
| Admin ` /admin/pentest/allowlist` | CRUD allowlist (admin only) |

Skills Git : `pentest-lab`, `web-recon`, `wordpress-audit`, `port-recon`.

---

## Claude Desktop (stdio proxy, optionnel)

```json
"mcpServers": {
  "kubesecurebox": {
    "command": "C:\\Users\\Alexis\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
    "args": ["C:\\Users\\Alexis\\git\\kubesecurebox_gitops\\apps\\mcp-hub\\hub\\stdio_proxy.py"],
    "env": {
      "MCP_HUB_URL": "https://mcp.kubesecurebox.com",
      "MCP_TOKEN": "<secret_client>"
    }
  }
}
```

Prérequis : `python -m pip install --user mcp requests`

---

## API

| Path | Rôle |
|------|------|
| `GET /health` | health (public) |
| `GET /admin` | UI |
| `POST/GET/DELETE /admin/tokens` | tokens (admin) |
| `PATCH /admin/tokens/{id}` | scopes (secret inchangé) |
| `/mcp` | MCP Streamable HTTP (Bearer) |
| `/v1/rag/*` | RAG |
| `/v1/skills` | skills |
| `/v1/activity` | journal |
| `/v1/pentest/*` | lab scans (`pentest:lab`) |
| `/admin/pentest/allowlist` | allowlist (admin) |

## Skills Git

Ajouter sous `apps/mcp-hub/skills/<nom>/SKILL.md` puis lister la clé dans `kustomization.yaml` (ConfigMap `mcp-skills`) + sync Argo.

## Sécurité (rappel)

- Le hub est **sur Internet** : tokens forts, pas de scope `admin` sur les clients Claude.
- `pentest:lab` uniquement sur un token dédié ; allowlist obligatoire avant tout scan.
- Révoque immédiatement un token fuité (admin UI).
- Access Cloudflare sur `/admin` si tu veux une 2ᵉ barrière pour l’UI.
