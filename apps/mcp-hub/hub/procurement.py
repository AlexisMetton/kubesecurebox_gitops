# Requêtes marchés publics (index local mcp_procurement_*)
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import db
import psycopg2.extras

DISCLAIMER = (
    "Données publiques BOAMP (et assimilés) indexées localement. "
    "Couverture incomplète : sous-seuils souvent absents ; SIREN/geo variables. "
    "Faits publics uniquement — pas d'inférence de fraude ou de favoritisme."
)


def _as_opt_str(v: Any) -> str | None:
    """Normalise un argument MCP/HTTP (évite IndexError sur listes vides)."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        if not v:
            return None
        v = v[0]
    s = str(v).strip()
    return s or None


def _as_int(v: Any, default: int, lo: int = 1, hi: int = 50) -> int:
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(n, hi))


def _conn():
    assert db.pool is not None
    return db.pool.getconn()


def _put(conn) -> None:
    assert db.pool is not None
    db.pool.putconn(conn)


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _row_to_dict(row: dict) -> dict:
    out = {k: _jsonable(v) for k, v in dict(row).items() if k != "raw_json"}
    out["source_url"] = out.get("source_url") or ""
    return out


def _wrap(items: list[dict], **meta) -> dict:
    return {
        "disclaimer": DISCLAIMER,
        "count": len(items),
        "items": items,
        **meta,
    }


def search(
    *,
    dept: str | None = None,
    q: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = 20,
) -> dict:
    dept = _as_opt_str(dept)
    q = _as_opt_str(q)
    published_from = _as_opt_str(published_from)
    published_to = _as_opt_str(published_to)
    limit = _as_int(limit, 20)
    clauses: list[str] = []
    params: list[Any] = []
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept)
    if q:
        clauses.append(
            "(title ILIKE %s OR description ILIKE %s OR buyer_name ILIKE %s "
            "OR winner_name ILIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])
    if published_from:
        clauses.append("published_at >= %s")
        params.append(published_from)
    if published_to:
        clauses.append("published_at <= %s")
        params.append(published_to)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT id, source, external_id, notice_type, title, description,
               published_at, buyer_name, buyer_siren, buyer_city, buyer_dept,
               winner_name, winner_siren, amount_ht, currency, source_url
        FROM mcp_procurement_notices
        {where}
        ORDER BY published_at DESC NULLS LAST, id DESC
        LIMIT %s
    """
    params.append(limit)
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return _wrap(rows, filters={
            "dept": dept,
            "q": q,
            "published_from": published_from,
            "published_to": published_to,
        })
    finally:
        _put(conn)


def by_winner(
    *,
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = 30,
) -> dict:
    siren = _as_opt_str(siren)
    name = _as_opt_str(name)
    dept = _as_opt_str(dept)
    limit = _as_int(limit, 30)
    if not siren and not name:
        return {"error": "siren ou name requis", "disclaimer": DISCLAIMER}
    clauses: list[str] = []
    params: list[Any] = []
    if siren:
        clauses.append("winner_siren = %s")
        params.append("".join(c for c in siren if c.isdigit())[:9])
    if name:
        clauses.append("winner_name ILIKE %s")
        params.append(f"%{name}%")
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept)
    where = " WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT id, source, external_id, notice_type, title, published_at,
               buyer_name, buyer_siren, buyer_city, buyer_dept,
               winner_name, winner_siren, amount_ht, currency, source_url
        FROM mcp_procurement_notices
        {where}
        ORDER BY published_at DESC NULLS LAST
        LIMIT %s
    """
    params.append(limit)
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return _wrap(rows)
    finally:
        _put(conn)


def by_buyer(
    *,
    siren: str | None = None,
    name: str | None = None,
    dept: str | None = None,
    limit: int = 30,
) -> dict:
    siren = _as_opt_str(siren)
    name = _as_opt_str(name)
    dept = _as_opt_str(dept)
    limit = _as_int(limit, 30)
    if not siren and not name and not dept:
        return {
            "error": "siren, name ou dept requis",
            "disclaimer": DISCLAIMER,
        }
    clauses: list[str] = []
    params: list[Any] = []
    if siren:
        clauses.append("buyer_siren = %s")
        params.append("".join(c for c in siren if c.isdigit())[:9])
    if name:
        clauses.append("buyer_name ILIKE %s")
        params.append(f"%{name}%")
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept)
    where = " WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT id, source, external_id, notice_type, title, published_at,
               buyer_name, buyer_siren, buyer_city, buyer_dept,
               winner_name, winner_siren, amount_ht, currency, source_url
        FROM mcp_procurement_notices
        {where}
        ORDER BY published_at DESC NULLS LAST
        LIMIT %s
    """
    params.append(limit)
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return _wrap(rows)
    finally:
        _put(conn)


def get_notice(notice_id: int) -> dict:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, source, external_id, notice_type, title, description,
                       published_at, buyer_name, buyer_siren, buyer_city, buyer_dept,
                       winner_name, winner_siren, amount_ht, currency, source_url,
                       raw_json, updated_at
                FROM mcp_procurement_notices
                WHERE id = %s
                """,
                (int(notice_id),),
            )
            row = cur.fetchone()
        if not row:
            return {"error": "avis introuvable", "disclaimer": DISCLAIMER}
        out = _row_to_dict(row)
        raw = row.get("raw_json")
        if raw is not None:
            out["raw"] = raw
        out["disclaimer"] = DISCLAIMER
        return out
    finally:
        _put(conn)


def top_winners(
    *,
    dept: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit: int = 20,
) -> dict:
    dept = _as_opt_str(dept)
    published_from = _as_opt_str(published_from)
    published_to = _as_opt_str(published_to)
    limit = _as_int(limit, 20)
    # Exclure artefact buyer==winner ; privilégier attributions / résultats
    clauses = [
        "winner_name <> ''",
        "buyer_name <> ''",
        "lower(trim(winner_name)) <> lower(trim(buyer_name))",
        "("
        "notice_type ILIKE '%ATTRIBUTION%' OR notice_type ILIKE '%RESULTAT%' "
        "OR winner_siren <> ''"
        ")",
        "(amount_ht IS NULL OR (amount_ht >= 100 AND amount_ht <= 500000000))",
    ]
    params: list[Any] = []
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept)
    if published_from:
        clauses.append("published_at >= %s")
        params.append(published_from)
    if published_to:
        clauses.append("published_at <= %s")
        params.append(published_to)
    where = " WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT COALESCE(NULLIF(winner_siren, ''), winner_name) AS winner_key,
               MAX(winner_name) AS winner_name,
               MAX(winner_siren) AS winner_siren,
               COUNT(*)::int AS notice_count,
               SUM(amount_ht) AS amount_ht_sum
        FROM mcp_procurement_notices
        {where}
        GROUP BY COALESCE(NULLIF(winner_siren, ''), winner_name)
        ORDER BY amount_ht_sum DESC NULLS LAST, notice_count DESC
        LIMIT %s
    """
    params.append(limit)
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            raw_rows = cur.fetchall() or []
            rows = []
            for r in raw_rows:
                rows.append({
                    "winner_name": r.get("winner_name") or "",
                    "winner_siren": r.get("winner_siren") or "",
                    "notice_count": int(r.get("notice_count") or 0),
                    "amount_ht_sum": _jsonable(r.get("amount_ht_sum")),
                })
        return _wrap(
            rows,
            filters={
                "dept": dept,
                "published_from": published_from,
                "published_to": published_to,
            },
            note=(
                "Agrégat sur avis avec attributaire ≠ acheteur "
                "(ATTRIBUTION/RESULTAT ou SIREN attributaire). "
                "Montants absurdes exclus."
            ),
        )
    finally:
        _put(conn)


def stats() -> dict:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*)::int AS total,
                       COUNT(DISTINCT buyer_dept) FILTER (WHERE buyer_dept <> '')::int
                           AS depts,
                       MIN(published_at) AS min_published,
                       MAX(published_at) AS max_published
                FROM mcp_procurement_notices
                """
            )
            row = dict(cur.fetchone() or {})
            cur.execute(
                """
                SELECT source, status, last_success_at, records_upserted, last_error
                FROM mcp_procurement_sync_state
                ORDER BY source
                """
            )
            sync = [dict(r) for r in cur.fetchall()]
        for s in sync:
            for k, v in list(s.items()):
                s[k] = _jsonable(v)
        return {
            "disclaimer": DISCLAIMER,
            "notices": {
                "total": row.get("total") or 0,
                "depts": row.get("depts") or 0,
                "min_published": _jsonable(row.get("min_published")),
                "max_published": _jsonable(row.get("max_published")),
            },
            "sync": sync,
        }
    finally:
        _put(conn)
