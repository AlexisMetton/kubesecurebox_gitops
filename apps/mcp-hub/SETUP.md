# MCP Hub — suite après déploiement

## Claude Desktop (stdio proxy)

`mcp-remote` tente OAuth → échec. Utiliser `hub/stdio_proxy.py` :

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

Prérequis : `python -m pip install mcp requests` + Tailscale + **code hub à jour** (routes `/v1/*`).

## Health

`curl -sk https://mcp.kubesecurebox.com/health`
