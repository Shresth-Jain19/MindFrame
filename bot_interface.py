import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from supabase import acreate_client
from cryptography.fernet import Fernet

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

cipher = Fernet(os.getenv("MASTER_KEY").encode())


async def get_supabase():
    return await acreate_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


# --- HELPER: Check System Health ---
async def check_system_status():
    """Returns (is_online, eta_message)"""
    try:
        sb = await get_supabase()
        res = await sb.table("system_status").select("last_seen").eq("id", 1).execute()
        if not res.data:
            return False, "Unknown"

        last_seen_str = res.data[0]["last_seen"]
        last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))

        # If seen in last 30 seconds, it's Online
        is_online = (datetime.now(timezone.utc) - last_seen) < timedelta(seconds=30)
        return is_online
    except:
        return False


# --- COMMANDS ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 **MindFrame Ready.**\n\n"
        "• Send any Reel/Short to summarize.\n"
        "• Use /history to see past saves.\n"
        "• Use /show <id> to read a specific summary.",
        parse_mode="Markdown",
    )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists the last 5 summaries."""
    user = update.effective_user
    sb = await get_supabase()

    # Fetch last 5 'done' tasks
    res = (
        await sb.table("tasks")
        .select("id, content, created_at")
        .eq("user_id", user.id)
        .eq("status", "done")
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )

    if not res.data:
        await update.message.reply_text("No history found.")
        return

    msg = "**🗂 Recent History:**\n\n"
    for task in res.data:
        # Truncate URL for display
        short_content = (
            (task["content"][:30] + "..")
            if len(task["content"]) > 30
            else task["content"]
        )
        msg += f"🆔 `/show {task['id']}` : {short_content}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retrieves a specific summary by ID."""
    try:
        task_id = context.args[0]
        user = update.effective_user
        sb = await get_supabase()

        res = (
            await sb.table("tasks")
            .select("*")
            .eq("id", task_id)
            .eq("user_id", user.id)
            .execute()
        )

        if not res.data or not res.data[0].get("encrypted_summary"):
            await update.message.reply_text("❌ Summary not found or empty.")
            return

        encrypted = res.data[0]["encrypted_summary"]
        decrypted = cipher.decrypt(encrypted.encode()).decode()

        await update.message.reply_text(
            f"📄 **Recall (ID {task_id}):**\n\n{decrypted}", parse_mode="Markdown"
        )

    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/show <id>` (e.g., /show 12)")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    sb = await get_supabase()

    # --- NEW: GREETING CHECK (No AI, No Database Queue) ---
    greetings = ["hi", "hello", "hey", "how are you", "how are you?"]
    if text.lower().strip() in greetings:
        await update.message.reply_text(
            "👋 Hello! I am MindFrame.\nSend me an Instagram Reel or YouTube Short URL to analyze.",
            parse_mode="Markdown",
        )
        return  # Stop here, don't queue as a task
    # ------------------------------------------------------

    # 1. Check Status
    is_online = await check_system_status()

    if is_online:
        status_text = "✅ Connected to MindFrame AI Engine."
    else:
        status_text = "💤 Machine Offline. Task queued for later."

    msg = await update.message.reply_text(status_text, parse_mode="Markdown")

    # 2. Queue Task
    await sb.table("tasks").insert(
        {
            "user_id": user.id,
            "user_name": user.full_name,
            "content": text,
            "status": "pending",
        }
    ).execute()

    # 3. Update UI
    if is_online:
        await context.bot.edit_message_text(
            chat_id=user.id,
            message_id=msg.message_id,
            text="⏳ Extracting Intelligence...",
        )


# --- BACKGROUND DELIVERER ---
async def check_results(context: ContextTypes.DEFAULT_TYPE):
    sb = await get_supabase()
    # Get 'done' tasks that haven't been flagged as delivered (Simulated by checking loop)
    res = await sb.table("tasks").select("*").eq("status", "done").execute()

    for task in res.data:
        if task.get("encrypted_summary"):
            try:
                decrypted = cipher.decrypt(task["encrypted_summary"].encode()).decode()

                # If it was a long video rejection, show warning icon
                icon = "⚠️" if "Video too long" in decrypted else "🧠"

                await context.bot.send_message(
                    chat_id=task["user_id"],
                    text=f"{icon} **MindFrame Result:**\n\n{decrypted}",
                    parse_mode="Markdown",
                )

                # Update status to 'archived'
                await sb.table("tasks").update({"status": "archived"}).eq(
                    "id", task["id"]
                ).execute()

            except Exception as e:
                logging.error(f"Delivery failed: {e}")


# --- MAIN ---
async def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("show", show_summary))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    if app.job_queue:
        app.job_queue.run_repeating(check_results, interval=4, first=1)

    async with app:
        await app.start()
        await app.updater.start_polling()
        stop_signal = asyncio.Event()
        await stop_signal.wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
