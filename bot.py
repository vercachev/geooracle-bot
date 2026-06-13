import asyncio
import base64
import os
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

SYSTEM_PROMPT = """You are GeoOracle — an expert AI geolocator. Analyze photos using these methods:

BOLLARDS: Serbia=red rectangle off-center, Croatia=white back, Czech=fluorescent orange, Ukraine=weathered, Albania=vertical red rectangle
POLES: Hungary/Romania=holes in poles, Thailand=black-orange-black-orange base, South Korea/Japan=yellow-black stripes, Mexico/Colombia=octagonal
LICENSE PLATES: UK/Singapore/Israel=yellow, Russia=all white, Ukraine=stripes, Italy/France=blue stripe, Malaysia=black background
ROAD MARKINGS: Yellow center lines=Americas/Asia, White=Europe/Africa, Double yellow outer=UK/Singapore
ARCHITECTURE: Soviet panels=post-USSR, specific shed styles=Belgium, etc.
SCRIPTS: Cyrillic=Russia/Ukraine/Bulgaria/Serbia, Devanagari=India/Nepal, Hangul=South Korea, Thai script=Thailand
VEGETATION: soil color, grass type, tropical vs temperate
SUN DIRECTION: determines hemisphere

Always respond in Russian. Structure your response EXACTLY like this:

🌍 ЛОКАЦИЯ:
Страна: [country]
Город/Регион: [city/region]
Район: [district if possible]
Улица: [street if possible]

📊 УВЕРЕННОСТЬ: [X]% 

🔍 КЛЮЧЕВЫЕ ПОДСКАЗКИ ДЛЯ ЭТОГО ФОТО:
[3-5 personalized visual clues YOU noticed in THIS specific photo]

🗺️ КАК НАЙТИ ТОЧНЕЕ:
[3 specific tips for THIS photo - what to look for to narrow down location]"""

HINTS_PROMPT = """You are GeoOracle — an expert in visual geolocation using Rainbolt's methods.
Give a structured guide on HOW to find location in photos. Respond in Russian.
Cover: bollards, poles, license plates, road markings, scripts, vegetation, architecture, sun direction.
Be practical and specific with examples."""

user_mode = {}

def main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎮 GeoGuessr режим", callback_data="mode_geo")
    builder.button(text="🔍 OSINT режим", callback_data="mode_osint")
    builder.button(text="💡 Как искать локацию", callback_data="hints")
    builder.adjust(1)
    return builder.as_markup()

def back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Главное меню", callback_data="back")
    return builder.as_markup()

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👁️ *GeoOracle* — определяю локации по фото\n\n"
        "Выбери режим работы:",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "mode_geo")
async def mode_geo(call: CallbackQuery):
    user_mode[call.from_user.id] = "geo"
    await call.message.edit_text(
        "🎮 *GeoGuessr режим*\n\n"
        "Отправь скриншот из игры — определю страну, регион и дам подсказки как Rainbolt!\n\n"
        "📸 Жду фото...",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )

@dp.callback_query(F.data == "mode_osint")
async def mode_osint(call: CallbackQuery):
    user_mode[call.from_user.id] = "osint"
    await call.message.edit_text(
        "🔍 *OSINT режим*\n\n"
        "Отправь любое фото — определю локацию максимально точно!\n\n"
        "📸 Жду фото...",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )

@dp.callback_query(F.data == "hints")
async def show_hints(call: CallbackQuery):
    await call.message.edit_text("💡 Загружаю гайд...", reply_markup=None)
    
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "anthropic/claude-sonnet-4-6",
                "max_tokens": 1500,
                "messages": [
                    {"role": "system", "content": HINTS_PROMPT},
                    {"role": "user", "content": "Дай полный гайд как определять локацию по визуальным подсказкам"}
                ]
            }
        )
        data = response.json()
        text = data["choices"][0]["message"]["content"]
    
    await call.message.answer(text, reply_markup=main_keyboard())

@dp.callback_query(F.data == "back")
async def back(call: CallbackQuery):
    await call.message.edit_text(
        "👁️ *GeoOracle* — определяю локации по фото\n\n"
        "Выбери режим работы:",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.message(F.photo)
async def handle_photo(message: Message):
    mode = user_mode.get(message.from_user.id, "osint")
    mode_text = "🎮 GeoGuessr" if mode == "geo" else "🔍 OSINT"
    
    thinking = await message.answer(f"{mode_text} | 👁️ Анализирую фото...")
    
    # Download photo
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_b64 = base64.b64encode(file_bytes.read()).decode()
    
    user_content = "Определи локацию на этом фото максимально точно." if mode == "osint" else \
                   "Это скриншот из GeoGuessr. Определи страну, регион и дай подсказки для игры."
    
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
    await message.answer(result, reply_markup=main_keyboard())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
