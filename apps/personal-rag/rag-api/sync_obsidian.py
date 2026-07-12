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
CONTENT_SOURCE = os.environ.get("CONTENT_SOURCE", "obsidian")
PARA_ROOT = os.environ.get("PARA_ROOT", "Second Cerveau")

IGNORE_DIRS = {".obsidian", ".trash", ".git", ".cursor", "node_modules", "_Import"}
IGNORE_PREFIXES = ("._", "~$")
SKIP_PATH_PREFIXES = tuple(
    p.strip().replace("\\", "/").strip("/")
    for p in os.environ.get(
        "SKIP_PATH_PREFIXES",
        "Second Cerveau/0 INBOX,Second Cerveau/_Import,Second Cerveau/6 GARDEN/A traiter",
    ).split(",")
    if p.strip()
)

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
TAG_INLINE_RE = re.compile(r"(?:^|[\s(])#([a-zA-Z][\w/-]*)")
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


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
    match = FRONTMATTER_RE.match(texte)
    if not match:
        return {}, texte
    try:
        meta = yaml.safe_load(match.group(1))
        if not isinstance(meta, dict):
            return {}, texte
    except yaml.YAMLError:
        return {}, texte
    return meta, texte[match.end() :]


def extraire_titre(meta: dict, corps: str, nom_fichier: str) -> str:
    titre = meta.get("title")
    if isinstance(titre, str) and titre.strip():
        return titre.strip()
    match = H1_RE.search(corps)
    if match:
        return match.group(1).strip()
    return nom_fichier


def extraire_tags(meta: dict, corps: str, est_moc: bool = False) -> list[str]:
    tags: list[str] = []
    raw = meta.get("tags", [])
    if isinstance(raw, str):
        tags.append(raw)
    elif isinstance(raw, list):
        tags.extend(str(t) for t in raw)
    for match in TAG_INLINE_RE.finditer(corps):
        tag = match.group(1)
        if tag not in tags:
            tags.append(tag)
    if est_moc and "moc" not in tags:
        tags.append("moc")
    return tags


def note_est_moc(meta: dict, rel: Path, nom_fichier: str) -> bool:
    doc_type = meta.get("type")
    if isinstance(doc_type, str) and doc_type.strip().lower() == "moc":
        return True
    posix = rel.as_posix()
    if "/MOC/" in posix or posix.startswith("MOC/"):
        return True
    stem = Path(nom_fichier).stem
    if stem.startswith("MOC ") or stem == "MOC Portefeuille":
        return True
    return False


def _cles_index(rel: Path) -> list[str]:
    posix = rel.as_posix()
    sans_ext = posix[:-3] if posix.endswith(".md") else posix
    return [posix.lower(), sans_ext.lower(), rel.stem.lower()]


def construire_index_liens(
    vault: Path, notes: list[Path]
) -> tuple[dict[str, tuple[str, str]], dict[str, list[tuple[Path, str, str]]]]:
    lookup: dict[str, tuple[str, str]] = {}
    par_stem: dict[str, list[tuple[Path, str, str]]] = {}
    for path in notes:
        rel = path.relative_to(vault)
        source_id = rel.as_posix()
        titre = path.stem
        for key in _cles_index(rel):
            lookup[key] = (titre, source_id)
        par_stem.setdefault(path.stem.lower(), []).append((rel.parent, titre, source_id))
    return lookup, par_stem


def _candidats_lien(cible: str, note_rel: Path) -> list[str]:
    cible = cible.replace("\\", "/").strip().split("#")[0].strip()
    note_dir = note_rel.parent
    noms = [cible]
    if not cible.endswith(".md"):
        noms.append(f"{cible}.md")
    candidats = []
    for base in (note_dir, Path(".")):
        for nom in noms:
            rel = (base / nom).as_posix()
            candidats.append(rel)
            candidats.append(rel.removesuffix(".md"))
    return candidats


def resoudre_wikilink(
    cible: str,
    note_rel: Path,
    lookup: dict[str, tuple[str, str]],
    par_stem: dict[str, list[tuple[Path, str, str]]],
) -> tuple[str, str] | None:
    for key in _candidats_lien(cible, note_rel):
        if key.lower() in lookup:
            return lookup[key.lower()]
    stem = Path(cible.split("#")[0].strip()).stem.lower()
    candidats = par_stem.get(stem, [])
    if len(candidats) == 1:
        return candidats[0][1], candidats[0][2]
    if len(candidats) > 1:
        note_dir = note_rel.parent
        for parent, titre, source_id in candidats:
            if parent == note_dir:
                return titre, source_id
        return candidats[0][1], candidats[0][2]
    return None


def enrichir_wikilinks(
    texte: str,
    note_rel: Path,
    lookup: dict[str, tuple[str, str]],
    par_stem: dict[str, list[tuple[Path, str, str]]],
) -> str:
    def remplacer(match: re.Match) -> str:
        cible = match.group(1).strip()
        alias = (match.group(2) or "").strip()
        resolu = resoudre_wikilink(cible, note_rel, lookup, par_stem)
        if resolu:
            titre, chemin = resolu
            label = alias or titre
            return f"{label} (note liée : {titre}, chemin {chemin})"
        return alias or cible

    return WIKILINK_RE.sub(remplacer, texte)


def chemin_exclu(rel: Path) -> bool:
    posix = rel.as_posix()
    for prefix in SKIP_PATH_PREFIXES:
        if posix == prefix or posix.startswith(prefix + "/"):
            return True
    return False


def lister_notes(vault: Path) -> tuple[list[Path], int]:
    notes = []
    ignores = 0
    for path in vault.rglob("*.md"):
        rel = path.relative_to(vault)
        if any(part in IGNORE_DIRS for part in rel.parts):
            ignores += 1
            continue
        if path.name.startswith(IGNORE_PREFIXES):
            ignores += 1
            continue
        if chemin_exclu(rel):
            ignores += 1
            continue
        notes.append(path)
    return notes, ignores


def dossier_de(chemin_relatif: Path) -> str:
    parts = chemin_relatif.parts
    if len(parts) <= 1:
        return "racine"
    if parts[0] == PARA_ROOT and len(parts) >= 2:
        para = slugifier(parts[1])
        if len(parts) >= 4:
            return f"{para}-{slugifier(parts[2])}"
        return para
    return slugifier(parts[0])


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


def sync(args):
    vault = Path(VAULT_PATH)
    if not vault.is_dir():
        sys.exit(f"Vault introuvable : {vault}")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Scan {vault}...")
    notes, ignores = lister_notes(vault)
    print(f"{len(notes)} note(s) .md ({ignores} ignorée(s))")
    if SKIP_PATH_PREFIXES:
        print(f"Exclusions : {', '.join(SKIP_PATH_PREFIXES)}")

    lookup, par_stem = construire_index_liens(vault, notes)

    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("INSERT INTO sync_runs (started_at) VALUES (now()) RETURNING id")
    run_id = cur.fetchone()[0]
    conn.commit()

    cur.execute("SELECT source_id, modified_time FROM documents")
    connus = {row[0]: row[1] for row in cur.fetchall()}

    stats = {"nouveau": 0, "modifie": 0, "inchange": 0, "echec": 0, "ignore": ignores}
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
        print(f"{'[+]' if est_nouveau else '[~]'} {source_id} [{dossier}]")

        if args.dry_run:
            stats["nouveau" if est_nouveau else "modifie"] += 1
            continue

        try:
            brut = path.read_text(encoding="utf-8", errors="replace")
            meta, corps = parse_frontmatter(brut)
            titre = extraire_titre(meta, corps, nom)
            tags = extraire_tags(meta, corps, est_moc=note_est_moc(meta, rel, nom))
            texte = nettoyer(enrichir_wikilinks(corps, rel, lookup, par_stem))
            date_doc = meta.get("date") or deviner_date(nom)
            if isinstance(date_doc, str) and len(date_doc) >= 10:
                date_doc = date_doc[:10]
            else:
                date_doc = deviner_date(nom)

            if len(texte.strip()) < 20:
                statut = "metadonnees_seul"
                fiche = (
                    f"Note {CONTENT_SOURCE} « {titre} », dossier {dossier}, "
                    f"chemin {source_id}, modifiée le {mtime.date()}."
                )
                chunks = []
                vecteurs = embed([fiche])
            else:
                statut = "complet"
                fiche = (
                    f"Note {CONTENT_SOURCE} « {titre} », dossier {dossier}, "
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
            cur.execute("DELETE FROM documents WHERE source_id = ANY(%s)", (supprimes,))
            conn.commit()
            print(f"[-] {len(supprimes)} note(s) retirée(s) de l'index")

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
