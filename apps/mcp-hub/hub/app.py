#!/usr/bin/env python3
# mcp-hub — control-plane tokens + MCP + admin UI + audit
import json
import os
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

import db
import oauth_bridge
import rag_client
import skills as skills_mod
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
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
        "activity_recent pour voir l'historique d'usage partagé."
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
        # admin voit tout ; sinon filtrer sur son token
        tid = None if "admin" in (principal.get("scopes") or []) else principal.get("id")
        out = db.list_activity(limit=limit, token_id=tid)
        _audit(principal, "activity_recent", True, f"limit={limit}", t0)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        _audit(principal, "activity_recent", False, str(e)[:200], t0)
        return json.dumps({"error": str(e)})


class McpAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            auth = request.headers.get("authorization", "")
            principal = db.authenticate(_bearer(auth))
            if not principal:
                return JSONResponse(
                    {"detail": "Token invalide"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": (
                            'Bearer realm="mcp", '
                            f'resource_metadata="{PUBLIC_BASE}/.well-known/oauth-protected-resource"'
                        )
                    },
                )
            token = _principal_var.set(principal)
            try:
                return await call_next(request)
            finally:
                _principal_var.reset(token)
        return await call_next(request)


mcp_asgi = mcp.http_app(path="/")


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


app.mount("/mcp", mcp_asgi)
