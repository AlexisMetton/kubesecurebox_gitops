#!/usr/bin/env python3
"""
Proxy MCP stdio pour Claude Desktop / clients locaux.
Appelle l'API REST du mcp-hub (évite mcp-remote + OAuth).

Usage Claude Desktop :
  command: python.exe
  args: [chemin/vers/stdio_proxy.py]
  env: MCP_HUB_URL, MCP_TOKEN
"""
from __future__ import annotations

import json
import os
import sys

import requests

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer  # mcp v1
    except ImportError:
        print(
            "Module mcp manquant. Installe : python -m pip install --user mcp requests",
            file=sys.stderr,
        )
        raise SystemExit(1)

HUB = os.environ.get("MCP_HUB_URL", "https://mcp.kubesecurebox.com").rstrip("/")
TOKEN = os.environ.get("MCP_TOKEN", "").strip()
if not TOKEN:
    print("MCP_TOKEN requis", file=sys.stderr)
    raise SystemExit(1)

TIMEOUT = 120


def _headers() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def _get(path: str) -> str:
    r = requests.get(f"{HUB}{path}", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return json.dumps(r.json(), ensure_ascii=False)


def _post(path: str, body: dict) -> str:
    r = requests.post(
        f"{HUB}{path}", headers=_headers(), json=body, timeout=TIMEOUT
    )
    r.raise_for_status()
    return json.dumps(r.json(), ensure_ascii=False)


mcp = MCPServer(
    "kubesecurebox",
    instructions=(
        "Hub KubeSecureBox. Utilise rag_search pour les notes, "
        "rag_ask pour une réponse synthétisée, skills_* pour les procédures, "
        "pentest_* / scan_* pour les scans lab (allowlist)."
    ),
)


@mcp.tool()
def rag_ask(
    question: str,
    dossier: str | None = None,
    tag: str | None = None,
    top: int = 8,
) -> str:
    """Pose une question sur les notes personnelles (Obsidian + Google Drive)."""
    body: dict = {"question": question, "top": top}
    if dossier:
        body["dossier"] = dossier
    if tag:
        body["tag"] = tag
    try:
        return _post("/v1/rag/ask", body)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_search(
    question: str,
    dossier: str | None = None,
    tag: str | None = None,
    top: int = 8,
) -> str:
    """Recherche vectorielle seule (extraits bruts, sans LLM)."""
    body: dict = {"question": question, "top": top}
    if dossier:
        body["dossier"] = dossier
    if tag:
        body["tag"] = tag
    try:
        return _post("/v1/rag/search", body)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_stats() -> str:
    """Statistiques du corpus RAG."""
    try:
        return _get("/v1/rag/stats")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_dossiers() -> str:
    """Liste les dossiers indexés."""
    try:
        return _get("/v1/rag/dossiers")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def skills_list() -> str:
    """Liste les skills disponibles."""
    try:
        return _get("/v1/skills")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def skills_get(name: str) -> str:
    """Charge un skill par son nom."""
    try:
        return _get(f"/v1/skills/{name}")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def activity_recent(limit: int = 20) -> str:
    """Historique récent des appels tools (journal partagé)."""
    try:
        return _get(f"/v1/activity?limit={int(limit)}")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def pentest_tools_list() -> str:
    """Liste les scanners lab disponibles."""
    try:
        return _get("/v1/pentest/tools")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def pentest_allowlist_list() -> str:
    """Liste les cibles autorisées (allowlist)."""
    try:
        return _get("/v1/pentest/allowlist")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_start(tool: str, target: str) -> str:
    """Lance un scan lab (Job + VPN). Cible allowlistée requise."""
    try:
        return _post("/v1/pentest/scans", {"tool": tool, "target": target})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_status(job_id: int) -> str:
    """Statut d'un scan lab."""
    try:
        return _get(f"/v1/pentest/scans/{int(job_id)}")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_report(job_id: int) -> str:
    """Rapport / logs d'un scan lab."""
    try:
        return _get(f"/v1/pentest/scans/{int(job_id)}")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_cancel(job_id: int) -> str:
    """Annule un scan lab."""
    try:
        r = requests.delete(
            f"{HUB}/v1/pentest/scans/{int(job_id)}",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return json.dumps(r.json(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    # mcp 2.x : run_stdio_async / run ; v1 : run(transport="stdio")
    if hasattr(mcp, "run_stdio_async"):
        import anyio

        anyio.run(mcp.run_stdio_async)
    else:
        mcp.run(transport="stdio")
