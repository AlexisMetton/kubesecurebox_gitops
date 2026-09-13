# Accès Postgres (tables mcp_*) — DB partagée personal-rag
import os
import secrets
from datetime import datetime, timezone

import bcrypt
import psycopg2
import psycopg2.extras
import psycopg2.pool

PG_DSN = (
    f"host={os.environ.get('PG_HOST', 'postgres.personal-rag.svc.cluster.local')} "
    f"port={os.environ.get('PG_PORT', '5432')} "
    f"dbname={os.environ.get('PG_DB', 'rag')} "
    f"user={os.environ.get('PG_USER', 'rag_user')} "
    f"password={os.environ['PG_PASSWORD']}"
)

pool: psycopg2.pool.ThreadedConnectionPool | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mcp_tokens (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    token_hash    TEXT NOT NULL,
    scopes        TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mcp_tokens_active
    ON mcp_tokens (id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS mcp_audit_events (
    id            BIGSERIAL PRIMARY KEY,
    token_id      BIGINT REFERENCES mcp_tokens(id) ON DELETE SET NULL,
    token_name    TEXT NOT NULL DEFAULT '',
    tool_name     TEXT NOT NULL,
    ok            BOOLEAN NOT NULL DEFAULT true,
    summary       TEXT NOT NULL DEFAULT '',
    latency_ms    INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_created
    ON mcp_audit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_token
    ON mcp_audit_events (token_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mcp_pentest_allowlist (
    id          BIGSERIAL PRIMARY KEY,
    pattern     TEXT NOT NULL UNIQUE,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mcp_scan_jobs (
    id            BIGSERIAL PRIMARY KEY,
    token_id      BIGINT REFERENCES mcp_tokens(id) ON DELETE SET NULL,
    token_name    TEXT NOT NULL DEFAULT '',
    tool_name     TEXT NOT NULL,
    target        TEXT NOT NULL,
    k8s_job_name  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    summary       TEXT NOT NULL DEFAULT '',
    log_excerpt   TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mcp_scan_jobs_created
    ON mcp_scan_jobs (created_at DESC);
"""

ALLOWED_SCOPES = {
    "admin",
    "rag:read",
    "skills:read",
    "activity:read",
    "pentest:lab",
}


def init_pool() -> None:
    global pool
    pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=6, dsn=PG_DSN)


def close_pool() -> None:
    global pool
    if pool is not None:
        pool.closeall()
        pool = None


def ensure_schema() -> None:
    assert pool is not None
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    finally:
        pool.putconn(conn)


def _hash_token(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_hash(raw: str, token_hash: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), token_hash.encode("utf-8"))
    except ValueError:
        return False


def bootstrap_admin_if_needed(bootstrap_token: str) -> None:
    if not bootstrap_token.strip():
        return
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM mcp_tokens WHERE revoked_at IS NULL")
            if cur.fetchone()[0] > 0:
                return
            cur.execute(
                """INSERT INTO mcp_tokens (name, token_hash, scopes)
                   VALUES (%s, %s, %s)""",
                (
                    "bootstrap-admin",
                    _hash_token(bootstrap_token.strip()),
                    ["admin", "rag:read", "skills:read", "activity:read"],
                ),
            )
        conn.commit()
    finally:
        pool.putconn(conn)


def create_token(name: str, scopes: list[str]) -> tuple[dict, str]:
    raw = secrets.token_urlsafe(32)
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO mcp_tokens (name, token_hash, scopes)
                   VALUES (%s, %s, %s)
                   RETURNING id, name, scopes, created_at, revoked_at, last_used_at""",
                (name, _hash_token(raw), scopes),
            )
            row = dict(cur.fetchone())
        conn.commit()
        row["created_at"] = row["created_at"].isoformat()
        return row, raw
    except psycopg2.Error:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def list_tokens() -> list[dict]:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, name, scopes, created_at, revoked_at, last_used_at
                   FROM mcp_tokens ORDER BY id"""
            )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                for k in ("created_at", "revoked_at", "last_used_at"):
                    if d.get(k) is not None:
                        d[k] = d[k].isoformat()
                rows.append(d)
            return rows
    finally:
        pool.putconn(conn)


def revoke_token(token_id: int) -> bool:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mcp_tokens SET revoked_at = now()
                   WHERE id = %s AND revoked_at IS NULL""",
                (token_id,),
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        pool.putconn(conn)


def update_token_scopes(token_id: int, scopes: list[str]) -> dict | None:
    """Change les scopes sans régénérer le secret."""
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """UPDATE mcp_tokens SET scopes = %s
                   WHERE id = %s AND revoked_at IS NULL
                   RETURNING id, name, scopes, created_at, revoked_at, last_used_at""",
                (scopes, token_id),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return None
            d = dict(row)
            for k in ("created_at", "revoked_at", "last_used_at"):
                if d.get(k) is not None:
                    d[k] = d[k].isoformat()
        conn.commit()
        return d
    except psycopg2.Error:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def authenticate(raw_token: str) -> dict | None:
    if not raw_token:
        return None
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, name, token_hash, scopes
                   FROM mcp_tokens WHERE revoked_at IS NULL"""
            )
            for row in cur.fetchall():
                if _verify_hash(raw_token, row["token_hash"]):
                    cur.execute(
                        "UPDATE mcp_tokens SET last_used_at = %s WHERE id = %s",
                        (datetime.now(timezone.utc), row["id"]),
                    )
                    conn.commit()
                    return {
                        "id": row["id"],
                        "name": row["name"],
                        "scopes": list(row["scopes"] or []),
                    }
        return None
    finally:
        pool.putconn(conn)


def log_event(
    token_id: int | None,
    token_name: str,
    tool_name: str,
    ok: bool,
    summary: str,
    latency_ms: int | None = None,
) -> None:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mcp_audit_events
                   (token_id, token_name, tool_name, ok, summary, latency_ms)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    token_id,
                    token_name[:200],
                    tool_name[:120],
                    ok,
                    (summary or "")[:800],
                    latency_ms,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def list_activity(limit: int = 50, token_id: int | None = None) -> list[dict]:
    assert pool is not None
    limit = max(1, min(limit, 200))
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if token_id is not None:
                cur.execute(
                    """SELECT id, token_id, token_name, tool_name, ok, summary,
                              latency_ms, created_at
                       FROM mcp_audit_events
                       WHERE token_id = %s
                       ORDER BY id DESC LIMIT %s""",
                    (token_id, limit),
                )
            else:
                cur.execute(
                    """SELECT id, token_id, token_name, tool_name, ok, summary,
                              latency_ms, created_at
                       FROM mcp_audit_events
                       ORDER BY id DESC LIMIT %s""",
                    (limit,),
                )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("created_at") is not None:
                    d["created_at"] = d["created_at"].isoformat()
                rows.append(d)
            return rows
    finally:
        pool.putconn(conn)


def _row_ts(d: dict, *keys: str) -> dict:
    for k in keys:
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def list_allowlist() -> list[dict]:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, pattern, note, created_at
                   FROM mcp_pentest_allowlist ORDER BY pattern"""
            )
            return [_row_ts(dict(r), "created_at") for r in cur.fetchall()]
    finally:
        pool.putconn(conn)


def add_allowlist(pattern: str, note: str = "") -> dict:
    pattern = pattern.strip().lower()
    if not pattern:
        raise ValueError("pattern vide")
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO mcp_pentest_allowlist (pattern, note)
                   VALUES (%s, %s)
                   RETURNING id, pattern, note, created_at""",
                (pattern, (note or "")[:300]),
            )
            row = _row_ts(dict(cur.fetchone()), "created_at")
        conn.commit()
        return row
    except psycopg2.Error:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def delete_allowlist(entry_id: int) -> bool:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mcp_pentest_allowlist WHERE id = %s", (entry_id,)
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        pool.putconn(conn)


def create_scan_job(
    token_id: int | None,
    token_name: str,
    tool_name: str,
    target: str,
    k8s_job_name: str,
) -> dict:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO mcp_scan_jobs
                   (token_id, token_name, tool_name, target, k8s_job_name, status)
                   VALUES (%s, %s, %s, %s, %s, 'pending')
                   RETURNING id, token_id, token_name, tool_name, target,
                             k8s_job_name, status, summary, log_excerpt,
                             created_at, finished_at""",
                (
                    token_id,
                    token_name[:200],
                    tool_name[:80],
                    target[:500],
                    k8s_job_name[:120],
                ),
            )
            row = _row_ts(dict(cur.fetchone()), "created_at", "finished_at")
        conn.commit()
        return row
    finally:
        pool.putconn(conn)


def get_scan_job(job_id: int) -> dict | None:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, token_id, token_name, tool_name, target,
                          k8s_job_name, status, summary, log_excerpt,
                          created_at, finished_at
                   FROM mcp_scan_jobs WHERE id = %s""",
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_ts(dict(row), "created_at", "finished_at")
    finally:
        pool.putconn(conn)


def update_scan_job(
    job_id: int,
    *,
    status: str | None = None,
    summary: str | None = None,
    log_excerpt: str | None = None,
    finished: bool = False,
) -> dict | None:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM mcp_scan_jobs WHERE id = %s", (job_id,))
            if not cur.fetchone():
                return None
            sets = []
            params: list = []
            if status is not None:
                sets.append("status = %s")
                params.append(status[:40])
            if summary is not None:
                sets.append("summary = %s")
                params.append(summary[:800])
            if log_excerpt is not None:
                sets.append("log_excerpt = %s")
                params.append(log_excerpt[-12000:])
            if finished:
                sets.append("finished_at = now()")
            if not sets:
                return get_scan_job(job_id)
            params.append(job_id)
            cur.execute(
                f"""UPDATE mcp_scan_jobs SET {", ".join(sets)}
                   WHERE id = %s
                   RETURNING id, token_id, token_name, tool_name, target,
                             k8s_job_name, status, summary, log_excerpt,
                             created_at, finished_at""",
                params,
            )
            out = _row_ts(dict(cur.fetchone()), "created_at", "finished_at")
        conn.commit()
        return out
    except psycopg2.Error:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def count_active_scans() -> int:
    assert pool is not None
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM mcp_scan_jobs
                   WHERE status IN ('pending', 'running')"""
            )
            return int(cur.fetchone()[0])
    finally:
        pool.putconn(conn)
