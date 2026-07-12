#!/usr/bin/env python3
# Bot Telegram — interface RAG personnel (long polling)

import html
import logging
import os
from urllib.parse import quote

import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rag-telegram")

RAG_API_URL = os.environ.get("RAG_API_URL", "http://rag-api:8000").rstrip("/")
RAG_API_TOKEN = os.environ["RAG_API_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OBSIDIAN_VAULT_NAME = os.environ.get("OBSIDIAN_VAULT_NAME", "Workspace")
# Base HTTPS publique (ex. tunnel Cloudflare) — liens cliquables dans Telegram
OBSIDIAN_PUBLIC_BASE = os.environ.get("OBSIDIAN_PUBLIC_BASE", "").rstrip("/")
ALLOWED = {
    int(x.strip())
    for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if x.strip().isdigit()
}


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {RAG_API_TOKEN}"}


def _allowed(update: Update) -> bool:
    if not ALLOWED:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS vide — bot refusé pour tous")
        return False
    uid = update.effective_user.id if update.effective_user else None
    return uid in ALLOWED


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        await update.message.reply_text("Accès non autorisé.")
        return
    await update.message.reply_text(
        "RAG personnel KubeSecureBox\n\n"
        "/ask <question> — interroger tes notes Obsidian\n"
        "/stats — état de l'index\n"
        "/dossiers — liste des dossiers indexés\n"
        "/help — cette aide\n\n"
        "Tu peux aussi envoyer une question directement (sans /ask)."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    try:
        r = requests.get(f"{RAG_API_URL}/stats", headers=_auth_headers(), timeout=30)
        r.raise_for_status()
        d = r.json()
        sync = d.get("last_sync") or {}
        await update.message.reply_text(
            f"Documents : {d.get('nb_documents', 0)}\n"
            f"Dossiers : {d.get('nb_dossiers', 0)}\n"
            f"Chunks : {d.get('nb_chunks', 0)}\n"
            f"Dernière sync : {sync.get('finished_at', '—')}\n"
            f"Succès sync : {sync.get('success', '—')}"
        )
    except requests.RequestException as e:
        await update.message.reply_text(f"Erreur API : {e}")


async def cmd_dossiers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    try:
        r = requests.get(f"{RAG_API_URL}/dossiers", headers=_auth_headers(), timeout=30)
        r.raise_for_status()
        lines = [
            f"• {x['slug']} ({x['nb_documents']})"
            for x in r.json().get("dossiers", [])[:30]
        ]
        await update.message.reply_text(
            "Dossiers indexés :\n" + ("\n".join(lines) if lines else "(vide)")
        )
    except requests.RequestException as e:
        await update.message.reply_text(f"Erreur API : {e}")


def _obsidian_uri(chemin_vault: str) -> str:
    path = chemin_vault if chemin_vault.endswith(".md") else f"{chemin_vault}.md"
    return (
        f"obsidian://open?vault={quote(OBSIDIAN_VAULT_NAME, safe='')}"
        f"&file={quote(path, safe='')}"
    )


def _source_https_link(chemin_vault: str) -> str:
    return f"{OBSIDIAN_PUBLIC_BASE}/open?file={quote(chemin_vault, safe='')}"


def _format_ask_response(data: dict) -> tuple[str, str | None]:
    """Telegram n'accepte que http(s) dans les <a href> — pas obsidian://."""
    sources = data.get("sources") or []
    use_html = bool(OBSIDIAN_PUBLIC_BASE and sources)

    if use_html:
        parts = [html.escape(data.get("answer", ""))]
        parts.append("\n\n<b>Sources</b> :")
        for s in sources[:5]:
            nom = html.escape(s.get("nom") or "Note")
            chemin = s.get("chemin") or s.get("chemin_vault") or ""
            if chemin:
                href = html.escape(_source_https_link(chemin), quote=True)
                parts.append(f'• <a href="{href}">{nom}</a>')
            else:
                parts.append(f"• {nom}")
        parts.append(f"\n<i>({data.get('duration_ms', 0)} ms)</i>")
        return "\n".join(parts), ParseMode.HTML

    parts = [data.get("answer", "")]
    if sources:
        parts.append("\n\nSources :")
        for s in sources[:5]:
            nom = s.get("nom") or "Note"
            chemin = s.get("chemin") or s.get("chemin_vault") or ""
            parts.append(f"• {nom}")
            if chemin:
                parts.append(f"  {_obsidian_uri(chemin)}")
    parts.append(f"\n({data.get('duration_ms', 0)} ms)")
    return "\n".join(parts), None


def _ask_api(question: str) -> tuple[str, str | None]:
    r = requests.post(
        f"{RAG_API_URL}/ask",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        json={"question": question, "top": 8},
        timeout=120,
    )
    r.raise_for_status()
    return _format_ask_response(r.json())


async def _reply_long(message, text: str, parse_mode: str | None = None):
    """Telegram limite les messages à 4096 caractères."""
    kwargs = {"disable_web_page_preview": True}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if len(text) <= 4096:
        await message.reply_text(text, **kwargs)
        return
    for i in range(0, len(text), 4000):
        await message.reply_text(text[i : i + 4000], **kwargs)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    question = " ".join(context.args).strip()
    if len(question) < 3:
        await update.message.reply_text("Usage : /ask ta question")
        return
    await update.message.chat.send_action("typing")
    try:
        reply, parse_mode = await _run_ask(question)
        await _reply_long(update.message, reply, parse_mode=parse_mode)
    except requests.RequestException as e:
        await update.message.reply_text(f"Erreur API : {e}")
    except Exception as e:
        logger.exception("Échec /ask")
        await update.message.reply_text(f"Erreur : {e}")


async def _run_ask(question: str) -> tuple[str, str | None]:
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _ask_api, question)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update) or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    if len(text) < 3:
        return
    await update.message.chat.send_action("typing")
    try:
        reply, parse_mode = await _run_ask(text)
        await _reply_long(update.message, reply, parse_mode=parse_mode)
    except requests.RequestException as e:
        await update.message.reply_text(f"Erreur API : {e}")
    except Exception as e:
        logger.exception("Échec question texte")
        await update.message.reply_text(f"Erreur : {e}")


def main():
    if not TELEGRAM_BOT_TOKEN or not RAG_API_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN et RAG_API_TOKEN requis")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("dossiers", cmd_dossiers))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logger.info("Bot Telegram démarré (long polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
