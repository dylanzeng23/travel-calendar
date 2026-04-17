import json
import logging
import re
import uuid

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import db
from models import Config, Itinerary
from planner import TravelPlanner
from calendar_gen import generate_ics, save_ics

logger = logging.getLogger(__name__)

_config: Config | None = None
_planner: TravelPlanner | None = None


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting for clean Telegram display."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **bold**
    text = re.sub(r'__(.+?)__', r'\1', text)       # __underline__
    text = re.sub(r'\*(.+?)\*', r'\1', text)       # *italic*
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # # headings
    return text


def set_config(config: Config):
    global _config, _planner
    _config = config
    _planner = TravelPlanner(config)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm XiaoLan, your travel planning assistant.\n\n"
        "Just tell me about a trip you're planning and I'll help you build a detailed itinerary "
        "that syncs to your calendar.\n\n"
        "Commands:\n"
        "/new — Start a new trip\n"
        "/trips — List your trips\n"
        "/generate — Generate calendar for current trip\n"
        "/share <id> — Get calendar subscribe URL\n"
        "/delete <id> — Delete a trip"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a new trip planning session."""
    chat_id = str(update.effective_chat.id)
    trip_id = uuid.uuid4().hex[:8]
    db.create_trip(trip_id, chat_id)
    await update.message.reply_text(
        f"New trip started! (ID: {trip_id})\n\n"
        "Tell me about your trip — where are you going, when, any preferences?"
    )


async def cmd_trips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all trips for this chat."""
    chat_id = str(update.effective_chat.id)
    trips = db.get_trips_for_chat(chat_id)
    if not trips:
        await update.message.reply_text("No trips yet. Send me a message to start planning!")
        return

    lines = []
    for t in trips:
        status_icon = "📝" if t["status"] == "planning" else "✅"
        lines.append(f"{status_icon} [{t['id']}] {t['title']}")
    await update.message.reply_text("Your trips:\n\n" + "\n".join(lines))


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate .ics calendar for the active trip."""
    chat_id = str(update.effective_chat.id)

    # Check if a trip ID was provided
    if context.args:
        trip_id = context.args[0]
        trip = db.get_trip(trip_id)
        if not trip or trip["chat_id"] != chat_id:
            await update.message.reply_text(f"Trip {trip_id} not found.")
            return
    else:
        trip = db.get_active_trip(chat_id)
        if not trip:
            await update.message.reply_text("No active trip. Use /new to start one.")
            return

    trip_id = trip["id"]
    await update.message.reply_text("Generating your calendar... this may take a moment.")
    await update.effective_chat.send_action("typing")

    # Get conversation and finalize
    conversation = db.get_conversation(trip_id)
    if not conversation:
        await update.message.reply_text("No conversation yet — tell me about your trip first!")
        return

    itinerary_data = _planner.finalize(conversation)
    if not itinerary_data:
        await update.message.reply_text("Failed to generate itinerary. Try chatting more to clarify your plans.")
        return

    # Save itinerary and generate .ics
    itinerary = Itinerary.from_dict(itinerary_data)
    ics_bytes = generate_ics(itinerary, trip_id)
    save_ics(ics_bytes, _config.data_dir, trip_id)

    db.update_trip(trip_id, title=itinerary.title, itinerary_json=json.dumps(itinerary_data), status="finalized")

    url = f"{_config.server_base_url}/trips/{trip_id}/calendar.ics"
    webcal_url = url.replace("http://", "webcal://").replace("https://", "webcal://")

    await update.message.reply_text(
        f"Calendar generated!\n\n"
        f"Title: {itinerary.title}\n"
        f"Events: {sum(len(d.events) for d in itinerary.days)}\n"
        f"Days: {len(itinerary.days)}\n\n"
        f"Subscribe URL:\n{webcal_url}\n\n"
        f"Direct download:\n{url}\n\n"
        f"Open the webcal link on your phone to add to Apple/Google Calendar. "
        f"Share it with travel companions — they'll see updates too.\n\n"
        f"Use /generate {trip_id} to regenerate after changes."
    )


async def cmd_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get the subscribe URL for a trip."""
    if not context.args:
        await update.message.reply_text("Usage: /share <trip_id>")
        return

    trip_id = context.args[0]
    trip = db.get_trip(trip_id)
    if not trip or trip["chat_id"] != str(update.effective_chat.id):
        await update.message.reply_text(f"Trip {trip_id} not found.")
        return

    if trip["status"] != "finalized":
        await update.message.reply_text("This trip hasn't been generated yet. Use /generate first.")
        return

    url = f"{_config.server_base_url}/trips/{trip_id}/calendar.ics"
    webcal_url = url.replace("http://", "webcal://").replace("https://", "webcal://")
    await update.message.reply_text(
        f"📅 {trip['title']}\n\n"
        f"Subscribe: {webcal_url}\n"
        f"Download: {url}"
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a trip."""
    if not context.args:
        await update.message.reply_text("Usage: /delete <trip_id>")
        return

    trip_id = context.args[0]
    trip = db.get_trip(trip_id)
    if not trip or trip["chat_id"] != str(update.effective_chat.id):
        await update.message.reply_text(f"Trip {trip_id} not found.")
        return

    db.delete_trip(trip_id)
    await update.message.reply_text(f"Trip {trip_id} ({trip['title']}) deleted.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language messages — route to active trip's conversation."""
    chat_id = str(update.effective_chat.id)
    user_text = update.message.text

    if not user_text:
        return

    # Find or create active trip
    trip = db.get_active_trip(chat_id)
    if not trip:
        # Auto-create a new trip
        trip_id = uuid.uuid4().hex[:8]
        db.create_trip(trip_id, chat_id)
        trip = db.get_trip(trip_id)
        logger.info(f"Auto-created trip {trip_id} for chat {chat_id}")

    trip_id = trip["id"]

    # Check if user wants to generate
    generate_triggers = ["generate", "make the calendar", "生成", "生成日历", "做日历", "looks good", "就这样"]
    if any(trigger in user_text.lower() for trigger in generate_triggers):
        # Delegate to generate command
        context.args = [trip_id]
        await cmd_generate(update, context)
        return

    # Save user message and get Claude's response
    db.add_message(trip_id, "user", user_text)
    conversation = db.get_conversation(trip_id)

    await update.effective_chat.send_action("typing")

    try:
        response = _planner.chat(conversation, user_text)
    except Exception as e:
        logger.error(f"Planner error: {e}")
        await update.message.reply_text(f"Sorry, something went wrong: {e}")
        return

    # Save assistant response
    db.add_message(trip_id, "assistant", response)

    # Split long messages (Telegram 4096 char limit)
    response = _strip_markdown(response)
    for i in range(0, len(response), 4000):
        chunk = response[i : i + 4000]
        await update.message.reply_text(chunk)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages — download and send to Claude for trip planning context."""
    chat_id = str(update.effective_chat.id)
    photo = update.message.photo[-1]  # highest resolution
    caption = update.message.caption or ""

    trip = db.get_active_trip(chat_id)
    if not trip:
        trip_id = uuid.uuid4().hex[:8]
        db.create_trip(trip_id, chat_id)
        trip = db.get_trip(trip_id)

    trip_id = trip["id"]

    await update.effective_chat.send_action("typing")

    try:
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        # Store placeholder in conversation
        db.add_message(trip_id, "user", f"[photo]{f': {caption}' if caption else ''}")

        conversation = db.get_conversation(trip_id)
        response = _planner.chat_with_image(conversation, bytes(image_bytes), "image/jpeg", caption)

        db.add_message(trip_id, "assistant", response)

        response = _strip_markdown(response)
        for i in range(0, len(response), 4000):
            await update.message.reply_text(response[i : i + 4000])
    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await update.message.reply_text(f"Sorry, I couldn't process that image: {e}")


def build_bot_app(config: Config) -> Application:
    """Build and return the Telegram bot application."""
    set_config(config)

    app = Application.builder().token(config.bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("trips", cmd_trips))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("share", cmd_share))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    return app
