import base64
import os
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

SYSTEM_PROMPT = """You are GeoOracle — an expert AI geolocator. Analyze photos using these methods:

BOLLARDS: Serbia=red rectangle off-center, Croatia=white back, Czech=fluorescent orange, Ukraine=weathered, Albania=vertical red rectangle
POLES: Hungary/Romania=holes in poles, Thailand=black-orange base, South Korea/Japan=yellow-black stripes, Mexico/Colombia=octagonal
LICENSE PLATES: UK/Singapore/Israel=yellow, Russia=all white, Ukraine=stripes, Italy/France=blue stripe, Malaysia=black background
ROAD MARKINGS: Yellow center=Americas/Asia, White=Europe/Africa, Double yellow outer=UK/Singapore
SCRIPTS: Cyrillic=Russia/Ukraine/Bulgaria, Devanagari=India/Nepal, Hangul=South Korea, Thai=Thailand
VEGETATION: soil color, grass type, tropical vs temperate
SUN DIRECTION: determines hemisphere

Always respond in Russian. Structure EXACTLY like this:

🌍 ЛОКАЦИЯ:
Страна: [country]
Город/Регион: [city/region]
Район: [district if possible]
Улица: [street if possible]

📊 УВЕРЕННОСТЬ: [X]%

🔍 КЛЮЧЕВЫЕ ПОДСКАЗКИ ДЛЯ ЭТОГО ФОТО:
[3-5 personalized clues YOU noticed in THIS specific photo]

🗺️ КАК НАЙТИ ТОЧНЕЕ:
[3 specific tips for THIS photo to narrow down location]"""

HINTS_TEXT = """💡 *Гайд по визуальной геолокации (метод Rainbolt)*

*🪵 Столбы (самое важное!)*
• Дырки в столбах → Венгрия/Румыния
• Чёрно-оранжевое основание → Таиланд
• Жёлто-чёрные полосы → Корея/Япония
• Восьмигранные → Мексика/Колумбия

*🚧 Болларды*
• Красный прямоугольник смещён → Сербия
• Белый фон сзади → Хорватия
• Флуоресцентный оранжевый → Чехия
• Потрёпанный вид → Украина

*🚗 Номерные знаки*
• Жёлтый фон → Великобритания/Израиль
• Белый → Россия
• Полосы → Украина/Албания
• Синяя полоса → Италия/Франция

*🛣️ Разметка дорог*
• Жёлтые линии по центру → Америка/Азия
• Белые → Европа/Африка

*🌿 Растительность и почва*
• Цвет почвы, тип травы
• Тропическая vs умеренная зона
• Направление солнца → определяет полушарие

*✍️ Надписи и шрифты*
• Кириллица → Россия/Украина/Болгария
• Деванагари → Индия/Непал  
• Хангыль → Корея
• Тайский → Таиланд"""

user_mode = {}

def main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎮 GeoGuessr режим", callback_data="mode_geo")],
        [InlineKeyboardButton("🔍 OSINT режим", callback_data="mode_osint")],
        [InlineKeyboardButton("💡 Как искать локацию", callback_data="hints")],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_keyboard():
    keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data="back")]]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👁️ *GeoOracle* — определяю локации по фото\n\nВыбери режим работы:",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "mode_geo":
        user_mode[query.from_user.id] = "geo"
        await query.edit_message_text(
            "🎮 *GeoGuessr режим*\n\nОтправь скриншот из игры!\n\n📸 Жду фото...",
            parse_mode="Markdown",
            reply_markup=back_keyboard()
        )
    elif query.data == "mode_osint":
        user_mode[query.from_user.id] = "osint"
        await query.edit_message_text(
            "🔍 *OSINT режим*\n\nОтправь любое фото — определю локацию максимально точно!\n\n📸 Жду фото...",
            parse_mode="Markdown",
            reply_markup=back_keyboard()
        )
    elif query.data == "hints":
        await query.edit_message_text(
            HINTS_TEXT,
            parse_mode="Markdown",
            reply_markup=back_keyboard()
        )
    elif query.data == "back":
        await query.edit_message_text(
            "👁️ *GeoOracle* — определяю локации по фото\n\nВыбери режим работы:",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = user_mode.get(update.effective_user.id, "osint")
    mode_text = "🎮 GeoGuessr" if mode == "geo" else "🔍 OSINT"
    
    thinking = await update.message.reply_text(f"{mode_text} | 👁️ Анализирую фото...")
    
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    
    async with httpx.AsyncClient() as client:
        img_response = await client.get(file.file_path)
        image_b64 = base64.b64encode(img_response.content).decode()
    
    user_content = "Определи локацию максимально точно." if mode == "osint" else \
                   "Это скриншот из GeoGuessr. Определи страну, регион и дай подсказки."
    
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "anthropic/claude-sonnet-4-6",
                "max_tokens": 1000,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": user_content}
                    ]}
                ]
            }
        )
        data = response.json()
        result = data["choices"][0]["message"]["content"]
    
    await thinking.delete()
    await update.message.reply_text(result, reply_markup=main_keyboard())

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.run_polling()

if __name__ == "__main__":
    main()
