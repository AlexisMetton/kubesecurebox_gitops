# Client HTTP vers personal-rag
import os

import requests

RAG_API_URL = os.environ.get(
    "RAG_API_URL", "http://rag-api.personal-rag.svc.cluster.local:8000"
).rstrip("/")
RAG_API_TOKEN = os.environ["RAG_API_TOKEN"]

_TIMEOUT = 90


def _headers() -> dict:
    return {"Authorization": f"Bearer {RAG_API_TOKEN}"}


def ask(question: str, dossier: str | None = None, tag: str | None = None, top: int = 8) -> dict:
    payload: dict = {"question": question, "top": top}
    if dossier:
        payload["dossier"] = dossier
    if tag:
        payload["tag"] = tag
    r = requests.post(
        f"{RAG_API_URL}/ask",
        headers=_headers(),
        json=payload,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def search(question: str, dossier: str | None = None, tag: str | None = None, top: int = 8) -> dict:
    payload: dict = {"question": question, "top": top}
    if dossier:
        payload["dossier"] = dossier
    if tag:
        payload["tag"] = tag
    r = requests.post(
        f"{RAG_API_URL}/search",
        headers=_headers(),
        json=payload,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def stats() -> dict:
    r = requests.get(f"{RAG_API_URL}/stats", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def dossiers() -> dict:
    r = requests.get(f"{RAG_API_URL}/dossiers", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()
