import base64
import os
import urllib.request
import json
import logging
import time
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
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-4-scout:free",
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
[3 specific tips for THIS photo to narrow down location]"""

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

def call_model(model_id, image_b64, user_content):
    payload = json.dumps({
        "model": model_id,
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
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://geooracle-bot.onrender.com",
            "X-Title": "GeoOracle"
        }
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]

def analyze_photo(file_id, mode):
    file_info = tg_request("getFile", {"file_id": file_id})
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

    with urllib.request.urlopen(file_url, timeout=30) as r:
        image_b64 = base64.b64encode(r.read()).decode()

    user_content = "Определи локацию максимально точно." if mode == "osint" else \
                   "Это скриншот из GeoGuessr. Определи страну, регион и дай подсказки."

    last_error = None
    for model_id in MODELS:
        try:
            logger.info(f"Trying model: {model_id}")
            result = call_model(model_id, image_b64, user_content)
            logger.info(f"Success: {model_id}")
            return result
        except urllib.error.HTTPError as e:
            last_error = e
            logger.warning(f"Model {model_id} HTTP {e.code}, trying next...")
            time.sleep(2)
        except Exception as e:
            last_error = e
            logger.warning(f"Model {model_id} error: {e}, trying next...")
            time.sleep(2)

    raise Exception(f"All models failed. Last error: {last_error}")

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
                sent = send_message(chat_id, f"{mode_text} | 👁️ Анализирую фото...")

                try:
                    file_id = msg["photo"][-1]["file_id"]
                    result = analyze_photo(file_id, mode)
                    try:
                        tg_request("deleteMessage", {"chat_id": chat_id, "message_id": sent["result"]["message_id"]})
                    except:
                        pass
                    send_message(chat_id, result, main_keyboard())
                except Exception as e:
                    logger.error(f"Analyze failed: {e}")
                    try:
                        tg_request("deleteMessage", {"chat_id": chat_id, "message_id": sent["result"]["message_id"]})
                    except:
                        pass
                    send_message(chat_id, "❌ Не удалось проанализировать фото. Попробуй ещё раз.", main_keyboard())

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

    except Exception as e:
        logger.error(f"Error handling update: {e}")

def poll():
    offset = 0
    # Ждём пока старый инстанс умрёт (фикс 409)
    logger.info("Waiting for old instance to shut down...")
    time.sleep(5)
    try:
        tg_request("getUpdates", {"offset": -1, "timeout": 1})
    except:
        pass
    time.sleep(3)
    logger.info("GeoOracle started polling...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            with urllib.request.urlopen(url, timeout=40) as r:
                data = json.loads(r.read())
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                threading.Thread(target=handle_update, args=(update,), daemon=True).start()
        except urllib.error.HTTPError as e:
            if e.code == 409:
                logger.warning("409 Conflict — waiting...")
                time.sleep(10)
            else:
                logger.error(f"Polling HTTP error: {e}")
                time.sleep(5)
        except Exception as e:
            logger.error(f"Polling error: {e}")
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