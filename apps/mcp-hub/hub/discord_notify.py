# Notification Discord depuis le hub (hors VPN pentest).
from __future__ import annotations

import os

import requests

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def notify_scan(
    *,
    token_name: str,
    tool: str,
    target: str,
    job_id: int | None,
    ok: bool,
    detail: str = "",
) -> None:
    if not WEBHOOK:
        return
    status = "OK" if ok else "REFUS/ERR"
    content = (
        f"**MCP pentest** `{status}`\n"
        f"token=`{token_name}` tool=`{tool}` target=`{target}`"
    )
    if job_id is not None:
        content += f" job_id=`{job_id}`"
    if detail:
        content += f"\n{detail[:400]}"
    try:
        requests.post(WEBHOOK, json={"content": content[:1800]}, timeout=8)
    except Exception:
        pass
