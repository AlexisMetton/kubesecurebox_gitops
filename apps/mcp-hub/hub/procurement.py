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
    """Agrège en Python (évite les pièges SQL/driver sur GROUP BY)."""
    import traceback

    try:
        dept = _as_opt_str(dept)
        published_from = _as_opt_str(published_from)
        published_to = _as_opt_str(published_to)
        limit = _as_int(limit, 20)

        clauses = [
            "winner_name <> ''",
            "buyer_name <> ''",
            "lower(btrim(winner_name)) <> lower(btrim(buyer_name))",
        ]
        params: list[Any] = []
        if dept:
            clauses.append("buyer_dept = %s")
            params.append(dept)
        if published_from:
            clauses.append("published_at >= %s::date")
            params.append(published_from)
        if published_to:
            clauses.append("published_at <= %s::date")
            params.append(published_to)

        where = " WHERE " + " AND ".join(clauses)
        sql = f"""
            SELECT winner_name, winner_siren, notice_type, amount_ht
            FROM mcp_procurement_notices
            {where}
            ORDER BY published_at DESC NULLS LAST
            LIMIT 5000
        """
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                raw = cur.fetchall() or []
        finally:
            _put(conn)

        # (winner_key) -> stats
        agg: dict[str, dict[str, Any]] = {}
        for row in raw:
            # row is tuple: winner_name, winner_siren, notice_type, amount_ht
            if not row or len(row) < 4:
                continue
            w_name = (row[0] or "").strip()
            w_siren = (row[1] or "").strip()
            ntype = (row[2] or "").upper()
            amount = row[3]

            if not w_name:
                continue
            # garder attributions / résultats, ou SIREN renseigné
            if (
                "ATTRIBUTION" not in ntype
                and "RESULTAT" not in ntype
                and not w_siren
            ):
                continue

            amt: float | None
            try:
                if amount is None:
                    amt = None
                else:
                    amt = float(amount)
                    if amt < 100 or amt > 500_000_000:
                        amt = None  # montant aberrant : compte l'avis sans somme
            except (TypeError, ValueError):
                amt = None

            key = w_siren or w_name.lower()
            slot = agg.get(key)
            if slot is None:
                slot = {
                    "winner_name": w_name,
                    "winner_siren": w_siren,
                    "notice_count": 0,
                    "amount_ht_sum": 0.0,
                    "has_amount": False,
                }
                agg[key] = slot
            slot["notice_count"] += 1
            if amt is not None:
                slot["amount_ht_sum"] += amt
                slot["has_amount"] = True

        items = []
        for slot in agg.values():
            items.append({
                "winner_name": slot["winner_name"],
                "winner_siren": slot["winner_siren"],
                "notice_count": slot["notice_count"],
                "amount_ht_sum": (
                    round(slot["amount_ht_sum"], 2) if slot["has_amount"] else None
                ),
            })
        items.sort(
            key=lambda x: (
                x["amount_ht_sum"] is not None,
                x["amount_ht_sum"] or 0,
                x["notice_count"],
            ),
            reverse=True,
        )
        items = items[:limit]
        return _wrap(
            items,
            filters={
                "dept": dept,
                "published_from": published_from,
                "published_to": published_to,
            },
            note=(
                "Agrégat attributaire ≠ acheteur ; "
                "avis ATTRIBUTION/RESULTAT (ou SIREN) ; montants absurdes exclus de la somme."
            ),
        )
    except Exception as e:
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "traceback": traceback.format_exc()[-2000:],
            "disclaimer": DISCLAIMER,
        }


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
