#!/usr/bin/env python3
# Synchronisation vault Obsidian -> pgvector

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests
import yaml

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
PG_DSN = os.environ.get(
    "PG_DSN",
    "host={host} port={port} dbname={db} user={user} password={password}".format(
        host=os.environ.get("PG_HOST", "postgres"),
        port=os.environ.get("PG_PORT", "5432"),
        db=os.environ.get("PG_DB", "rag"),
        user=os.environ.get("PG_USER", "rag_user"),
        password=os.environ.get("PG_PASSWORD", ""),
    ),
)

VAULT_PATH = os.environ.get("VAULT_PATH", "/vault")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
CHUNK_TARGET_CHARS = int(os.environ.get("CHUNK_TARGET_CHARS", "3000"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "400"))
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "64"))

IGNORE_DIRS = {".obsidian", ".trash", ".git", ".cursor", "node_modules"}
IGNORE_PREFIXES = ("._", "~$")


def slugifier(nom: str) -> str:
    s = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "racine"


def deviner_date(nom: str) -> str | None:
    m = re.search(r"(20\d{2})[-_./](\d{1,2})[-_./](\d{1,2})", nom)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            return None
    return None


def _eclater(bloc: str) -> list[str]:
    if len(bloc) <= CHUNK_TARGET_CHARS:
        return [bloc]
    for sep in ("\n", ". ", ", "):
        if sep in bloc:
            morceaux, courant = [], ""
            for part in bloc.split(sep):
                if len(courant) + len(part) + len(sep) > CHUNK_TARGET_CHARS and courant:
                    morceaux.append(courant)
                    courant = part
                else:
                    courant = courant + sep + part if courant else part
            if courant:
                morceaux.append(courant)
            final = []
            for mo in morceaux:
                final.extend(_eclater(mo) if len(mo) > CHUNK_TARGET_CHARS else [mo])
            return final
    pas = CHUNK_TARGET_CHARS - CHUNK_OVERLAP_CHARS
    return [bloc[i : i + CHUNK_TARGET_CHARS] for i in range(0, len(bloc), pas)]


def chunker(texte: str) -> list[str]:
    paragraphes = [p.strip() for p in re.split(r"\n\s*\n", texte) if p.strip()]
    blocs = []
    for p in paragraphes:
        blocs.extend(_eclater(p))
    chunks, courant = [], ""
    for p in blocs:
        if len(courant) + len(p) + 2 > CHUNK_TARGET_CHARS and courant:
            chunks.append(courant.strip())
            courant = courant[-CHUNK_OVERLAP_CHARS:] + "\n\n" + p
        else:
            courant = (courant + "\n\n" + p) if courant else p
    if courant.strip():
        chunks.append(courant.strip())
    return chunks


def nettoyer(texte: str) -> str:
    texte = "".join(c if c.isprintable() or c in "\n\t" else " " for c in texte)
    return re.sub(r"[ \t]{3,}", "  ", texte)


def parse_frontmatter(texte: str) -> tuple[dict, str]:
    if not texte.startswith("---"):
        return {}, texte
    parts = texte.split("---", 2)
    if len(parts) < 3:
        return {}, texte
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].lstrip("\n")


def extraire_tags(meta: dict) -> list[str]:
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        return [tags]
    if isinstance(tags, list):
        return [str(t) for t in tags]
    return []


def embed(textes: list[str]) -> list[list[float]]:
    textes = [(t.strip() or "(vide)")[:6000] for t in textes]
    vecteurs = []
    for i in range(0, len(textes), EMBED_BATCH_SIZE):
        lot = textes[i : i + EMBED_BATCH_SIZE]
        for tentative in range(3):
            try:
                r = requests.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={"model": EMBEDDING_MODEL, "input": lot},
                    timeout=120,
                )
                if r.status_code == 400:
                    raise ValueError(f"Embeddings 400 : {r.text[:300]}")
                r.raise_for_status()
                data = r.json()["data"]
                vecteurs.extend(d["embedding"] for d in sorted(data, key=lambda d: d["index"]))
                break
            except requests.RequestException as e:
                if tentative == 2:
                    raise
                print(f"  ! retry embeddings ({e})")
                time.sleep(5 * (tentative + 1))
    return vecteurs


def lister_notes(vault: Path) -> list[Path]:
    notes = []
    for path in vault.rglob("*.md"):
        rel = path.relative_to(vault)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if path.name.startswith(IGNORE_PREFIXES):
            continue
        notes.append(path)
    return notes


def dossier_de(chemin_relatif: Path) -> str:
    if len(chemin_relatif.parts) <= 1:
        return "racine"
    return slugifier(chemin_relatif.parent.parts[0])


def sync(args):
    vault = Path(VAULT_PATH)
    if not vault.is_dir():
        sys.exit(f"Vault introuvable : {vault}")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Scan {vault}...")
    notes = lister_notes(vault)
    print(f"{len(notes)} note(s) .md")

    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sync_runs (started_at) VALUES (now()) RETURNING id"
    )
    run_id = cur.fetchone()[0]
    conn.commit()

    cur.execute("SELECT source_id, modified_time FROM documents")
    connus = {row[0]: row[1] for row in cur.fetchall()}

    stats = {"nouveau": 0, "modifie": 0, "inchange": 0, "echec": 0}
    ids_vus = set()

    for path in notes:
        rel = path.relative_to(vault)
        source_id = rel.as_posix()
        ids_vus.add(source_id)

        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if source_id in connus and connus[source_id] >= mtime:
            stats["inchange"] += 1
            continue

        if args.limit and stats["nouveau"] + stats["modifie"] >= args.limit:
            break

        nom = path.stem
        dossier = dossier_de(rel)
        est_nouveau = source_id not in connus
        print(f"{'[+]' if est_nouveau else '[~]'} {source_id}")

        if args.dry_run:
            stats["nouveau" if est_nouveau else "modifie"] += 1
            continue

        try:
            brut = path.read_text(encoding="utf-8", errors="replace")
            meta, corps = parse_frontmatter(brut)
            tags = extraire_tags(meta)
            titre = meta.get("title") or nom
            texte = nettoyer(corps)
            date_doc = meta.get("date") or deviner_date(nom)
            if isinstance(date_doc, str) and len(date_doc) >= 10:
                date_doc = date_doc[:10]
            else:
                date_doc = deviner_date(nom)

            if len(texte.strip()) < 20:
                statut = "metadonnees_seul"
                fiche = (
                    f"Note Obsidian « {titre} », dossier {dossier}, "
                    f"chemin {source_id}, modifiée le {mtime.date()}."
                )
                chunks = []
                entete = f"[Note : {titre}, dossier {dossier}]\n"
                vecteurs = embed([fiche])
            else:
                statut = "complet"
                fiche = (
                    f"Note Obsidian « {titre} », dossier {dossier}, "
                    f"chemin {source_id}."
                )
                chunks = chunker(texte)
                entete = (
                    f"[Note : {titre}"
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
                   VALUES (%s,%s,%s,'text/markdown',%s,%s::jsonb,%s,%s,%s)
                   RETURNING id""",
                (
                    source_id,
                    titre,
                    source_id,
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
            print(f"  ! ÉCHEC {source_id} : {str(e)[:200]}")
            stats["echec"] += 1

    if not args.dry_run:
        supprimes = [s for s in connus if s not in ids_vus]
        if supprimes:
            cur.execute(
                "DELETE FROM documents WHERE source_id = ANY(%s)", (supprimes,)
            )
            conn.commit()
            print(f"[-] {len(supprimes)} note(s) supprimée(s) du vault")

        cur.execute(
            """UPDATE sync_runs SET finished_at = now(), stats = %s, success = %s
               WHERE id = %s""",
            (json.dumps(stats), stats["echec"] == 0, run_id),
        )
        conn.commit()

    cur.close()
    conn.close()
    print(f"Terminé : {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Obsidian -> pgvector")
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
