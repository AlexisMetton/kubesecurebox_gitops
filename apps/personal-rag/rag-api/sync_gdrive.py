#!/usr/bin/env python3
# Synchronisation Google Drive personnel -> pgvector

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import psycopg2
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from pypdf import PdfReader

from sync_obsidian import PG_DSN, chunker, deviner_date, embed, nettoyer, slugifier

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "").strip() or "root"
GDRIVE_MAX_FILE_BYTES = int(os.environ.get("GDRIVE_MAX_FILE_BYTES", str(15 * 1024 * 1024)))
CONTENT_SOURCE = "google-drive"
SOURCE_PREFIX = "gdrive:"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

EXPORT_MIMES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

TEXT_EXTENSIONS = (
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".html",
    ".htm",
    ".xml",
    ".yaml",
    ".yml",
    ".log",
    ".rst",
)

SKIP_MIME_PREFIXES = tuple(
    p.strip()
    for p in os.environ.get(
        "GDRIVE_SKIP_MIME_PREFIXES", "image/,video/,audio/,application/zip"
    ).split(",")
    if p.strip()
)


def _credentials() -> Credentials:
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        sys.exit("GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET et GOOGLE_REFRESH_TOKEN requis")
    creds = Credentials(
        None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _parse_modified(value: str) -> datetime:
    dt = parsedate_to_datetime(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _mime_skip(mime_type: str) -> bool:
    return any(mime_type.startswith(prefix) for prefix in SKIP_MIME_PREFIXES)


def _dossier(folder_path: str) -> str:
    parts = [p for p in folder_path.strip("/").split("/") if p]
    if not parts:
        return "gdrive-racine"
    return "gdrive-" + slugifier(parts[0])


def _source_id(file_id: str) -> str:
    return f"{SOURCE_PREFIX}{file_id}"


def _drive_link(file_id: str, web_view_link: str | None) -> str:
    if web_view_link:
        return web_view_link
    return f"https://drive.google.com/file/d/{file_id}/view"


def _download_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _extract_text(service, meta: dict) -> str | None:
    file_id = meta["id"]
    mime_type = meta.get("mimeType", "")
    name = meta.get("name", "sans-nom")
    size = int(meta.get("size") or 0)

    if mime_type == "application/vnd.google-apps.folder":
        return None
    if _mime_skip(mime_type):
        return None
    if size and size > GDRIVE_MAX_FILE_BYTES:
        print(f"  ! ignoré (>{GDRIVE_MAX_FILE_BYTES} o) : {name}")
        return None

    try:
        if mime_type in EXPORT_MIMES:
            data = (
                service.files()
                .export(fileId=file_id, mimeType=EXPORT_MIMES[mime_type])
                .execute()
            )
            if isinstance(data, bytes):
                return data.decode("utf-8", errors="replace")
            return str(data)

        if mime_type == "application/pdf" or name.lower().endswith(".pdf"):
            return _extract_pdf(_download_bytes(service, file_id))

        if mime_type.startswith("text/") or name.lower().endswith(TEXT_EXTENSIONS):
            return _download_bytes(service, file_id).decode("utf-8", errors="replace")

        print(f"  ! type non supporté ({mime_type}) : {name}")
        return None
    except HttpError as e:
        print(f"  ! HTTP {e.resp.status} : {name}")
        return None
    except Exception as e:
        print(f"  ! extraction : {name} — {str(e)[:200]}")
        return None


def _iter_files(service, folder_id: str, folder_path: str = ""):
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=query,
                pageSize=100,
                fields=(
                    "nextPageToken, files(id, name, mimeType, modifiedTime, "
                    "webViewLink, size)"
                ),
                pageToken=page_token,
                supportsAllDrives=False,
                includeItemsFromAllDrives=False,
            )
            .execute()
        )
        for item in resp.get("files", []):
            if item.get("mimeType") == "application/vnd.google-apps.folder":
                sub_path = f"{folder_path}/{item['name']}".strip("/")
                yield from _iter_files(service, item["id"], sub_path)
            else:
                yield item, folder_path
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def sync(args):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Scan Google Drive...")
    service = build("drive", "v3", credentials=_credentials(), cache_discovery=False)

    fichiers = list(_iter_files(service, GDRIVE_FOLDER_ID))
    print(f"{len(fichiers)} fichier(s) Drive (hors dossiers)")

    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("INSERT INTO sync_runs (started_at) VALUES (now()) RETURNING id")
    run_id = cur.fetchone()[0]
    conn.commit()

    cur.execute("SELECT source_id, modified_time FROM documents WHERE source_id LIKE %s", (f"{SOURCE_PREFIX}%",))
    connus = {row[0]: row[1] for row in cur.fetchall()}

    stats = {"nouveau": 0, "modifie": 0, "inchange": 0, "echec": 0, "ignore": 0}
    ids_vus = set()

    for meta, folder_path in fichiers:
        file_id = meta["id"]
        source_id = _source_id(file_id)
        ids_vus.add(source_id)

        mtime = _parse_modified(meta["modifiedTime"])
        if source_id in connus and connus[source_id] >= mtime:
            stats["inchange"] += 1
            continue

        if args.limit and stats["nouveau"] + stats["modifie"] >= args.limit:
            break

        nom = meta.get("name", "sans-nom")
        dossier = _dossier(folder_path)
        est_nouveau = source_id not in connus
        print(f"{'[+]' if est_nouveau else '[~]'} {nom} [{dossier}]")

        if args.dry_run:
            stats["nouveau" if est_nouveau else "modifie"] += 1
            continue

        try:
            texte_brut = _extract_text(service, meta)
            if texte_brut is None:
                stats["ignore"] += 1
                continue

            texte = nettoyer(texte_brut)
            titre = Path(nom).stem
            tags = ["gdrive"]
            mime_type = meta.get("mimeType", "application/octet-stream")
            chemin_vault = _drive_link(file_id, meta.get("webViewLink"))
            date_doc = deviner_date(nom)

            if len(texte.strip()) < 20:
                statut = "metadonnees_seul"
                fiche = (
                    f"Document {CONTENT_SOURCE} « {titre} », dossier {dossier}, "
                    f"lien {chemin_vault}, modifié le {mtime.date()}."
                )
                chunks = []
                vecteurs = embed([fiche])
            else:
                statut = "complet"
                fiche = (
                    f"Document {CONTENT_SOURCE} « {titre} », dossier {dossier}, "
                    f"lien {chemin_vault}."
                )
                chunks = chunker(texte)
                entete = (
                    f"[Document Drive : {titre}"
                    + (f", daté du {date_doc}" if date_doc else "")
                    + f", dossier {dossier}]\n"
                )
                chunks = [entete + c for c in chunks]
                vecteurs = embed([fiche] + chunks)

            tags_json = json.dumps(tags)
            cur.execute("DELETE FROM documents WHERE source_id = %s", (source_id,))
            cur.execute(
                """INSERT INTO documents
                   (source_id, nom, chemin_vault, mime_type, dossier, tags,
                    date_document, modified_time, statut_ingestion)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                   RETURNING id""",
                (
                    source_id,
                    titre,
                    chemin_vault,
                    mime_type,
                    dossier,
                    tags_json,
                    date_doc,
                    mtime,
                    statut,
                ),
            )
            doc_id = cur.fetchone()[0]

            cur.execute(
                """INSERT INTO chunks
                   (document_id, type_chunk, contenu, position, embedding,
                    dossier, tags, date_document)
                   VALUES (%s,'fiche_fichier',%s,0,%s,%s,%s::jsonb,%s)""",
                (doc_id, fiche, json.dumps(vecteurs[0]), dossier, tags_json, date_doc),
            )
            for i, (chunk, vec) in enumerate(zip(chunks, vecteurs[1:]), start=1):
                cur.execute(
                    """INSERT INTO chunks
                       (document_id, type_chunk, contenu, position, embedding,
                        dossier, tags, date_document)
                       VALUES (%s,'contenu',%s,%s,%s,%s,%s::jsonb,%s)""",
                    (
                        doc_id,
                        chunk,
                        i,
                        json.dumps(vec),
                        dossier,
                        tags_json,
                        date_doc,
                    ),
                )
            conn.commit()
            stats["nouveau" if est_nouveau else "modifie"] += 1
        except Exception as e:
            conn.rollback()
            print(f"  ! ÉCHEC {nom} : {str(e)[:200]}")
            stats["echec"] += 1

    if not args.dry_run:
        supprimes = [s for s in connus if s not in ids_vus and s.startswith(SOURCE_PREFIX)]
        if supprimes:
            cur.execute("DELETE FROM documents WHERE source_id = ANY(%s)", (supprimes,))
            conn.commit()
            print(f"[-] {len(supprimes)} fichier(s) Drive retiré(s) de l'index")

        cur.execute(
            """UPDATE sync_runs SET finished_at = now(), stats = %s, success = %s
               WHERE id = %s""",
            (json.dumps({**stats, "source": "gdrive"}), stats["echec"] == 0, run_id),
        )
        conn.commit()

    cur.close()
    conn.close()
    print(f"Terminé : {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Google Drive -> pgvector")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limiter le nb de fichiers ingérés (0 = illimité)",
    )
    args = parser.parse_args()
    if not OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY manquante")
    sync(args)
