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


async def get_user_task_number(user_id):
    """Get the next task number for a user (1-based, per user)."""
    try:
        sb = await get_supabase()
        # Count existing tasks for this user (excluding START_EVENT and GREETING_EVENT)
        res = (
            await sb.table("tasks")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .not_.in_("content", ["START_EVENT", "GREETING_EVENT"])
            .execute()
        )
        count = res.count if res.count is not None else 0
        return count + 1
    except:
        return 1


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sb = await get_supabase()

    # Record the START_EVENT so we know the user exists
    await sb.table("tasks").insert(
        {
            "user_id": user.id,
            "user_name": user.full_name,
            "content": "START_EVENT",
            "status": "done",
            "task_number": 0,  # Special number for events
        }
    ).execute()

    await update.message.reply_text(
        "🧠 Welcome to MindFrame.\n\n"
        "• Send any Reel/Short/Article to extract insights.\n"
        "• Use /history to see past saves.\n"
        "• Use /show <id> to read a specific summary.",
        parse_mode="Markdown",
    )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists the last 5 summaries with preview."""
    user = update.effective_user
    sb = await get_supabase()

    # Fetch last 5 'done' or 'archived' tasks that have summaries
    res = (
        await sb.table("tasks")
        .select("id, content, encrypted_summary, task_number, created_at")
        .eq("user_id", user.id)
        .in_("status", ["done", "archived"])
        .not_.in_("content", ["START_EVENT", "GREETING_EVENT"])
        .not_.is_("encrypted_summary", "null")
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )

    if not res.data:
        await update.message.reply_text("No history found.")
        return

    msg = "🗂 Recent History:\n\n"
    for task in res.data:
        task_num = task.get("task_number", task["id"])
        task_id = task["id"]

        # Try to get a preview from the summary
        preview = ""
        if task.get("encrypted_summary"):
            try:
                decrypted = cipher.decrypt(task["encrypted_summary"].encode()).decode()
                # Extract first line as preview
                first_line = decrypted.split("\n")[0].strip()
                if first_line:
                    preview = first_line[:50] + (".." if len(first_line) > 50 else "")
            except:
                pass

        # Fallback to content if no preview
        if not preview:
            preview = (
                (task["content"][:50] + "..")
                if len(task["content"]) > 50
                else task["content"]
            )

        msg += f"#{task_num} `/show {task_id}`\n{preview}\n\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retrieves a specific summary by ID or task number."""
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Usage: `/show <id>` (e.g., /show 12)")
            return

        task_identifier = context.args[0]
        user = update.effective_user
        sb = await get_supabase()

        # Try to find by ID first
        res = (
            await sb.table("tasks")
            .select("*")
            .eq("id", task_identifier)
            .eq("user_id", user.id)
            .execute()
        )

        # If not found by ID, try by task_number
        if not res.data or not res.data[0].get("encrypted_summary"):
            res = (
                await sb.table("tasks")
                .select("*")
                .eq("task_number", task_identifier)
                .eq("user_id", user.id)
                .execute()
            )

        if not res.data:
            await update.message.reply_text("❌ Summary not found.")
            return

        task = res.data[0]

        if not task.get("encrypted_summary"):
            await update.message.reply_text("❌ Summary not available yet or empty.")
            return

        try:
            encrypted = task["encrypted_summary"]
            decrypted = cipher.decrypt(encrypted.encode()).decode()

            task_num = task.get("task_number", task["id"])
            task_id = task["id"]
            content_preview = (
                (task["content"][:50] + "..")
                if len(task["content"]) > 50
                else task["content"]
            )

            await update.message.reply_text(
                f"📄 Summary #{task_num} (ID: {task_id})\n\n"
                f"Source: {content_preview}\n\n"
                f"{decrypted}",
                parse_mode="Markdown",
            )
        except Exception as e:
            logging.error(f"Error decrypting summary: {e}")
            await update.message.reply_text(
                "❌ Error retrieving summary. It may be corrupted."
            )

    except (IndexError, ValueError) as e:
        logging.error(f"Error in show_summary: {e}")
        await update.message.reply_text("⚠️ Usage: `/show <id>` (e.g., /show 12)")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    sb = await get_supabase()

    # --- GREETING LOGIC START ---
    greetings = ["hi", "hello", "hey", "how are you", "how are you?"]

    if text.lower().strip() in greetings:
        # Check history to see if this is the FIRST message or a recurring one
        # We count how many tasks exist for this user
        # Note: The /start command adds 1 event. So if count <= 1, it's new.
        res = (
            await sb.table("tasks")
            .select("id", count="exact")
            .eq("user_id", user.id)
            .execute()
        )
        task_count = res.count if res.count is not None else 0

        if task_count <= 1:
            # Case 1: First message after onboarding
            await update.message.reply_text(
                f"Hi {user.first_name}.", parse_mode="Markdown"
            )
        else:
            # Case 2: Recurring Greeting
            await update.message.reply_text(
                "👋 Hello! I am MindFrame.\nSend me an Instagram Reel or YouTube Short URL to analyze.",
                parse_mode="Markdown",
            )

        # Important: Save this interaction so the count increases for next time
        await sb.table("tasks").insert(
            {
                "user_id": user.id,
                "user_name": user.full_name,
                "content": "GREETING_EVENT",
                "status": "done",  # Mark done so processor ignores it
                "task_number": 0,  # Special number for events
            }
        ).execute()
        return
    # --- GREETING LOGIC END ---

    # 1. Check Status (For normal tasks)
    is_online = await check_system_status()

    if is_online:
        status_text = "✅ Connected to MindFrame. Processing will begin shortly."
    else:
        status_text = "💤 Machine Offline. Task queued for later."

    msg = await update.message.reply_text(status_text, parse_mode="Markdown")

    # 2. Get next task number for this user
    task_number = await get_user_task_number(user.id)

    # 3. Queue Task
    await sb.table("tasks").insert(
        {
            "user_id": user.id,
            "user_name": user.full_name,
            "content": text,
            "status": "pending",
            "task_number": task_number,
        }
    ).execute()

    # 4. Update UI
    if is_online:
        await context.bot.edit_message_text(
            chat_id=user.id,
            message_id=msg.message_id,
            text="⏳ Extracting Intelligence...",
        )


# --- BACKGROUND DELIVERER ---
async def check_results(context: ContextTypes.DEFAULT_TYPE):
    sb = await get_supabase()
    try:
        # Get 'done' tasks that haven't been flagged as delivered
        # Order by created_at to process oldest first
        res = (
            await sb.table("tasks")
            .select("*")
            .eq("status", "done")
            .order("created_at", desc=False)
            .limit(10)  # Process up to 10 at a time
            .execute()
        )

        for task in res.data:
            try:
                # Skip internal events like START or GREETING
                if task.get("content") in ["START_EVENT", "GREETING_EVENT"]:
                    # Just archive them without sending a message
                    await sb.table("tasks").update({"status": "archived"}).eq(
                        "id", task["id"]
                    ).execute()
                    continue

                if task.get("encrypted_summary"):
                    try:
                        decrypted = cipher.decrypt(
                            task["encrypted_summary"].encode()
                        ).decode()

                        # Determine icon based on content
                        icon = "🧠"
                        if (
                            "Video too long" in decrypted
                            or "too long" in decrypted.lower()
                        ):
                            icon = "⚠️"
                        elif "Error" in decrypted or "Failed" in decrypted:
                            icon = "❌"
                        elif "Download Failed" in decrypted:
                            icon = "❌"

                        # Send message to user
                        await context.bot.send_message(
                            chat_id=task["user_id"],
                            text=f"{icon} MindFrame Result:\n\n{decrypted}",
                            parse_mode="Markdown",
                        )

                        # Update status to 'archived' after successful delivery
                        await sb.table("tasks").update({"status": "archived"}).eq(
                            "id", task["id"]
                        ).execute()

                        logging.info(
                            f"✅ Delivered summary for task {task['id']} to user {task['user_id']}"
                        )

                    except Exception as e:
                        logging.error(f"Delivery failed for task {task['id']}: {e}")
                        # Don't archive on error, so it can be retried
                        # But add a small delay to avoid infinite retries
                        await asyncio.sleep(1)

                else:
                    # No summary yet, skip for now
                    continue

            except Exception as e:
                logging.error(f"Error processing task {task.get('id', 'unknown')}: {e}")
                continue

    except Exception as e:
        logging.error(f"Error in check_results: {e}")


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
