#!/usr/bin/env python3
# API RAG personnel — Obsidian / pgvector / DeepSeek

import json
import os
import time

import psycopg2
import psycopg2.pool
import requests
from fastapi import Depends, FastAPI, HTTPException, Header, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from urllib.parse import quote

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
RAG_API_TOKEN = os.environ["RAG_API_TOKEN"]
OBSIDIAN_VAULT_NAME = os.environ.get("OBSIDIAN_VAULT_NAME", "Workspace")

PG_DSN = (
    f"host={os.environ.get('PG_HOST', 'postgres')} "
    f"port={os.environ.get('PG_PORT', '5432')} "
    f"dbname={os.environ.get('PG_DB', 'rag')} "
    f"user={os.environ.get('PG_USER', 'rag_user')} "
    f"password={os.environ['PG_PASSWORD']}"
)

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
MOC_SEARCH_PENALTY = float(os.environ.get("MOC_SEARCH_PENALTY", "0.15"))

app = FastAPI(title="RAG personnel KubeSecureBox", docs_url=None, redoc_url=None)
pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=8, dsn=PG_DSN)


def verifier_token(authorization: str = Header(default="")):
    if authorization != f"Bearer {RAG_API_TOKEN}":
        raise HTTPException(status_code=401, detail="Token invalide")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    dossier: str | None = Field(default=None, max_length=200)
    tag: str | None = Field(default=None, max_length=100)
    top: int = Field(default=8, ge=1, le=30)


def embed_question(question: str) -> list[float]:
    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"model": EMBEDDING_MODEL, "input": [question]},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def rechercher(
    vecteur: list[float],
    dossier: str | None,
    tag: str | None,
    top: int,
) -> list[dict]:
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        filtres = []
        filtres_params: list = []
        vecteur_json = json.dumps(vecteur)
        if dossier:
            filtres.append("c.dossier = %s")
            filtres_params.append(dossier)
        if tag:
            filtres.append("c.tags @> %s::jsonb")
            filtres_params.append(json.dumps([tag]))
        where = ("WHERE " + " AND ".join(filtres)) if filtres else ""
        penalite = 0.0 if tag == "moc" else MOC_SEARCH_PENALTY
        cur.execute(
            f"""SELECT c.contenu, d.nom, d.chemin_vault, d.dossier,
                       d.date_document,
                       (c.embedding <=> %s::vector) AS distance
                FROM chunks c JOIN documents d ON d.id = c.document_id
                {where}
                ORDER BY (c.embedding <=> %s::vector) +
                    CASE WHEN d.tags @> '["moc"]'::jsonb THEN %s ELSE 0 END
                LIMIT %s""",
            [vecteur_json, *filtres_params, vecteur_json, penalite, top],
        )
        return [_ligne(l) for l in cur.fetchall()]
    finally:
        pool.putconn(conn)


def _ligne(l) -> dict:
    return {
        "contenu": l[0],
        "nom": l[1],
        "chemin_vault": l[2],
        "dossier": l[3],
        "date_document": l[4].isoformat() if l[4] else None,
        "distance": float(l[5]),
    }


def generer(question: str, extraits: list[dict]) -> str:
    contexte = "\n\n---\n\n".join(
        f"[Source {i + 1} : {e['nom']} ({e['chemin_vault']})]\n{e['contenu']}"
        for i, e in enumerate(extraits)
    )
    system = (
        "Tu es l'assistant de connaissance personnelle de l'utilisateur. "
        "Tu réponds en français, UNIQUEMENT à partir des extraits fournis "
        "(notes Obsidian et documents Google Drive). "
        "Tu écris pour un humain pressé mais exigeant : clair, structuré, actionnable. "
        "Contraintes STRICTES :\n"
        "- N'invente rien. Si l'info n'est pas dans les extraits : dis-le explicitement.\n"
        "- Cite les sources entre parenthèses en fin de phrase quand tu affirmes un fait.\n"
        "- Format de sortie :\n"
        "  RÉSUMÉ (2-3 lignes max)\n"
        "  POINTS CLÉS (3-7 puces)\n"
        "  DÉTAILS (si nécessaire, concis)\n"
        "  ACTIONS (si applicable, checklist)\n"
        "  QUESTIONS (si ambiguïtés, 1-3 questions)\n"
        "- Pas de blabla, pas de disclaimer inutile."
    )
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Extraits :\n\n{contexte}\n\nQuestion : {question}",
                },
            ],
            "temperature": 0.2,
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _deepseek_stream(question: str, extraits: list[dict]):
    """Stream DeepSeek (format SSE OpenAI-like) et yield des deltas texte."""
    contexte = "\n\n---\n\n".join(
        f"[Source {i + 1} : {e['nom']} ({e['chemin_vault']})]\n{e['contenu']}"
        for i, e in enumerate(extraits)
    )
    system = (
        "Tu es l'assistant de connaissance personnelle de l'utilisateur. "
        "Tu réponds en français, UNIQUEMENT à partir des extraits fournis "
        "(notes Obsidian et documents Google Drive). "
        "Tu écris pour un humain pressé mais exigeant : clair, structuré, actionnable. "
        "Contraintes STRICTES :\n"
        "- N'invente rien. Si l'info n'est pas dans les extraits : dis-le explicitement.\n"
        "- Cite les sources entre parenthèses en fin de phrase quand tu affirmes un fait.\n"
        "- Format de sortie :\n"
        "  RÉSUMÉ (2-3 lignes max)\n"
        "  POINTS CLÉS (3-7 puces)\n"
        "  DÉTAILS (si nécessaire, concis)\n"
        "  ACTIONS (si applicable, checklist)\n"
        "  QUESTIONS (si ambiguïtés, 1-3 questions)\n"
        "- Pas de blabla, pas de disclaimer inutile."
    )
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Extraits :\n\n{contexte}\n\nQuestion : {question}",
                },
            ],
            "temperature": 0.2,
            "stream": True,
        },
        timeout=90,
        stream=True,
    )
    r.raise_for_status()

    for raw in r.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        choice = (payload.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        text = delta.get("content") or ""
        if text:
            yield text


def _to_sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode(
        "utf-8"
    )


@app.post("/ask_sse", dependencies=[Depends(verifier_token)])
def ask_sse(req: AskRequest):
    debut = time.time()

    def gen():
        try:
            yield _to_sse("status", {"stage": "search", "label": "🔎 Recherche…"})
            vecteur = embed_question(req.question)
            extraits = rechercher(vecteur, req.dossier, req.tag, req.top)
            if not extraits:
                yield _to_sse(
                    "done",
                    {
                        "answer": "Aucune note trouvée pour cette recherche.",
                        "sources": [],
                        "duration_ms": int((time.time() - debut) * 1000),
                    },
                )
                return

            yield _to_sse("status", {"stage": "generate", "label": "🧠 Génération…"})
            answer_parts: list[str] = []
            for delta in _deepseek_stream(req.question, extraits):
                answer_parts.append(delta)
                yield _to_sse("delta", {"text": delta})
            answer = "".join(answer_parts).strip()

            vus, sources = set(), []
            for e in extraits:
                if e["chemin_vault"] not in vus:
                    vus.add(e["chemin_vault"])
                    sources.append(
                        {
                            "nom": e["nom"],
                            "chemin": e["chemin_vault"],
                            "dossier": e["dossier"],
                            "date_document": e["date_document"],
                            "extrait": e["contenu"][:200],
                        }
                    )

            yield _to_sse(
                "done",
                {
                    "answer": answer or "(réponse vide)",
                    "sources": sources[:15],
                    "duration_ms": int((time.time() - debut) * 1000),
                },
            )
        except requests.RequestException:
            yield _to_sse(
                "error",
                {"message": "Service IA momentanément indisponible"},
            )

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/ask", dependencies=[Depends(verifier_token)])
def ask(req: AskRequest):
    debut = time.time()
    try:
        vecteur = embed_question(req.question)
        extraits = rechercher(vecteur, req.dossier, req.tag, req.top)
        if not extraits:
            return {
                "answer": "Aucune note trouvée pour cette recherche.",
                "sources": [],
                "duration_ms": int((time.time() - debut) * 1000),
            }
        answer = generer(req.question, extraits)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=503, detail="Service IA momentanément indisponible"
        ) from e

    vus, sources = set(), []
    for e in extraits:
        if e["chemin_vault"] not in vus:
            vus.add(e["chemin_vault"])
            sources.append(
                {
                    "nom": e["nom"],
                    "chemin": e["chemin_vault"],
                    "dossier": e["dossier"],
                    "date_document": e["date_document"],
                    "extrait": e["contenu"][:200],
                }
            )

    return {
        "answer": answer,
        "sources": sources[:15],
        "duration_ms": int((time.time() - debut) * 1000),
    }


@app.get("/stats", dependencies=[Depends(verifier_token)])
def stats():
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT dossier) FROM documents")
        nb_docs, nb_dossiers = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM chunks")
        nb_chunks = cur.fetchone()[0]
        cur.execute(
            """SELECT started_at, finished_at, stats, success
               FROM sync_runs ORDER BY id DESC LIMIT 1"""
        )
        row = cur.fetchone()
        last_sync = None
        if row:
            last_sync = {
                "started_at": row[0].isoformat() if row[0] else None,
                "finished_at": row[1].isoformat() if row[1] else None,
                "stats": row[2],
                "success": row[3],
            }
        return {
            "nb_documents": nb_docs,
            "nb_dossiers": nb_dossiers,
            "nb_chunks": nb_chunks,
            "last_sync": last_sync,
        }
    finally:
        pool.putconn(conn)


@app.get("/dossiers", dependencies=[Depends(verifier_token)])
def dossiers():
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT dossier, COUNT(*) FROM documents
               GROUP BY dossier ORDER BY dossier"""
        )
        return {"dossiers": [{"slug": r[0], "nb_documents": r[1]} for r in cur.fetchall()]}
    finally:
        pool.putconn(conn)


@app.get("/health")
def health():
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Base indisponible") from exc
    finally:
        pool.putconn(conn)


@app.get("/open")
def open_obsidian_note(file: str = Query(min_length=1, max_length=500)):
    """Redirect HTTPS → obsidian:// (liens cliquables Telegram)."""
    if ".." in file or file.startswith("/"):
        raise HTTPException(status_code=400, detail="Chemin invalide")
    path = file if file.endswith(".md") else f"{file}.md"
    target = (
        f"obsidian://open?vault={quote(OBSIDIAN_VAULT_NAME, safe='')}"
        f"&file={quote(path, safe='')}"
    )
    return RedirectResponse(target, status_code=302)
