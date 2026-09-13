#!/usr/bin/env python3
# mcp-hub — control-plane tokens + MCP + admin UI + audit
import json
import os
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

import db
import discord_notify
import oauth_bridge
import pentest_runner
import procurement
import rag_client
import skills as skills_mod
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

BOOTSTRAP = os.environ.get("MCP_BOOTSTRAP_ADMIN_TOKEN", "")
PUBLIC_BASE = os.environ.get("MCP_PUBLIC_BASE", "https://mcp.kubesecurebox.com").rstrip(
    "/"
)
_principal_var: ContextVar[dict | None] = ContextVar("mcp_principal", default=None)
CODE_DIR = Path(__file__).resolve().parent


def _bearer(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        return ""
    return authorization[7:].strip()


def require_token(authorization: str = Header(default="")) -> dict:
    principal = db.authenticate(_bearer(authorization))
    if not principal:
        raise HTTPException(status_code=401, detail="Token invalide")
    return principal


def require_scope(scope: str):
    def dep(principal: dict = Depends(require_token)) -> dict:
        if scope not in principal["scopes"] and "admin" not in principal["scopes"]:
            raise HTTPException(status_code=403, detail=f"Scope requis: {scope}")
        return principal

    return dep


def _audit(principal: dict | None, tool: str, ok: bool, summary: str, t0: float) -> None:
    if not principal:
        return
    try:
        db.log_event(
            principal.get("id"),
            principal.get("name") or "",
            tool,
            ok,
            summary,
            int((time.time() - t0) * 1000),
        )
    except Exception:
        pass


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(
        default_factory=lambda: ["rag:read", "skills:read", "activity:read"]
    )


mcp = FastMCP(
    "kubesecurebox-mcp-hub",
    instructions=(
        "Hub MCP personnel KubeSecureBox. "
        "Utilise rag_search pour récupérer des extraits du vault Obsidian/GDrive, "
        "rag_ask pour une réponse déjà synthétisée, "
        "skills_list/skills_get pour les procédures partagées, "
        "activity_recent pour l'historique, "
        "pentest_* / scan_* pour les scans lab autorisés (allowlist), "
        "procurement_* pour les marchés publics FR indexés (BOAMP, faits sourcés)."
    ),
)


def _current_principal() -> dict | None:
    return _principal_var.get()


def _has_scope(principal: dict | None, scope: str) -> bool:
    if not principal:
        return False
    scopes = principal.get("scopes") or []
    return scope in scopes or "admin" in scopes


@mcp.tool()
def rag_ask(
    question: str,
    dossier: str | None = None,
    tag: str | None = None,
    top: int = 8,
) -> str:
    """Pose une question sur les notes personnelles (Obsidian + Google Drive)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "rag:read"):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        out = rag_client.ask(question, dossier, tag, top)
        _audit(principal, "rag_ask", True, question[:200], t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "rag_ask", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_search(
    question: str,
    dossier: str | None = None,
    tag: str | None = None,
    top: int = 8,
) -> str:
    """Recherche vectorielle seule dans les notes (extraits bruts, sans LLM)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "rag:read"):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        out = rag_client.search(question, dossier, tag, top)
        _audit(principal, "rag_search", True, question[:200], t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "rag_search", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_stats() -> str:
    """Statistiques du corpus RAG."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "rag:read"):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        out = rag_client.stats()
        _audit(principal, "rag_stats", True, "stats", t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "rag_stats", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_dossiers() -> str:
    """Liste les dossiers indexés dans le RAG."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "rag:read"):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        out = rag_client.dossiers()
        _audit(principal, "rag_dossiers", True, "dossiers", t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "rag_dossiers", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def skills_list() -> str:
    """Liste les skills disponibles (catalogue partagé)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "skills:read"):
        return json.dumps({"error": "scope skills:read requis"})
    out = skills_mod.list_skills()
    _audit(principal, "skills_list", True, f"{len(out)} skills", t0)
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def skills_get(name: str) -> str:
    """Charge le contenu complet d'un skill par son nom."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "skills:read"):
        return json.dumps({"error": "scope skills:read requis"})
    skill = skills_mod.get_skill(name)
    if not skill:
        _audit(principal, "skills_get", False, f"inconnu:{name}", t0)
        return json.dumps({"error": f"skill inconnu: {name}"})
    _audit(principal, "skills_get", True, name, t0)
    return json.dumps(skill, ensure_ascii=False)


@mcp.tool()
def activity_recent(limit: int = 20) -> str:
    """Historique récent des appels tools MCP (journal partagé)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "activity:read"):
        return json.dumps({"error": "scope activity:read requis"})
    try:
        tid = None if "admin" in (principal.get("scopes") or []) else principal.get("id")
        out = db.list_activity(limit=limit, token_id=tid)
        _audit(principal, "activity_recent", True, f"limit={limit}", t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "activity_recent", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def pentest_tools_list() -> str:
    """Liste les scanners lab disponibles (nmap, nikto, …)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "pentest:lab"):
        return json.dumps({"error": "scope pentest:lab requis"})
    out = pentest_runner.list_tools()
    _audit(principal, "pentest_tools_list", True, f"{len(out)} tools", t0)
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def pentest_allowlist_list() -> str:
    """Liste les domaines/IP/CIDR autorisés pour les scans lab."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "pentest:lab"):
        return json.dumps({"error": "scope pentest:lab requis"})
    out = db.list_allowlist()
    _audit(principal, "pentest_allowlist_list", True, f"{len(out)} entries", t0)
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def scan_start(tool: str, target: str) -> str:
    """Lance un scan lab (Job éphémère + VPN). Cible doit être allowlistée."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "pentest:lab"):
        return json.dumps({"error": "scope pentest:lab requis"})
    try:
        job = pentest_runner.start_scan(
            token_id=principal.get("id") if principal else None,
            token_name=(principal or {}).get("name") or "",
            tool=tool,
            target=target,
        )
        discord_notify.notify_scan(
            token_name=(principal or {}).get("name") or "",
            tool=tool,
            target=target,
            job_id=job.get("id"),
            ok=True,
            detail="scan démarré",
        )
        _audit(principal, "scan_start", True, f"{tool}:{target}", t0)
        return json.dumps(job, ensure_ascii=False)
    except PermissionError as e:
        discord_notify.notify_scan(
            token_name=(principal or {}).get("name") or "",
            tool=tool,
            target=target,
            job_id=None,
            ok=False,
            detail=str(e),
        )
        _audit(principal, "scan_start", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})
    except Exception as e:
        discord_notify.notify_scan(
            token_name=(principal or {}).get("name") or "",
            tool=tool,
            target=target,
            job_id=None,
            ok=False,
            detail=str(e)[:200],
        )
        _audit(principal, "scan_start", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_status(job_id: int) -> str:
    """Statut d'un scan lab (rafraîchit depuis Kubernetes)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "pentest:lab"):
        return json.dumps({"error": "scope pentest:lab requis"})
    try:
        job = pentest_runner.refresh_job(int(job_id))
        if not job:
            return json.dumps({"error": "job inconnu"})
        _audit(principal, "scan_status", True, f"id={job_id}:{job.get('status')}", t0)
        return json.dumps(job, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "scan_status", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_report(job_id: int) -> str:
    """Rapport / logs d'un scan lab."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "pentest:lab"):
        return json.dumps({"error": "scope pentest:lab requis"})
    try:
        job = pentest_runner.refresh_job(int(job_id))
        if not job:
            return json.dumps({"error": "job inconnu"})
        _audit(principal, "scan_report", True, f"id={job_id}", t0)
        return json.dumps(job, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "scan_report", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_cancel(job_id: int) -> str:
    """Annule un scan lab en cours."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "pentest:lab"):
        return json.dumps({"error": "scope pentest:lab requis"})
    try:
        job = pentest_runner.cancel_job(int(job_id))
        if not job:
            return json.dumps({"error": "job inconnu"})
        _audit(principal, "scan_cancel", True, f"id={job_id}", t0)
        return json.dumps(job, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "scan_cancel", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_search(
    dept: str | None = None,
    q: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = 20,
) -> str:
    """Recherche d'avis marchés publics indexés (zone, mots-clés, période)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "public:procurement"):
        return json.dumps({"error": "scope public:procurement requis"})
    try:
        out = procurement.search(
            dept=dept,
            q=q,
            published_from=published_from,
            published_to=published_to,
            limit=limit,
        )
        _audit(principal, "procurement_search", True, (q or dept or "")[:200], t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "procurement_search", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_by_winner(
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = 30,
) -> str:
    """Marchés publics indexés pour un attributaire (SIREN et/ou nom)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "public:procurement"):
        return json.dumps({"error": "scope public:procurement requis"})
    try:
        out = procurement.by_winner(siren=siren, name=name, dept=dept, limit=limit)
        _audit(
            principal,
            "procurement_by_winner",
            True,
            (siren or name or "")[:200],
            t0,
        )
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "procurement_by_winner", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_by_buyer(
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = 30,
) -> str:
    """Marchés publics indexés pour un acheteur (collectivité / SIREN / nom)."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "public:procurement"):
        return json.dumps({"error": "scope public:procurement requis"})
    try:
        out = procurement.by_buyer(siren=siren, name=name, dept=dept, limit=limit)
        _audit(
            principal,
            "procurement_by_buyer",
            True,
            (siren or name or dept or "")[:200],
            t0,
        )
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "procurement_by_buyer", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_get(notice_id: int) -> str:
    """Détail d'un avis indexé (id interne) avec URL source."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "public:procurement"):
        return json.dumps({"error": "scope public:procurement requis"})
    try:
        out = procurement.get_notice(int(notice_id))
        ok = "error" not in out
        _audit(principal, "procurement_get", ok, f"id={notice_id}", t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "procurement_get", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


@mcp.tool()
def procurement_top_winners(
    dept: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = 20,
) -> str:
    """Top attributaires (nombre d'avis / montants) sur une zone et période."""
    principal = _current_principal()
    t0 = time.time()
    if not _has_scope(principal, "public:procurement"):
        return json.dumps({"error": "scope public:procurement requis"})
    try:
        out = procurement.top_winners(
            dept=dept,
            published_from=published_from,
            published_to=published_to,
            limit=limit,
        )
        _audit(principal, "procurement_top_winners", True, (dept or "")[:200], t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "procurement_top_winners", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


class McpAuthMiddleware:
    """ASGI middleware (pas BaseHTTPMiddleware) pour ne pas casser le streaming MCP.

    Réécrit /mcp → /mcp/ avant le Mount Starlette, sinon 307 et Claude droppe Authorization.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path") or ""
            # Évite le 307 Mount(/mcp) → /mcp/ qui fait perdre le Bearer côté Claude.ai
            if path == "/mcp":
                scope = dict(scope)
                scope["path"] = "/mcp/"
                if "raw_path" in scope:
                    qs = scope.get("query_string") or b""
                    scope["raw_path"] = b"/mcp/" + ((b"?" + qs) if qs else b"")
                path = "/mcp/"

            if path.startswith("/mcp"):
                headers = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in scope.get("headers") or []
                }
                principal = db.authenticate(_bearer(headers.get("authorization", "")))
                if not principal:
                    body = b'{"detail":"Token invalide"}'
                    www = (
                        'Bearer realm="mcp", '
                        f'resource_metadata="{PUBLIC_BASE}/.well-known/oauth-protected-resource"'
                    )
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"www-authenticate", www.encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return

                token = _principal_var.set(principal)
                try:
                    await self.app(scope, receive, send)
                finally:
                    _principal_var.reset(token)
                return

        await self.app(scope, receive, send)


def _make_mcp_asgi():
    # stateless_http : plus robuste derrière Cloudflare (pas d'affinité de session)
    try:
        return mcp.http_app(path="/", transport="streamable-http", stateless_http=True)
    except TypeError:
        try:
            return mcp.http_app(path="/", stateless_http=True)
        except TypeError:
            return mcp.http_app(path="/")


mcp_asgi = _make_mcp_asgi()


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    db.init_pool()
    db.ensure_schema()
    db.bootstrap_admin_if_needed(BOOTSTRAP)
    yield
    db.close_pool()


def _build_lifespan():
    try:
        from fastmcp.utilities.lifespan import combine_lifespans

        return combine_lifespans(app_lifespan, mcp_asgi.lifespan)
    except Exception:

        @asynccontextmanager
        async def fallback(app: FastAPI):
            async with app_lifespan(app):
                mcp_life = getattr(mcp_asgi, "lifespan", None)
                if mcp_life is not None:
                    async with mcp_life(app):
                        yield
                else:
                    yield

        return fallback


app = FastAPI(
    title="KubeSecureBox MCP Hub",
    docs_url=None,
    redoc_url=None,
    lifespan=_build_lifespan(),
)
app.add_middleware(McpAuthMiddleware)
app.include_router(oauth_bridge.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "mcp-hub"}


@app.get("/admin")
@app.get("/admin/")
def admin_page():
    path = CODE_DIR / "admin.html"
    if path.is_file():
        return FileResponse(path, media_type="text/html; charset=utf-8")
    return HTMLResponse("<p>admin.html manquant</p>", status_code=404)


@app.post("/admin/tokens")
def admin_create_token(
    body: TokenCreate, principal: dict = Depends(require_scope("admin"))
):
    scopes = [s for s in body.scopes if s in db.ALLOWED_SCOPES]
    if not scopes:
        raise HTTPException(status_code=400, detail="Aucun scope valide")
    t0 = time.time()
    try:
        meta, raw = db.create_token(body.name, scopes)
        _audit(principal, "admin_create_token", True, body.name, t0)
    except Exception as e:
        _audit(principal, "admin_create_token", False, str(e)[:200], t0)
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"token": meta, "secret": raw, "note": "Le secret n'est affiché qu'une fois"}


@app.get("/admin/tokens")
def admin_list_tokens(_principal: dict = Depends(require_scope("admin"))):
    return {"tokens": db.list_tokens()}


@app.delete("/admin/tokens/{token_id}")
def admin_revoke_token(
    token_id: int, principal: dict = Depends(require_scope("admin"))
):
    t0 = time.time()
    if not db.revoke_token(token_id):
        _audit(principal, "admin_revoke_token", False, f"id={token_id}", t0)
        raise HTTPException(status_code=404, detail="Token introuvable ou déjà révoqué")
    _audit(principal, "admin_revoke_token", True, f"id={token_id}", t0)
    return {"revoked": True, "id": token_id}


class TokenScopesUpdate(BaseModel):
    scopes: list[str] = Field(min_length=1)


@app.patch("/admin/tokens/{token_id}")
def admin_update_token_scopes(
    token_id: int,
    body: TokenScopesUpdate,
    principal: dict = Depends(require_scope("admin")),
):
    scopes = [s for s in body.scopes if s in db.ALLOWED_SCOPES]
    if not scopes:
        raise HTTPException(status_code=400, detail="Aucun scope valide")
    t0 = time.time()
    meta = db.update_token_scopes(token_id, scopes)
    if not meta:
        _audit(principal, "admin_update_scopes", False, f"id={token_id}", t0)
        raise HTTPException(status_code=404, detail="Token introuvable ou révoqué")
    _audit(principal, "admin_update_scopes", True, f"id={token_id}:{scopes}", t0)
    return {"token": meta}


class RagQuery(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    dossier: str | None = None
    tag: str | None = None
    top: int = Field(default=8, ge=1, le=30)


@app.post("/v1/rag/ask", dependencies=[Depends(require_scope("rag:read"))])
def v1_rag_ask(body: RagQuery, principal: dict = Depends(require_token)):
    t0 = time.time()
    try:
        out = rag_client.ask(body.question, body.dossier, body.tag, body.top)
        _audit(principal, "rag_ask", True, body.question[:200], t0)
        return out
    except Exception as e:
        _audit(principal, "rag_ask", False, str(e)[:200], t0)
        raise


@app.post("/v1/rag/search", dependencies=[Depends(require_scope("rag:read"))])
def v1_rag_search(body: RagQuery, principal: dict = Depends(require_token)):
    t0 = time.time()
    try:
        out = rag_client.search(body.question, body.dossier, body.tag, body.top)
        _audit(principal, "rag_search", True, body.question[:200], t0)
        return out
    except Exception as e:
        _audit(principal, "rag_search", False, str(e)[:200], t0)
        raise


@app.get("/v1/rag/stats", dependencies=[Depends(require_scope("rag:read"))])
def v1_rag_stats(principal: dict = Depends(require_token)):
    t0 = time.time()
    try:
        out = rag_client.stats()
        _audit(principal, "rag_stats", True, "stats", t0)
        return out
    except Exception as e:
        _audit(principal, "rag_stats", False, str(e)[:200], t0)
        raise


@app.get("/v1/rag/dossiers", dependencies=[Depends(require_scope("rag:read"))])
def v1_rag_dossiers(principal: dict = Depends(require_token)):
    t0 = time.time()
    try:
        out = rag_client.dossiers()
        _audit(principal, "rag_dossiers", True, "dossiers", t0)
        return out
    except Exception as e:
        _audit(principal, "rag_dossiers", False, str(e)[:200], t0)
        raise


@app.get("/v1/skills", dependencies=[Depends(require_scope("skills:read"))])
def v1_skills_list(principal: dict = Depends(require_token)):
    t0 = time.time()
    out = skills_mod.list_skills()
    _audit(principal, "skills_list", True, f"{len(out)} skills", t0)
    return {"skills": out}


@app.get("/v1/skills/{name}", dependencies=[Depends(require_scope("skills:read"))])
def v1_skills_get(name: str, principal: dict = Depends(require_token)):
    t0 = time.time()
    skill = skills_mod.get_skill(name)
    if not skill:
        _audit(principal, "skills_get", False, f"inconnu:{name}", t0)
        raise HTTPException(status_code=404, detail="skill inconnu")
    _audit(principal, "skills_get", True, name, t0)
    return skill


@app.get("/v1/activity", dependencies=[Depends(require_scope("activity:read"))])
def v1_activity(
    limit: int = Query(default=50, ge=1, le=200),
    principal: dict = Depends(require_token),
):
    t0 = time.time()
    tid = None if "admin" in principal["scopes"] else principal["id"]
    out = db.list_activity(limit=limit, token_id=tid)
    _audit(principal, "activity_recent", True, f"limit={limit}", t0)
    return {"events": out}


class AllowlistCreate(BaseModel):
    pattern: str = Field(min_length=1, max_length=253)
    note: str = Field(default="", max_length=300)


class ScanStartBody(BaseModel):
    tool: str = Field(min_length=1, max_length=40)
    target: str = Field(min_length=1, max_length=500)


@app.get("/admin/pentest/allowlist")
def admin_list_allowlist(_principal: dict = Depends(require_scope("admin"))):
    return {"entries": db.list_allowlist()}


@app.post("/admin/pentest/allowlist")
def admin_add_allowlist(
    body: AllowlistCreate, principal: dict = Depends(require_scope("admin"))
):
    t0 = time.time()
    try:
        row = db.add_allowlist(body.pattern, body.note)
        _audit(principal, "admin_allowlist_add", True, body.pattern, t0)
        return {"entry": row}
    except Exception as e:
        _audit(principal, "admin_allowlist_add", False, str(e)[:200], t0)
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.delete("/admin/pentest/allowlist/{entry_id}")
def admin_del_allowlist(
    entry_id: int, principal: dict = Depends(require_scope("admin"))
):
    t0 = time.time()
    if not db.delete_allowlist(entry_id):
        _audit(principal, "admin_allowlist_del", False, f"id={entry_id}", t0)
        raise HTTPException(status_code=404, detail="entrée introuvable")
    _audit(principal, "admin_allowlist_del", True, f"id={entry_id}", t0)
    return {"deleted": True, "id": entry_id}


@app.get("/v1/pentest/tools", dependencies=[Depends(require_scope("pentest:lab"))])
def v1_pentest_tools(principal: dict = Depends(require_token)):
    t0 = time.time()
    out = pentest_runner.list_tools()
    _audit(principal, "pentest_tools_list", True, f"{len(out)} tools", t0)
    return {"tools": out}


@app.get("/v1/pentest/allowlist", dependencies=[Depends(require_scope("pentest:lab"))])
def v1_pentest_allowlist(principal: dict = Depends(require_token)):
    t0 = time.time()
    out = db.list_allowlist()
    _audit(principal, "pentest_allowlist_list", True, f"{len(out)} entries", t0)
    return {"entries": out}


@app.post("/v1/pentest/scans", dependencies=[Depends(require_scope("pentest:lab"))])
def v1_scan_start(body: ScanStartBody, principal: dict = Depends(require_token)):
    t0 = time.time()
    try:
        job = pentest_runner.start_scan(
            token_id=principal["id"],
            token_name=principal["name"],
            tool=body.tool,
            target=body.target,
        )
        discord_notify.notify_scan(
            token_name=principal["name"],
            tool=body.tool,
            target=body.target,
            job_id=job.get("id"),
            ok=True,
        )
        _audit(principal, "scan_start", True, f"{body.tool}:{body.target}", t0)
        return {"job": job}
    except PermissionError as e:
        discord_notify.notify_scan(
            token_name=principal["name"],
            tool=body.tool,
            target=body.target,
            job_id=None,
            ok=False,
            detail=str(e),
        )
        _audit(principal, "scan_start", False, str(e)[:200], t0)
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        discord_notify.notify_scan(
            token_name=principal["name"],
            tool=body.tool,
            target=body.target,
            job_id=None,
            ok=False,
            detail=str(e)[:200],
        )
        _audit(principal, "scan_start", False, str(e)[:200], t0)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/v1/pentest/scans/{job_id}", dependencies=[Depends(require_scope("pentest:lab"))])
def v1_scan_status(job_id: int, principal: dict = Depends(require_token)):
    t0 = time.time()
    job = pentest_runner.refresh_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job inconnu")
    _audit(principal, "scan_status", True, f"id={job_id}", t0)
    return {"job": job}


@app.delete(
    "/v1/pentest/scans/{job_id}", dependencies=[Depends(require_scope("pentest:lab"))]
)
def v1_scan_cancel(job_id: int, principal: dict = Depends(require_token)):
    t0 = time.time()
    job = pentest_runner.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job inconnu")
    _audit(principal, "scan_cancel", True, f"id={job_id}", t0)
    return {"job": job}


@app.get(
    "/v1/procurement/search",
    dependencies=[Depends(require_scope("public:procurement"))],
)
def v1_procurement_search(
    dept: str | None = None,
    q: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    principal: dict = Depends(require_token),
):
    t0 = time.time()
    try:
        out = procurement.search(
            dept=dept,
            q=q,
            published_from=published_from,
            published_to=published_to,
            limit=limit,
        )
        _audit(principal, "procurement_search", True, (q or dept or "")[:200], t0)
        return out
    except Exception as e:
        _audit(principal, "procurement_search", False, str(e)[:200], t0)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get(
    "/v1/procurement/by-winner",
    dependencies=[Depends(require_scope("public:procurement"))],
)
def v1_procurement_by_winner(
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = Query(default=30, ge=1, le=50),
    principal: dict = Depends(require_token),
):
    t0 = time.time()
    try:
        out = procurement.by_winner(siren=siren, name=name, dept=dept, limit=limit)
        if out.get("error"):
            raise HTTPException(status_code=400, detail=out["error"])
        _audit(
            principal,
            "procurement_by_winner",
            True,
            (siren or name or "")[:200],
            t0,
        )
        return out
    except HTTPException:
        raise
    except Exception as e:
        _audit(principal, "procurement_by_winner", False, str(e)[:200], t0)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get(
    "/v1/procurement/by-buyer",
    dependencies=[Depends(require_scope("public:procurement"))],
)
def v1_procurement_by_buyer(
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = Query(default=30, ge=1, le=50),
    principal: dict = Depends(require_token),
):
    t0 = time.time()
    try:
        out = procurement.by_buyer(siren=siren, name=name, dept=dept, limit=limit)
        if out.get("error"):
            raise HTTPException(status_code=400, detail=out["error"])
        _audit(
            principal,
            "procurement_by_buyer",
            True,
            (siren or name or dept or "")[:200],
            t0,
        )
        return out
    except HTTPException:
        raise
    except Exception as e:
        _audit(principal, "procurement_by_buyer", False, str(e)[:200], t0)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get(
    "/v1/procurement/notices/{notice_id}",
    dependencies=[Depends(require_scope("public:procurement"))],
)
def v1_procurement_get(notice_id: int, principal: dict = Depends(require_token)):
    t0 = time.time()
    try:
        out = procurement.get_notice(notice_id)
        if out.get("error"):
            raise HTTPException(status_code=404, detail=out["error"])
        _audit(principal, "procurement_get", True, f"id={notice_id}", t0)
        return out
    except HTTPException:
        raise
    except Exception as e:
        _audit(principal, "procurement_get", False, str(e)[:200], t0)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get(
    "/v1/procurement/top-winners",
    dependencies=[Depends(require_scope("public:procurement"))],
)
def v1_procurement_top_winners(
    dept: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    principal: dict = Depends(require_token),
):
    t0 = time.time()
    try:
        out = procurement.top_winners(
            dept=dept,
            published_from=published_from,
            published_to=published_to,
            limit=limit,
        )
        _audit(principal, "procurement_top_winners", True, (dept or "")[:200], t0)
        return out
    except Exception as e:
        _audit(principal, "procurement_top_winners", False, str(e)[:200], t0)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get(
    "/v1/procurement/stats",
    dependencies=[Depends(require_scope("public:procurement"))],
)
def v1_procurement_stats(principal: dict = Depends(require_token)):
    t0 = time.time()
    try:
        out = procurement.stats()
        _audit(principal, "procurement_stats", True, "stats", t0)
        return out
    except Exception as e:
        _audit(principal, "procurement_stats", False, str(e)[:200], t0)
        raise HTTPException(status_code=500, detail=str(e)) from e


app.mount("/mcp", mcp_asgi)
