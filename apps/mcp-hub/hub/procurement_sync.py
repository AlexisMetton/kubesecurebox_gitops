#!/usr/bin/env python3
"""Sync BOAMP (OpenDataSoft Explore v2.1) → mcp_procurement_notices.

Usage (CronJob / local) :
  python procurement_sync.py --dept 58
  python procurement_sync.py --dept 58 --max-pages 5
  python procurement_sync.py --dept 58 --since 2023-01-01
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

# psycopg2 / db uniquement au moment du sync (pas pour normalize_record)

BOAMP_BASE = os.environ.get(
    "BOAMP_API_BASE",
    "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp",
)
PAGE_SIZE = 100
SOURCE = "boamp"

_SIREN_RE = re.compile(r"\b(\d{9})\b")
_AMOUNT_RE = re.compile(
    r"(?i)(?:montant|valeur|amount|ht)[^\d]{0,40}(\d[\d\s.,]{2,})",
)


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "kubesecurebox-mcp-hub-procurement/1.0",
    }


def _parse_json_maybe(val: Any) -> Any:
    if val is None or val == "":
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if s[0] in "{[":
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return val
    return val


def _walk_find(obj: Any, keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.upper() in keys or k.lower() in {x.lower() for x in keys}:
                found.append(v)
            found.extend(_walk_find(v, keys))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk_find(item, keys))
    return found


def _first_str(vals: list[Any]) -> str:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float, Decimal)):
            return str(v)
        if isinstance(v, dict):
            for kk in ("DENOMINATION", "NOM", "NAME", "RAISON_SOCIALE", "VALUE", "content"):
                if kk in v and str(v[kk]).strip():
                    return str(v[kk]).strip()
            # nested single value
            for vv in v.values():
                if isinstance(vv, str) and vv.strip():
                    return vv.strip()
        if isinstance(v, list) and v:
            s = _first_str(v)
            if s:
                return s
    return ""


def _extract_siren(*texts: str) -> str:
    for t in texts:
        if not t:
            continue
        m = _SIREN_RE.search(t.replace(" ", ""))
        if m:
            return m.group(1)
        m = _SIREN_RE.search(t)
        if m:
            return m.group(1)
    return ""


def _parse_amount(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float, Decimal)):
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return None
    if isinstance(val, dict):
        for k in ("VALUE", "value", "HT", "TTC", "MONTANT", "montant"):
            if k in val:
                a = _parse_amount(val[k])
                if a is not None:
                    return a
        return None
    if isinstance(val, str):
        cleaned = (
            val.replace("\u00a0", " ")
            .replace("€", "")
            .replace("EUR", "")
            .strip()
        )
        cleaned = cleaned.replace(" ", "").replace(",", ".")
        cleaned = re.sub(r"[^\d.]", "", cleaned)
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def _extract_amount(donnees: Any, titulaire_raw: Any) -> Decimal | None:
    for blob in (donnees, titulaire_raw):
        if blob is None:
            continue
        for keyset in (
            {"MONTANT", "MONTANT_HT", "VALEUR", "VALUE", "AMOUNT"},
            {"estimation", "ESTIMATION"},
        ):
            for v in _walk_find(blob, keyset):
                a = _parse_amount(v)
                if a is not None and a > 0:
                    return a
    # fallback regex on stringified donnees
    if isinstance(donnees, (dict, list)):
        text = json.dumps(donnees, ensure_ascii=False)
        m = _AMOUNT_RE.search(text)
        if m:
            return _parse_amount(m.group(1))
    return None


def _extract_winner(rec: dict, donnees: Any) -> tuple[str, str]:
    titulaire = _parse_json_maybe(rec.get("titulaire"))
    name = ""
    if isinstance(titulaire, str):
        name = titulaire.strip()
    elif titulaire is not None:
        name = _first_str([titulaire])
    if not name and donnees is not None:
        name = _first_str(
            _walk_find(
                donnees,
                {
                    "TITULAIRE",
                    "TITULAIRES",
                    "ATTRIBUTAIRE",
                    "DENOMINATION_SOCIALE",
                    "DENOMINATION",
                },
            )
        )
    siren = _extract_siren(name, json.dumps(titulaire, ensure_ascii=False) if titulaire else "")
    if not siren and donnees is not None:
        for v in _walk_find(donnees, {"SIREN", "siren", "ID_SIREN"}):
            s = _extract_siren(str(v))
            if s:
                siren = s
                break
    return name[:500], siren


def _extract_buyer(rec: dict, donnees: Any) -> tuple[str, str, str]:
    name = (rec.get("nomacheteur") or "").strip()
    city = ""
    siren = ""
    if donnees is not None:
        ident = None
        for v in _walk_find(donnees, {"IDENTITE"}):
            if isinstance(v, dict):
                ident = v
                break
        if isinstance(ident, dict):
            if not name:
                name = str(ident.get("DENOMINATION") or "").strip()
            city = str(ident.get("VILLE") or "").strip()
            siren = _extract_siren(
                str(ident.get("SIRET") or ""),
                str(ident.get("SIREN") or ""),
                str(ident.get("CODE_IDENT_NATIONAL") or ""),
            )
            if len(siren) > 9:
                siren = siren[:9]
        if not siren:
            for v in _walk_find(donnees, {"SIREN", "SIRET"}):
                s = "".join(c for c in str(v) if c.isdigit())
                if len(s) >= 9:
                    siren = s[:9]
                    break
    return name[:500], siren, city[:200]


def _dept_from_rec(rec: dict) -> str:
    raw = rec.get("code_departement")
    if isinstance(raw, list) and raw:
        return str(raw[0]).strip()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    pre = rec.get("code_departement_prestation")
    if pre:
        return str(pre).strip()
    return ""


def _notice_type(rec: dict) -> str:
    nature = (rec.get("nature") or "").strip()
    lib = (rec.get("nature_libelle") or "").strip()
    if nature:
        return f"{nature}" + (f" ({lib})" if lib else "")
    return lib or "inconnu"


def _source_url(rec: dict) -> str:
    url = (rec.get("url_avis") or "").strip()
    if url:
        return url
    idweb = (rec.get("idweb") or "").strip()
    if idweb:
        return f"https://www.boamp.fr/pages/avis/?q=idweb:{idweb}"
    eid = (rec.get("id") or "").strip()
    if eid:
        return f"https://www.boamp.fr/pages/avis/?q=idweb:{eid.replace('_', '-')}"
    return ""


def _published_at(rec: dict) -> date | None:
    raw = rec.get("dateparution")
    if not raw:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    s = str(raw)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def normalize_record(rec: dict) -> dict | None:
    external_id = str(rec.get("id") or rec.get("idweb") or "").strip()
    if not external_id:
        return None
    donnees = _parse_json_maybe(rec.get("donnees"))
    gestion = _parse_json_maybe(rec.get("gestion"))
    winner_name, winner_siren = _extract_winner(rec, donnees)
    buyer_name, buyer_siren, buyer_city = _extract_buyer(rec, donnees)
    title = (rec.get("objet") or "").strip()
    if not title and isinstance(donnees, dict):
        title = _first_str(_walk_find(donnees, {"TITRE_MARCHE", "OBJET_COMPLET", "RESUME_OBJET"}))
    description = ""
    if isinstance(donnees, dict):
        description = _first_str(_walk_find(donnees, {"OBJET_COMPLET", "RESUME_OBJET"}))[:4000]
    amount = _extract_amount(donnees, _parse_json_maybe(rec.get("titulaire")))
    slim_raw = {
        "id": rec.get("id"),
        "idweb": rec.get("idweb"),
        "nature": rec.get("nature"),
        "nature_libelle": rec.get("nature_libelle"),
        "type_procedure": rec.get("type_procedure"),
        "famille": rec.get("famille"),
        "dateparution": rec.get("dateparution"),
        "nomacheteur": rec.get("nomacheteur"),
        "titulaire": rec.get("titulaire"),
        "url_avis": rec.get("url_avis"),
        "code_departement": rec.get("code_departement"),
        "objet": rec.get("objet"),
        "gestion": gestion if isinstance(gestion, (dict, list)) else None,
    }
    return {
        "source": SOURCE,
        "external_id": external_id[:120],
        "notice_type": _notice_type(rec)[:200],
        "title": title[:2000],
        "description": description,
        "published_at": _published_at(rec),
        "buyer_name": buyer_name,
        "buyer_siren": buyer_siren,
        "buyer_city": buyer_city,
        "buyer_dept": _dept_from_rec(rec)[:3],
        "winner_name": winner_name,
        "winner_siren": winner_siren,
        "amount_ht": amount,
        "currency": "EUR",
        "source_url": _source_url(rec)[:1000],
        "raw_json": slim_raw,
    }


def _set_sync_state(
    source: str,
    *,
    status: str,
    records: int = 0,
    error: str = "",
    cursor: str = "",
) -> None:
    import db

    assert db.pool is not None
    conn = db.pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mcp_procurement_sync_state
                    (source, cursor_token, last_success_at, last_error, status,
                     records_upserted, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (source) DO UPDATE SET
                    cursor_token = EXCLUDED.cursor_token,
                    last_success_at = CASE
                        WHEN EXCLUDED.status = 'ok' THEN EXCLUDED.last_success_at
                        ELSE mcp_procurement_sync_state.last_success_at
                    END,
                    last_error = EXCLUDED.last_error,
                    status = EXCLUDED.status,
                    records_upserted = EXCLUDED.records_upserted,
                    updated_at = now()
                """,
                (
                    source,
                    cursor,
                    datetime.now(timezone.utc) if status == "ok" else None,
                    error[:2000],
                    status,
                    records,
                ),
            )
        conn.commit()
    finally:
        db.pool.putconn(conn)


def upsert_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    import db
    import psycopg2.extras

    assert db.pool is not None
    prepared = []
    for row in rows:
        r = dict(row)
        r["raw_json"] = psycopg2.extras.Json(r.get("raw_json") or {})
        prepared.append(r)
    sql = """
        INSERT INTO mcp_procurement_notices (
            source, external_id, notice_type, title, description, published_at,
            buyer_name, buyer_siren, buyer_city, buyer_dept,
            winner_name, winner_siren, amount_ht, currency, source_url, raw_json,
            updated_at
        ) VALUES (
            %(source)s, %(external_id)s, %(notice_type)s, %(title)s, %(description)s,
            %(published_at)s, %(buyer_name)s, %(buyer_siren)s, %(buyer_city)s,
            %(buyer_dept)s, %(winner_name)s, %(winner_siren)s, %(amount_ht)s,
            %(currency)s, %(source_url)s, %(raw_json)s, now()
        )
        ON CONFLICT (source, external_id) DO UPDATE SET
            notice_type = EXCLUDED.notice_type,
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            published_at = EXCLUDED.published_at,
            buyer_name = EXCLUDED.buyer_name,
            buyer_siren = EXCLUDED.buyer_siren,
            buyer_city = EXCLUDED.buyer_city,
            buyer_dept = EXCLUDED.buyer_dept,
            winner_name = EXCLUDED.winner_name,
            winner_siren = EXCLUDED.winner_siren,
            amount_ht = EXCLUDED.amount_ht,
            currency = EXCLUDED.currency,
            source_url = EXCLUDED.source_url,
            raw_json = EXCLUDED.raw_json,
            updated_at = now()
    """
    conn = db.pool.getconn()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, prepared, page_size=50)
        conn.commit()
        return len(prepared)
    finally:
        db.pool.putconn(conn)


def fetch_page(
    *,
    dept: str | None,
    since: str | None,
    offset: int,
    limit: int = PAGE_SIZE,
) -> tuple[list[dict], int]:
    where_parts: list[str] = []
    if dept:
        # ODS : membership / equality on multi-valued field
        where_parts.append(f'code_departement="{dept}"')
    if since:
        where_parts.append(f'dateparution>="{since}"')
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
        "order_by": "dateparution desc",
    }
    if where_parts:
        params["where"] = " AND ".join(where_parts)
    url = f"{BOAMP_BASE}/records"
    r = requests.get(url, params=params, headers=_headers(), timeout=120)
    r.raise_for_status()
    payload = r.json()
    results = payload.get("results") or []
    total = int(payload.get("total_count") or 0)
    return results, total


def sync(
    *,
    dept: str | None = "58",
    since: str | None = None,
    max_pages: int | None = None,
) -> dict:
    import db

    db.init_pool()
    db.ensure_schema()
    _set_sync_state(SOURCE, status="running", records=0, cursor=f"dept={dept}")
    upserted = 0
    offset = 0
    pages = 0
    total = None
    try:
        while True:
            if max_pages is not None and pages >= max_pages:
                break
            records, total = fetch_page(
                dept=dept, since=since, offset=offset, limit=PAGE_SIZE
            )
            if not records:
                break
            rows = []
            for rec in records:
                norm = normalize_record(rec)
                if norm:
                    rows.append(norm)
            upserted += upsert_rows(rows)
            pages += 1
            offset += len(records)
            print(
                f"[procurement_sync] page={pages} got={len(records)} "
                f"upserted_total={upserted} offset={offset} api_total={total}",
                flush=True,
            )
            if offset >= total or len(records) < PAGE_SIZE:
                break
        _set_sync_state(
            SOURCE,
            status="ok",
            records=upserted,
            cursor=f"dept={dept};offset={offset}",
        )
        return {
            "ok": True,
            "upserted": upserted,
            "pages": pages,
            "api_total": total,
            "dept": dept,
        }
    except Exception as e:
        _set_sync_state(
            SOURCE,
            status="error",
            records=upserted,
            error=str(e),
            cursor=f"dept={dept};offset={offset}",
        )
        raise
    finally:
        db.close_pool()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync BOAMP → Postgres mcp_procurement_*")
    p.add_argument(
        "--dept",
        default=os.environ.get("PROCUREMENT_DEPT", "58"),
        help="Code département (défaut 58). Vide = pas de filtre dept.",
    )
    p.add_argument("--since", default=os.environ.get("PROCUREMENT_SINCE") or None)
    p.add_argument(
        "--max-pages",
        type=int,
        default=int(os.environ.get("PROCUREMENT_MAX_PAGES") or "0") or None,
        help="Limiter le nombre de pages (debug). 0 = illimité.",
    )
    p.add_argument(
        "--all-france",
        action="store_true",
        help="Ne pas filtrer par département",
    )
    args = p.parse_args(argv)
    dept = None if args.all_france else (args.dept.strip() or None)
    max_pages = args.max_pages
    try:
        out = sync(dept=dept, since=args.since, max_pages=max_pages)
    except Exception as e:
        print(f"[procurement_sync] ERROR {e}", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
