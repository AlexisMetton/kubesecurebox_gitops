# MCP Hub — admin UI, audit, Claude Desktop

## Admin UI (Tailscale)

https://mcp.kubesecurebox.com/admin

1. Colle un token avec scope **admin** (bootstrap ou token créé pour ça)
2. Gère tokens / vois skills / journal d’activité

## Claude Desktop (stdio proxy)

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

Scopes utiles client : `rag:read`, `skills:read`, `activity:read`.

Prérequis : `python -m pip install --user mcp requests`

## API

| Path | Rôle |
|------|------|
| `GET /health` | health |
| `GET /admin` | UI |
| `POST/GET/DELETE /admin/tokens` | tokens (admin) |
| `/v1/rag/*` | RAG |
| `/v1/skills` | skills |
| `/v1/activity` | journal |

## Skills Git

Ajouter sous `apps/mcp-hub/skills/<nom>/SKILL.md` puis commit + sync Argo.
