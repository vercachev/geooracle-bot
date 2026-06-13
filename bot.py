import base64
import os
import re
import urllib.request
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set!")

MODELS = [
    "anthropic/claude-sonnet-4-6",
    "google/gemini-2.5-pro",
    "qwen/qwen2.5-vl-72b-instruct",
]

SYSTEM_PROMPT = """You are GeoOracle — an expert AI geolocator. Analyze photos using these methods:

BOLLARDS: Serbia=red rectangle off-center, Croatia=white back, Czech=fluorescent orange, Ukraine=weathered
POLES: Hungary/Romania=holes in poles, Thailand=black-orange base, South Korea/Japan=yellow-black stripes
LICENSE PLATES: UK/Singapore/Israel=yellow, Russia=all white, Ukraine=stripes, Italy/France=blue stripe
ROAD MARKINGS: Yellow center=Americas/Asia, White=Europe/Africa
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
[3 specific tips for THIS photo to narrow down location]

COORDS: [latitude], [longitude]

IMPORTANT: Always end your response with the COORDS line in decimal degrees (e.g. COORDS: 44.1598, 28.6348). If unsure, use city center coordinates."""

HINTS_TEXT = """💡 Гайд по визуальной геолокации (метод Rainbolt)

🪵 Столбы:
• Дырки в столбах → Венгрия/Румыния
• Чёрно-оранжевое основание → Таиланд
• Жёлто-чёрные полосы → Корея/Япония

🚧 Болларды:
• Красный прямоугольник смещён → Сербия
• Белый фон сзади → Хорватия
• Потрёпанный вид → Украина

🚗 Номерные знаки:
• Жёлтый фон → Великобритания/Израиль
• Белый → Россия
• Синяя полоса → Италия/Франция

🛣️ Разметка: жёлтые линии → Америка/Азия, белые → Европа"""

user_mode = {}
# Хранит последний file_id фото для кнопки "Пересчитать"
user_last_photo = {}

def tg_request(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_request("sendMessage", data)

def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_request("editMessageText", data)

def answer_callback(callback_id):
    tg_request("answerCallbackQuery", {"callback_query_id": callback_id})

def main_keyboard():
    return {"inline_keyboard": [
        [{"text": "🎮 GeoGuessr режим", "callback_data": "mode_geo"}],
        [{"text": "🔍 OSINT режим", "callback_data": "mode_osint"}],
        [{"text": "💡 Как искать локацию", "callback_data": "hints"}],
    ]}

def back_keyboard():
    return {"inline_keyboard": [[{"text": "◀️ Главное меню", "callback_data": "back"}]]}

def result_keyboard(coords=None):
    rows = []
    if coords:
        lat, lon = coords
        maps_url = f"https://www.google.com/maps?q={lat},{lon}"
        rows.append([{"text": "🗺️ Открыть на карте", "url": maps_url}])
    rows.append([{"text": "🔄 Пересчитать", "callback_data": "reanalyze"}])
    rows.append([{"text": "◀️ Главное меню", "callback_data": "back"}])
    return {"inline_keyboard": rows}

def extract_coords(text):
    match = re.search(r"COORDS:\s*([-\d.]+),\s*([-\d.]+)", text)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None

def extract_country(text):
    """Извлекает страну из ответа модели для голосования."""
    match = re.search(r"Страна:\s*(.+)", text)
    if match:
        return match.group(1).strip().lower()
    return None

def call_model(model, image_b64, user_content, results, index):
    """Запрос к одной модели, результат пишется в results[index]."""
    try:
        payload = json.dumps({
            "model": model,
            "max_tokens": 1000,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": user_content}
                ]}
            ]
        }).encode()

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        results[index] = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Model {model} error: {e}")
        results[index] = None

def analyze_photo_consensus(file_id, mode):
    """Запрашивает 3 модели параллельно и возвращает итоговый текст + координаты."""
    file_info = tg_request("getFile", {"file_id": file_id})
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

    with urllib.request.urlopen(file_url) as r:
        image_b64 = base64.b64encode(r.read()).decode()

    user_content = "Определи локацию максимально точно." if mode == "osint" else \
                   "Это скриншот из GeoGuessr. Определи страну, регион и дай подсказки."

    results = [None] * len(MODELS)
    threads = []
    for i, model in enumerate(MODELS):
        t = threading.Thread(target=call_model, args=(model, image_b64, user_content, results, i))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # Голосование по стране
    countries = [extract_country(r) for r in results if r]
    valid_results = [r for r in results if r]

    if not valid_results:
        return "❌ Все модели не ответили. Попробуй ещё раз.", None

    # Считаем голоса
    country_votes = {}
    for c in countries:
        if c:
            country_votes[c] = country_votes.get(c, 0) + 1

    best_country = max(country_votes, key=country_votes.get) if country_votes else None
    votes = country_votes.get(best_country, 0) if best_country else 0
    total = len(valid_results)

    # Берём ответ Claude как основной (индекс 0), если он есть
    main_result = results[0] if results[0] else valid_results[0]

    # Убираем строку COORDS из текста для пользователя
    clean_result = re.sub(r"\nCOORDS:.*", "", main_result).strip()

    # Координаты берём из основного ответа
    coords = extract_coords(main_result)

    # Формируем шапку консенсуса
    if votes == total:
        consensus_header = f"✅ *Консенсус ({votes}/{total}):* все модели согласны\n\n"
    elif votes >= 2:
        consensus_header = f"⚠️ *Частичный консенсус ({votes}/{total}):* большинство согласно\n\n"
    else:
        consensus_header = f"❌ *Расхождение ({votes}/{total}):* модели не сошлись\n\n"

    return consensus_header + clean_result, coords

def handle_update(update):
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]

            if "text" in msg and msg["text"] == "/start":
                send_message(chat_id, "👁️ *GeoOracle* — определяю локации по фото\n\nВыбери режим работы:", main_keyboard())

            elif "photo" in msg:
                mode = user_mode.get(chat_id, "osint")
                mode_text = "🎮 GeoGuessr" if mode == "geo" else "🔍 OSINT"
                sent = send_message(chat_id, f"{mode_text} | 👁️ Анализирую фото тремя моделями...")

                file_id = msg["photo"][-1]["file_id"]
                user_last_photo[chat_id] = (file_id, mode)

                result_text, coords = analyze_photo_consensus(file_id, mode)

                tg_request("deleteMessage", {"chat_id": chat_id, "message_id": sent["result"]["message_id"]})
                send_message(chat_id, result_text, result_keyboard(coords))

        elif "callback_query" in update:
            cb = update["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            message_id = cb["message"]["message_id"]
            data = cb["data"]
            answer_callback(cb["id"])

            if data == "mode_geo":
                user_mode[chat_id] = "geo"
                edit_message(chat_id, message_id, "🎮 *GeoGuessr режим*\n\nОтправь скриншот из игры!\n\n📸 Жду фото...", back_keyboard())
            elif data == "mode_osint":
                user_mode[chat_id] = "osint"
                edit_message(chat_id, message_id, "🔍 *OSINT режим*\n\nОтправь любое фото!\n\n📸 Жду фото...", back_keyboard())
            elif data == "hints":
                edit_message(chat_id, message_id, HINTS_TEXT, back_keyboard())
            elif data == "back":
                edit_message(chat_id, message_id, "👁️ *GeoOracle* — определяю локации по фото\n\nВыбери режим работы:", main_keyboard())
            elif data == "reanalyze":
                if chat_id in user_last_photo:
                    file_id, mode = user_last_photo[chat_id]
                    mode_text = "🎮 GeoGuessr" if mode == "geo" else "🔍 OSINT"
                    edit_message(chat_id, message_id, f"{mode_text} | 🔄 Пересчитываю тремя моделями...")
                    result_text, coords = analyze_photo_consensus(file_id, mode)
                    edit_message(chat_id, message_id, result_text, result_keyboard(coords))
                else:
                    answer_callback(cb["id"])

    except Exception as e:
        logger.error(f"Error handling update: {e}")

def poll():
    offset = 0
    logger.info("Starting polling...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            with urllib.request.urlopen(url, timeout=40) as r:
                data = json.loads(r.read())

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                threading.Thread(target=handle_update, args=(update,), daemon=True).start()

        except Exception as e:
            logger.error(f"Polling error: {e}")
            import time
            time.sleep(5)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    logger.info("Starting GeoOracle bot...")
    server = HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server started")
    poll()
