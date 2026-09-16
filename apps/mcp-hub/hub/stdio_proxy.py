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
        "pentest_* / scan_* pour les scans lab (allowlist), "
        "procurement_* pour les marchés publics FR (BOAMP indexé)."
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


def _qs(**kwargs) -> str:
    from urllib.parse import urlencode

    pairs = {k: v for k, v in kwargs.items() if v is not None and v != ""}
    return ("?" + urlencode(pairs)) if pairs else ""


@mcp.tool()
def procurement_search(
    dept: str | None = None,
    q: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = 20,
) -> str:
    """Recherche d'avis marchés publics indexés."""
    try:
        return _get(
            "/v1/procurement/search"
            + _qs(
                dept=dept,
                q=q,
                published_from=published_from,
                published_to=published_to,
                limit=limit,
            )
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_by_winner(
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = 30,
) -> str:
    """Marchés pour un attributaire (SIREN / nom)."""
    try:
        return _get(
            "/v1/procurement/by-winner"
            + _qs(siren=siren, name=name, dept=dept, limit=limit)
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_by_buyer(
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = 30,
) -> str:
    """Marchés pour un acheteur (collectivité)."""
    try:
        return _get(
            "/v1/procurement/by-buyer"
            + _qs(siren=siren, name=name, dept=dept, limit=limit)
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_get(notice_id: int) -> str:
    """Détail d'un avis indexé avec URL source."""
    try:
        return _get(f"/v1/procurement/notices/{int(notice_id)}")
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_top_winners(
    dept: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = 20,
) -> str:
    """Top attributaires sur une zone / période."""
    try:
        return _get(
            "/v1/procurement/top-winners"
            + _qs(
                dept=dept,
                published_from=published_from,
                published_to=published_to,
                limit=limit,
            )
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def tec_recent_votes(limit: int = 10) -> str:
    """Derniers votes PE (The European Citizen)."""
    try:
        return _get("/v1/tec/recent-votes" + _qs(limit=limit))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def tec_vote_detail(
    adopted_text_id: int,
    country: str | None = "FRA",
    votes_limit: int = 80,
) -> str:
    """Détail d'un vote (adopted_text_id)."""
    try:
        return _get(
            f"/v1/tec/votes/{int(adopted_text_id)}"
            + _qs(country=country, votes_limit=votes_limit)
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def tec_mep(identifier: str, recent_votes_limit: int = 5) -> str:
    """Fiche eurodéputé TEC."""
    try:
        return _get(
            f"/v1/tec/meps/{identifier}"
            + _qs(recent_votes_limit=recent_votes_limit)
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def tec_leaderboard(country: str = "FRA") -> str:
    """Palmarès TEC (votes / présence)."""
    try:
        return _get("/v1/tec/leaderboard" + _qs(country=country))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def tec_post_context(
    adopted_text_id: int | None = None,
    mode: str = "vote",
    country: str = "FRA",
) -> str:
    """Contexte factuel pour rédiger un post X (skill european-citizen)."""
    try:
        return _get(
            "/v1/tec/post-context"
            + _qs(
                adopted_text_id=adopted_text_id,
                mode=mode,
                country=country,
            )
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    # mcp 2.x : run_stdio_async / run ; v1 : run(transport="stdio")
    if hasattr(mcp, "run_stdio_async"):
        import anyio

        anyio.run(mcp.run_stdio_async)
    else:
        mcp.run(transport="stdio")
