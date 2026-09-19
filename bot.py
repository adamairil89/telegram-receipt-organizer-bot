import datetime
import logging
import os
import http.server
import socketserver
import threading
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Load variables from the .env file into the system environment
load_dotenv()

# Read the values securely
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))

# --- Minimal Health-Check Server for Render ---
def run_health_server():
    port = int(os.getenv("PORT", 10000))

    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is alive!")

    with socketserver.TCPServer(("", port), HealthHandler) as httpd:
        httpd.serve_forever()


# Start web server on a separate daemon thread before running the bot
threading.Thread(target=run_health_server, daemon=True).start()

TOPIC_IDS = {
    "Groceries": 2,
    "Car": 4,
    "Utilities": 6,
    "Medical": 8,
    "Meals" : 30,
}

MERCHANT_OPTIONS = {
    "Groceries": ["Mydin", "99 Speedmart", "Lotus's", "Aeon", "Other"],
    "Car": ["Shell", "Petronas", "Workshop", "Car Wash", "Other"],
    "Utilities": ["TNB", "Water", "Wifi", "Other"],
    "Medical": ["Clinic", "Pharmacy", "Hospital", "Other"],
    "Meals": ["McD", "KFC", "Restaurant", "Cafe", "Food Court", "Other"],
}

# Conversation States
CATEGORY, MERCHANT, CUSTOM_MERCHANT, AMOUNT, DATE_CHOICE, CUSTOM_DATE = range(6)


async def receipt_entry(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Triggered on either photo or document (PDF, DOCX) upload."""
    msg = update.message
    uploader = update.effective_user.first_name or "Family Member"
    context.user_data["uploader"] = uploader

    if msg.photo:
        context.user_data["file_type"] = "photo"
        context.user_data["file_id"] = msg.photo[-1].file_id
    elif msg.document:
        context.user_data["file_type"] = "document"
        context.user_data["file_id"] = msg.document.file_id
        context.user_data["file_name"] = msg.document.file_name
    else:
        return ConversationHandler.END

    # Dynamic 2-column category buttons
    categories = list(TOPIC_IDS.keys())
    keyboard = []
    for i in range(0, len(categories), 2):
        row = [
            InlineKeyboardButton(cat, callback_data=f"cat_{cat}")
            for cat in categories[i : i + 2]
        ]
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await msg.reply_text(
        "Receipt file received! Select category:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CATEGORY


async def category_picked(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User picks a main category."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Receipt processing cancelled.")
        return ConversationHandler.END

    selected_cat = query.data.replace("cat_", "")
    context.user_data["category"] = selected_cat

    merchants = MERCHANT_OPTIONS.get(selected_cat, ["Other"])
    buttons = [
        [InlineKeyboardButton(m, callback_data=f"merch_{m}")] for m in merchants
    ]
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.edit_message_text(
        f"Category: *{selected_cat}*\nSelect store / merchant:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return MERCHANT


async def merchant_picked(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User picks an existing store button or taps Other."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Receipt processing cancelled.")
        return ConversationHandler.END

    chosen = query.data.replace("merch_", "")

    if chosen == "Other":
        await query.edit_message_text(
            "Please type the name of the store or service:"
        )
        return CUSTOM_MERCHANT

    context.user_data["merchant"] = chosen
    await query.edit_message_text(
        f"Merchant: *{chosen}*\n\nPlease reply with total amount (e.g. 54.20):",
        parse_mode="Markdown",
    )
    return AMOUNT


async def custom_merchant_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receives custom typed merchant name."""
    context.user_data["merchant"] = update.message.text.strip()
    await update.message.reply_text(
        f"Merchant: *{context.user_data['merchant']}*\nNow reply with total amount (e.g. 54.20):",
        parse_mode="Markdown",
    )
    return AMOUNT


async def amount_received(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receives amount, then asks about receipt date."""
    raw_amount = update.message.text.strip().replace("RM", "").replace(",", "")

    try:
        amount_val = float(raw_amount)
        context.user_data["amount_str"] = f"RM {amount_val:.2f}"
        context.user_data["amount_tag"] = f"rm{int(amount_val)}"
    except ValueError:
        await update.message.reply_text(
            "Invalid format. Please enter numbers only (e.g. 45.80):"
        )
        return AMOUNT

    # Date choice buttons
    today_str = datetime.datetime.now().strftime("%d %b %Y")
    keyboard = [
        [InlineKeyboardButton(f"📅 Today ({today_str})", callback_data="date_today")],
        [InlineKeyboardButton("🗓️ Other Date", callback_data="date_custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]

    await update.message.reply_text(
        f"Amount recorded: *{context.user_data['amount_str']}*\nWhen was this receipt issued?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return DATE_CHOICE


async def date_choice_picked(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handles choosing today vs manual date."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Receipt processing cancelled.")
        return ConversationHandler.END

    if query.data == "date_today":
        today = datetime.datetime.now()
        date_display = today.strftime("%d %b %Y")
        month_tag = today.strftime("%b%Y").lower()
        await query.edit_message_text(f"Using date: {date_display}")
        return await finalize_receipt(update, context, date_display, month_tag)

    # User picked custom date
    await query.edit_message_text(
        "Please type the receipt date (e.g. `12/09/2026` or `12 Sep 2026`):",
        parse_mode="Markdown",
    )
    return CUSTOM_DATE


async def custom_date_received(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Parses custom typed date."""
    raw_date = update.message.text.strip()
    parsed_date = None

    # Common date formats to try
    formats = ["%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"]
    for fmt in formats:
        try:
            parsed_date = datetime.datetime.strptime(raw_date, fmt)
            break
        except ValueError:
            continue

    if parsed_date:
        date_display = parsed_date.strftime("%d %b %Y")
        month_tag = parsed_date.strftime("%b%Y").lower()
    else:
        # Fallback if text format cannot be strictly parsed
        date_display = raw_date
        month_tag = "receipt"

    return await finalize_receipt(update, context, date_display, month_tag)


async def finalize_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    date_display: str,
    month_tag: str,
) -> int:
    """Builds caption and posts to target topic."""
    cat = context.user_data.get("category", "General")
    merch = context.user_data.get("merchant", "General")
    uploader = context.user_data.get("uploader", "Family Member")
    photo_id = context.user_data.get("photo_id")
    amount_str = context.user_data.get("amount_str", "RM 0.00")
    amount_tag = context.user_data.get("amount_tag", "rm0")

    clean_merch_tag = "".join(filter(str.isalnum, merch.lower()))
    clean_cat_tag = cat.lower()

    # Formatted caption with Uploaded by
    caption_text = (
        f"🧾 *{merch}* ({amount_str})\n"
        f"📅 Date: {date_display}\n"
        f"👤 Uploaded by: {uploader}\n\n"
        f"#{clean_cat_tag} #{clean_merch_tag} #{month_tag} #{amount_tag}"
    )

    target_thread = TOPIC_IDS.get(cat)
    file_type = context.user_data.get("file_type")
    file_id = context.user_data.get("file_id")

    if file_type == "photo":
        await context.bot.send_photo(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=target_thread,
            photo=file_id,
            caption=caption_text,
            parse_mode="Markdown",
        )
    elif file_type == "document":
        await context.bot.send_document(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=target_thread,
            document=file_id,
            caption=caption_text,
            parse_mode="Markdown",
        )

    chat_target = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_target,
        text=f"✅ Saved to *{cat}* topic!",
        parse_mode="Markdown",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the conversation."""
    context.user_data.clear()
    await update.message.reply_text("Receipt processing cancelled.")
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points = [MessageHandler(filters.PHOTO | filters.Document.ALL, receipt_entry)],
        states={
            CATEGORY: [CallbackQueryHandler(category_picked)],
            MERCHANT: [CallbackQueryHandler(merchant_picked)],
            CUSTOM_MERCHANT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, custom_merchant_text
                )
            ],
            AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)
            ],
            DATE_CHOICE: [CallbackQueryHandler(date_choice_picked)],
            CUSTOM_DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, custom_date_received
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()