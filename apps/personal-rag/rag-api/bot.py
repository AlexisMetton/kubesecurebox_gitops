#!/usr/bin/env python3
# Bot Telegram — interface RAG personnel (long polling)

import html
import io
import logging
import os
import time
import uuid
from urllib.parse import quote

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rag-telegram")

RAG_API_URL = os.environ.get("RAG_API_URL", "http://rag-api:8000").rstrip("/")
RAG_API_TOKEN = os.environ["RAG_API_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")
OBSIDIAN_VAULT_NAME = os.environ.get("OBSIDIAN_VAULT_NAME", "Workspace")
# Base HTTPS publique (ex. tunnel Cloudflare) — liens cliquables dans Telegram
OBSIDIAN_PUBLIC_BASE = os.environ.get("OBSIDIAN_PUBLIC_BASE", "").rstrip("/")
TELEGRAM_STREAM = os.environ.get("TELEGRAM_STREAM", "1").strip() not in ("0", "false", "no")
ALLOWED = {
    int(x.strip())
    for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if x.strip().isdigit()
}

_SOURCE_CACHE: dict[str, dict] = {}

async def _safe_edit(msg, text: str, **kwargs):
    """Évite l'erreur Telegram 'Message is not modified' (edits identiques)."""
    try:
        current = getattr(msg, "text", None)
        if current == text and not kwargs:
            return
        await msg.edit_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


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
        "Tu peux aussi envoyer une question directement (sans /ask)\n"
        "ou un message vocal 🎤."
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
    if chemin_vault.startswith("http://") or chemin_vault.startswith("https://"):
        return chemin_vault
    return f"{OBSIDIAN_PUBLIC_BASE}/open?file={quote(chemin_vault, safe='')}"


def _format_ask_response(data: dict) -> tuple[str, str | None]:
    """Liens courts : titre cliquable → HTTPS /open → redirect obsidian://."""
    sources = data.get("sources") or []
    if OBSIDIAN_PUBLIC_BASE and sources:
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
            parts.append(f"• {s.get('nom') or 'Note'}")
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


def _sse_iter(question: str):
    """Itère sur les events SSE renvoyés par /ask_sse."""
    r = requests.post(
        f"{RAG_API_URL}/ask_sse",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        json={"question": question, "top": 8},
        stream=True,
        timeout=300,
    )
    r.raise_for_status()
    event = None
    data_lines: list[str] = []
    for raw in r.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\n")
        if not line:
            if event and data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
            continue


def _keyboard_for_sources(cache_id: str, sources: list[dict]) -> InlineKeyboardMarkup | None:
    if not sources:
        return None
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, s in enumerate(sources[:3], start=1):
        chemin = s.get("chemin") or s.get("chemin_vault") or ""
        if chemin:
            row.append(InlineKeyboardButton(f"Ouvrir {i}", url=_source_https_link(chemin)))
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Sources", callback_data=f"src:{cache_id}")])
    return InlineKeyboardMarkup(buttons)


async def _stream_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str):
    """Envoie/édite un message pour simuler un streaming token-par-token."""
    msg = await update.message.reply_text("🔎 Recherche…")
    last_edit = 0.0
    buffer = ""
    final_data: dict | None = None

    try:
        for ev, payload in _sse_iter(question):
            if ev == "status":
                try:
                    import json

                    d = json.loads(payload)
                except Exception:
                    d = {}
                label = d.get("label") or "…"
                await _safe_edit(msg, label)
                continue

            if ev == "delta":
                try:
                    import json

                    d = json.loads(payload)
                except Exception:
                    d = {}
                buffer += d.get("text") or ""
                now = time.monotonic()
                if now - last_edit < 0.8:
                    continue
                last_edit = now
                # Telegram limite; on affiche une fenêtre de fin de message en live
                live = buffer[-3500:] if len(buffer) > 3500 else buffer
                await _safe_edit(msg, live or "…")
                continue

            if ev == "done":
                try:
                    import json

                    final_data = json.loads(payload)
                except Exception:
                    final_data = {"answer": buffer, "sources": [], "duration_ms": 0}
                break

            if ev == "error":
                await _safe_edit(msg, "Erreur : service IA indisponible.")
                return

        if not final_data:
            final_data = {"answer": buffer, "sources": [], "duration_ms": 0}

        cache_id = uuid.uuid4().hex[:10]
        _SOURCE_CACHE[cache_id] = {
            "ts": time.time(),
            "sources": final_data.get("sources") or [],
        }

        reply, parse_mode = _format_ask_response(final_data)
        kb = _keyboard_for_sources(cache_id, final_data.get("sources") or [])
        kwargs = {"disable_web_page_preview": True}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if kb:
            kwargs["reply_markup"] = kb
        # Edit final (si > 4096, fallback en messages multiples)
        if len(reply) <= 4096:
            await _safe_edit(msg, reply, **kwargs)
        else:
            await _safe_edit(msg, "Réponse trop longue, je l’envoie en plusieurs messages…")
            await _reply_long(update.message, reply, parse_mode=parse_mode)
    except requests.RequestException as e:
        await _safe_edit(msg, f"Erreur API : {e}")
    except Exception as e:
        logger.exception("Échec streaming")
        await _safe_edit(msg, f"Erreur : {e}")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if not data.startswith("src:"):
        return
    cache_id = data.split(":", 1)[1]
    entry = _SOURCE_CACHE.get(cache_id)
    if not entry:
        await query.message.reply_text("Sources expirées.")
        return
    sources = entry.get("sources") or []
    if not sources:
        await query.message.reply_text("Aucune source.")
        return
    lines = ["Sources :"]
    for s in sources[:15]:
        nom = s.get("nom") or "Document"
        chemin = s.get("chemin") or s.get("chemin_vault") or ""
        lines.append(f"- {nom} — {chemin}")
    await query.message.reply_text("\n".join(lines), disable_web_page_preview=True)


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
    try:
        if TELEGRAM_STREAM:
            await _stream_answer(update, context, question)
        else:
            await update.message.chat.send_action("typing")
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


def _transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY manquante pour la transcription vocale")
    r = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        files={"file": (filename, io.BytesIO(audio_bytes), "application/octet-stream")},
        data={"model": WHISPER_MODEL, "language": "fr"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json().get("text", "").strip()


async def _run_transcribe(audio_bytes: bytes, filename: str) -> str:
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _transcribe_audio, audio_bytes, filename)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update) or not update.message:
        return
    voice = update.message.voice
    audio = update.message.audio
    if not voice and not audio:
        return

    media = voice or audio
    filename = "voice.ogg" if voice else (audio.file_name or "audio.mp3")

    await update.message.chat.send_action("typing")
    try:
        tg_file = await context.bot.get_file(media.file_id)
        bio = io.BytesIO()
        await tg_file.download_to_memory(out=bio)
        audio_bytes = bio.getvalue()
        if len(audio_bytes) < 100:
            await update.message.reply_text("Message vocal trop court.")
            return

        text = await _run_transcribe(audio_bytes, filename)
        if len(text.strip()) < 3:
            await update.message.reply_text("Je n'ai pas compris l'audio.")
            return

        await update.message.reply_text(f"🎤 {text}")
        if TELEGRAM_STREAM:
            await _stream_answer(update, context, text)
        else:
            reply, parse_mode = await _run_ask(text)
            await _reply_long(update.message, reply, parse_mode=parse_mode)
    except requests.RequestException as e:
        await update.message.reply_text(f"Erreur transcription/API : {e}")
    except Exception as e:
        logger.exception("Échec message vocal")
        await update.message.reply_text(f"Erreur : {e}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update) or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    if len(text) < 3:
        return
    try:
        if TELEGRAM_STREAM:
            await _stream_answer(update, context, text)
        else:
            await update.message.chat.send_action("typing")
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
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logger.info("Bot Telegram démarré (long polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
