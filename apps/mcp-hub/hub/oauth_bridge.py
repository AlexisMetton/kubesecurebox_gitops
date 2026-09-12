# OAuth minimal pour Claude.ai custom connectors (DCR + authorize + token).
# Le access_token émis = un token MCP hub valide (Bearer déjà en base).
from __future__ import annotations

import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import db

PUBLIC_BASE = os.environ.get("MCP_PUBLIC_BASE", "https://mcp.kubesecurebox.com").rstrip(
    "/"
)

# code -> {token, exp, redirect_uri, client_id}
_auth_codes: dict[str, dict] = {}
# client_id -> meta (DCR)
_clients: dict[str, dict] = {}

router = APIRouter(tags=["oauth"])


def _prune() -> None:
    now = time.time()
    dead = [k for k, v in _auth_codes.items() if v["exp"] < now]
    for k in dead:
        _auth_codes.pop(k, None)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/{path:path}")
def oauth_protected_resource(path: str = ""):
    return {
        "resource": f"{PUBLIC_BASE}/mcp",
        "authorization_servers": [PUBLIC_BASE],
        "scopes_supported": sorted(db.ALLOWED_SCOPES),
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def oauth_authorization_server():
    return {
        "issuer": PUBLIC_BASE,
        "authorization_endpoint": f"{PUBLIC_BASE}/oauth/authorize",
        "token_endpoint": f"{PUBLIC_BASE}/oauth/token",
        "registration_endpoint": f"{PUBLIC_BASE}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": sorted(db.ALLOWED_SCOPES),
    }


@router.post("/oauth/register")
async def oauth_register(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    client_id = f"mcp_{secrets.token_urlsafe(16)}"
    _clients[client_id] = {
        "redirect_uris": body.get("redirect_uris") or [],
        "client_name": body.get("client_name") or "claude",
        "created": time.time(),
    }
    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "token_endpoint_auth_method": "none",
            "redirect_uris": _clients[client_id]["redirect_uris"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
    )


@router.get("/oauth/authorize")
def oauth_authorize(
    response_type: str = Query("code"),
    client_id: str = Query(""),
    redirect_uri: str = Query(""),
    state: str = Query(""),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query(""),
    scope: str = Query(""),
):
    if response_type != "code" or not redirect_uri:
        raise HTTPException(status_code=400, detail="paramètres OAuth invalides")
    # Formulaire : coller le token MCP (créé dans /admin)
    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>MCP Hub — Autoriser Claude</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:420px;margin:3rem auto;padding:0 1rem;background:#0f1419;color:#e8eef4}}
input,button{{width:100%;padding:.7rem;margin:.4rem 0;border-radius:8px;border:1px solid #2a3544;background:#1a222c;color:#e8eef4}}
button{{background:#3d9a7a;color:#04140f;font-weight:600;border:none;cursor:pointer}}
p{{color:#8b9aab;font-size:.9rem;line-height:1.4}}
</style></head><body>
<h1>Autoriser Claude</h1>
<p>Colle un token MCP Hub (scopes rag/skills/activity, pas besoin d’admin). Créé dans /admin.</p>
<form method="post" action="/oauth/authorize">
<input type="hidden" name="response_type" value="code"/>
<input type="hidden" name="client_id" value="{client_id}"/>
<input type="hidden" name="redirect_uri" value="{redirect_uri}"/>
<input type="hidden" name="state" value="{state}"/>
<input type="hidden" name="code_challenge" value="{code_challenge}"/>
<input type="hidden" name="code_challenge_method" value="{code_challenge_method}"/>
<input type="hidden" name="scope" value="{scope}"/>
<label>Token MCP</label>
<input name="mcp_token" type="password" required autocomplete="off" placeholder="secret du token"/>
<button type="submit">Autoriser</button>
</form>
</body></html>"""
    return HTMLResponse(html)


@router.post("/oauth/authorize")
def oauth_authorize_submit(
    mcp_token: str = Form(...),
    response_type: str = Form("code"),
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    state: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form(""),
    scope: str = Form(""),
):
    if response_type != "code" or not redirect_uri:
        raise HTTPException(status_code=400, detail="paramètres OAuth invalides")
    principal = db.authenticate(mcp_token.strip())
    if not principal:
        raise HTTPException(status_code=401, detail="Token MCP invalide")
    _prune()
    code = secrets.token_urlsafe(24)
    _auth_codes[code] = {
        "token": mcp_token.strip(),
        "exp": time.time() + 300,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }
    q = {"code": code}
    if state:
        q["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(q)}", status_code=302)


@router.post("/oauth/token")
async def oauth_token(request: Request):
    _prune()
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    grant = data.get("grant_type")
    if grant == "refresh_token":
        # refresh = même access (token MCP longue durée)
        refresh = data.get("refresh_token") or ""
        if not db.authenticate(refresh):
            raise HTTPException(status_code=400, detail="invalid_grant")
        return {
            "access_token": refresh,
            "token_type": "bearer",
            "expires_in": 86400 * 365,
            "refresh_token": refresh,
        }

    if grant != "authorization_code":
        raise HTTPException(status_code=400, detail="unsupported_grant_type")

    code = data.get("code") or ""
    redirect_uri = data.get("redirect_uri") or ""
    entry = _auth_codes.pop(code, None)
    if not entry or entry["exp"] < time.time():
        raise HTTPException(status_code=400, detail="invalid_grant")
    if redirect_uri and entry["redirect_uri"] != redirect_uri:
        raise HTTPException(status_code=400, detail="invalid_grant")

    tok = entry["token"]
    return {
        "access_token": tok,
        "token_type": "bearer",
        "expires_in": 86400 * 365,
        "refresh_token": tok,
        "scope": " ".join(db.ALLOWED_SCOPES),
    }
