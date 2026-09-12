#!/usr/bin/env python3
# mcp-hub — control-plane tokens + serveur MCP (RAG + skills lecture)
import json
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar

import db
import rag_client
import skills as skills_mod
from fastapi import Depends, FastAPI, Header, HTTPException
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

BOOTSTRAP = os.environ.get("MCP_BOOTSTRAP_ADMIN_TOKEN", "")
_principal_var: ContextVar[dict | None] = ContextVar("mcp_principal", default=None)


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


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["rag:read", "skills:read"])


mcp = FastMCP(
    "kubesecurebox-mcp-hub",
    instructions=(
        "Hub MCP personnel KubeSecureBox. "
        "Utilise rag_search pour récupérer des extraits du vault Obsidian/GDrive, "
        "rag_ask pour une réponse déjà synthétisée, "
        "skills_list/skills_get pour les procédures partagées."
    ),
)


def _current_principal() -> dict | None:
    return _principal_var.get()


@mcp.tool()
def rag_ask(
    question: str,
    dossier: str | None = None,
    tag: str | None = None,
    top: int = 8,
) -> str:
    """Pose une question sur les notes personnelles (Obsidian + Google Drive). Réponse synthétisée via le RAG cluster."""
    principal = _current_principal()
    if not principal or (
        "rag:read" not in principal["scopes"] and "admin" not in principal["scopes"]
    ):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        return json.dumps(rag_client.ask(question, dossier, tag, top), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_search(
    question: str,
    dossier: str | None = None,
    tag: str | None = None,
    top: int = 8,
) -> str:
    """Recherche vectorielle seule dans les notes (extraits bruts, sans LLM). À utiliser pour contextualiser une réponse."""
    principal = _current_principal()
    if not principal or (
        "rag:read" not in principal["scopes"] and "admin" not in principal["scopes"]
    ):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        return json.dumps(
            rag_client.search(question, dossier, tag, top), ensure_ascii=False
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_stats() -> str:
    """Statistiques du corpus RAG (documents, chunks, dernière sync)."""
    principal = _current_principal()
    if not principal or (
        "rag:read" not in principal["scopes"] and "admin" not in principal["scopes"]
    ):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        return json.dumps(rag_client.stats(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def rag_dossiers() -> str:
    """Liste les dossiers indexés dans le RAG."""
    principal = _current_principal()
    if not principal or (
        "rag:read" not in principal["scopes"] and "admin" not in principal["scopes"]
    ):
        return json.dumps({"error": "scope rag:read requis"})
    try:
        return json.dumps(rag_client.dossiers(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def skills_list() -> str:
    """Liste les skills disponibles (catalogue partagé)."""
    principal = _current_principal()
    if not principal or (
        "skills:read" not in principal["scopes"] and "admin" not in principal["scopes"]
    ):
        return json.dumps({"error": "scope skills:read requis"})
    return json.dumps(skills_mod.list_skills(), ensure_ascii=False)


@mcp.tool()
def skills_get(name: str) -> str:
    """Charge le contenu complet d'un skill par son nom."""
    principal = _current_principal()
    if not principal or (
        "skills:read" not in principal["scopes"] and "admin" not in principal["scopes"]
    ):
        return json.dumps({"error": "scope skills:read requis"})
    skill = skills_mod.get_skill(name)
    if not skill:
        return json.dumps({"error": f"skill inconnu: {name}"})
    return json.dumps(skill, ensure_ascii=False)


class McpAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            auth = request.headers.get("authorization", "")
            principal = db.authenticate(_bearer(auth))
            if not principal:
                return JSONResponse({"detail": "Token invalide"}, status_code=401)
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


@app.get("/health")
def health():
    return {"status": "ok", "service": "mcp-hub"}


@app.post("/admin/tokens", dependencies=[Depends(require_scope("admin"))])
def admin_create_token(body: TokenCreate):
    allowed = {"admin", "rag:read", "skills:read"}
    scopes = [s for s in body.scopes if s in allowed]
    if not scopes:
        raise HTTPException(status_code=400, detail="Aucun scope valide")
    try:
        meta, raw = db.create_token(body.name, scopes)
    except Exception as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"token": meta, "secret": raw, "note": "Le secret n'est affiché qu'une fois"}


@app.get("/admin/tokens", dependencies=[Depends(require_scope("admin"))])
def admin_list_tokens():
    return {"tokens": db.list_tokens()}


@app.delete("/admin/tokens/{token_id}", dependencies=[Depends(require_scope("admin"))])
def admin_revoke_token(token_id: int):
    if not db.revoke_token(token_id):
        raise HTTPException(status_code=404, detail="Token introuvable ou déjà révoqué")
    return {"revoked": True, "id": token_id}


app.mount("/mcp", mcp_asgi)
