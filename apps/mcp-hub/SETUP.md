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

1. Admin UI → token dédié `claude-ios` (`rag:read`, `skills:read`, `activity:read` ; ajouter `pentest:lab` / `public:procurement` seulement si besoin)
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

1. Déployer `apps/pentest-lab` (Argo) + secret `proton-wireguard` (voir `apps/pentest-lab/README.md`)
2. Image `ghcr.io/alexismetton/pentest-runner:latest` (build auto GitHub Actions sur runner **ARM64** natif)
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

## Marchés publics (`public:procurement`)

Index local BOAMP (API DILA OpenDataSoft) → tables `mcp_procurement_*` (Postgres partagé).

### Prérequis

1. Déployer le hub (ConfigMap code inclut `procurement.py` / `procurement_sync.py`)
2. CronJob `procurement-sync` (2×/jour, MVP **département 58**)
3. Token client avec scope **`public:procurement`** (admin UI)
4. Skill Git : `procurement-fr`

Premier sync manuel (debug) :

```bash
kubectl -n mcp-hub create job --from=cronjob/procurement-sync procurement-sync-manual
kubectl -n mcp-hub logs -f job/procurement-sync-manual
```

### Tools MCP / REST

| Tool / path | Rôle |
|-------------|------|
| `procurement_search` / `GET /v1/procurement/search` | zone, mots-clés, période |
| `procurement_by_winner` / `GET /v1/procurement/by-winner` | attributaire SIREN/nom |
| `procurement_by_buyer` / `GET /v1/procurement/by-buyer` | acheteur |
| `procurement_get` / `GET /v1/procurement/notices/{id}` | détail + `source_url` |
| `procurement_top_winners` / `GET /v1/procurement/top-winners` | top attributaires |
| `GET /v1/procurement/stats` | volume index + état sync |

### Exemples de prompts

- « Top attributaires dans le 58 depuis 2023 »
- « Quels marchés pour Nevers Agglomération ? »
- « Cette entreprise (SIREN …) a gagné quels marchés dans le 58 ? »

### Limites (à rappeler)

- Sous-seuils souvent absents ; pas un détecteur de corruption
- SIREN / montants parfois manquants selon l’avis BOAMP
- MVP geo : sync CronJob filtré `dept=58` (élargir via `--all-france` plus tard)

---

## The European Citizen (`public:tec`)

Proxy HTTP vers l’API publique TEC (pas de re-index Postgres).

### Prérequis

1. Déployer le hub (ConfigMap code inclut `tec_client.py`)
2. Env `TEC_API_URL` / `TEC_SITE_URL` (déjà dans `deployment.yaml`)
3. Token client avec scope **`public:tec`**
4. Skill Git : `european-citizen`

### Tools MCP / REST

| Tool / path | Rôle |
|-------------|------|
| `tec_recent_votes` / `GET /v1/tec/recent-votes` | derniers votes + URLs |
| `tec_vote_detail` / `GET /v1/tec/votes/{adopted_text_id}` | détail + nominatif si dispo |
| `tec_mep` / `GET /v1/tec/meps/{identifier}` | fiche député |
| `tec_leaderboard` / `GET /v1/tec/leaderboard` | palmarès |
| `tec_post_context` / `GET /v1/tec/post-context` | paquet pour brouillons X |

Utiliser **`adopted_text_id`** (pas le `vote_id` de liste) pour détail / URLs site.

### Limites

- Détail nominatif parfois vide (« Non voté » partout) → ne pas inventer
- Pas d’auto-post X : drafts seulement via skill persona

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
| `/v1/procurement/*` | marchés publics BOAMP (`public:procurement`) |
| `/v1/tec/*` | The European Citizen (`public:tec`) |
| `/admin/pentest/allowlist` | allowlist (admin) |

## Skills Git

Ajouter sous `apps/mcp-hub/skills/<nom>/SKILL.md` puis lister la clé dans `kustomization.yaml` (ConfigMap `mcp-skills`) + sync Argo.

## Sécurité (rappel)

- Le hub est **sur Internet** : tokens forts, pas de scope `admin` sur les clients Claude.
- `pentest:lab` uniquement sur un token dédié ; allowlist obligatoire avant tout scan.
- Révoque immédiatement un token fuité (admin UI).
- Access Cloudflare sur `/admin` si tu veux une 2ᵉ barrière pour l’UI.
