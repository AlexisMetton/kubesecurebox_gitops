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
    limit = max(1, min(int(limit), 50))
    clauses: list[str] = []
    params: list[Any] = []
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept.strip())
    if q and q.strip():
        clauses.append(
            "(title ILIKE %s OR description ILIKE %s OR buyer_name ILIKE %s "
            "OR winner_name ILIKE %s)"
        )
        like = f"%{q.strip()}%"
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
    limit = max(1, min(int(limit), 50))
    if not (siren and siren.strip()) and not (name and name.strip()):
        return {"error": "siren ou name requis", "disclaimer": DISCLAIMER}
    clauses: list[str] = []
    params: list[Any] = []
    if siren and siren.strip():
        clauses.append("winner_siren = %s")
        params.append("".join(c for c in siren if c.isdigit())[:9])
    if name and name.strip():
        clauses.append("winner_name ILIKE %s")
        params.append(f"%{name.strip()}%")
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept.strip())
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
    limit = max(1, min(int(limit), 50))
    if not (siren and siren.strip()) and not (name and name.strip()) and not dept:
        return {
            "error": "siren, name ou dept requis",
            "disclaimer": DISCLAIMER,
        }
    clauses: list[str] = []
    params: list[Any] = []
    if siren and siren.strip():
        clauses.append("buyer_siren = %s")
        params.append("".join(c for c in siren if c.isdigit())[:9])
    if name and name.strip():
        clauses.append("buyer_name ILIKE %s")
        params.append(f"%{name.strip()}%")
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept.strip())
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
    limit = max(1, min(int(limit), 50))
    clauses = ["winner_name <> ''"]
    params: list[Any] = []
    if dept:
        clauses.append("buyer_dept = %s")
        params.append(dept.strip())
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
        ORDER BY notice_count DESC, amount_ht_sum DESC NULLS LAST
        LIMIT %s
    """
    params.append(limit)
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "winner_name": r["winner_name"],
                    "winner_siren": r["winner_siren"] or "",
                    "notice_count": r["notice_count"],
                    "amount_ht_sum": _jsonable(r["amount_ht_sum"]),
                })
        return _wrap(
            rows,
            filters={
                "dept": dept,
                "published_from": published_from,
                "published_to": published_to,
            },
            note="Agrégat sur avis indexés ayant un attributaire renseigné.",
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
