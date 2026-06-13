import base64
import json
import logging
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

MODEL_ID = "anthropic/claude-sonnet-4-6"

SYSTEM_PROMPTS = {
    "geo": """Ты эксперт по визуальной геолокации. Это скриншот из GeoGuessr — определи место максимально точно.

Анализируй ВСЁ: архитектуру, знаки, разметку, растительность, рельеф, вывески, номера машин, стиль застройки.

Отвечай СТРОГО на русском языке:

🌍 ЛОКАЦИЯ:
Страна: [страна]
Город/Регион: [город]
Район: [район если известен]
Улица: [улица если известна]

📊 УВЕРЕННОСТЬ: [0-100]%

🔍 КЛЮЧЕВЫЕ ПОДСКАЗКИ ДЛЯ ЭТОГО ФОТО:
1. [улика 1 - подробно]
2. [улика 2 - подробно]
3. [улика 3 - подробно]
4. [улика 4 - подробно]
5. [улика 5 - подробно]

📍 КАК НАЙТИ ТОЧНЕЕ:
[Что именно искать в Google Maps / Street View]

COORDS: lat, lon""",

    "osint": """Ты эксперт по OSINT и геолокации. Это фото для разведки — определи место максимально точно.

Анализируй ВСЁ: архитектуру, знаки, разметку, растительность, рельеф, вывески, номера машин, стиль застройки.

Отвечай СТРОГО на русском языке:

🌍 ЛОКАЦИЯ:
Страна: [страна]
Город/Регион: [город]
Район: [район если известен]
Улица: [улица если известна]

📊 УВЕРЕННОСТЬ: [0-100]%

🔍 КЛЮЧЕВЫЕ ПОДСКАЗКИ ДЛЯ ЭТОГО ФОТО:
1. [улика 1 - подробно]
2. [улика 2 - подробно]
3. [улика 3 - подробно]
4. [улика 4 - подробно]
5. [улика 5 - подробно]

📍 КАК НАЙТИ ТОЧНЕЕ:
[Что именно искать в Google Maps / Street View]

COORDS: lat, lon"""
}

HINTS_TEXT = """💡 Гайд по визуальной геолокации

🪵 Столбы:
• Дырки в бетонных столбах → часто Венгрия/Румыния
• Чёрно-оранжевые основания → Таиланд
• Жёлто-чёрные полосы → Корея/Япония

🚧 Болларды:
• Красный прямоугольник смещён → Сербия
• Белый фон сзади → Хорватия
• Сильно потрёпанные → часто Украина

🚗 Номерные знаки:
• Жёлтый фон → Великобритания / Израиль
• Синяя полоса ЕС → Европа

🛣️ Разметка:
• Жёлтые линии → Америка / Азия
• Белые линии → чаще Европа

🏙️ Смотри ещё на:
• фасады домов и тип окон
• ширину дорог
• стиль ТЦ / заправок / вывесок"""

WEATHER_CODES = {
    0: "☀️ Ясно", 1: "🌤 Малооблачно", 2: "⛅️ Переменная облачность", 3: "☁️ Пасмурно",
    45: "🌫 Туман", 48: "🌫 Иней", 51: "🌧 Лёгкая морось", 53: "🌧 Морось", 55: "🌧 Сильная морось",
    61: "🌧 Лёгкий дождь", 63: "🌧 Дождь", 65: "🌧 Сильный дождь",
    71: "🌨 Лёгкий снег", 73: "🌨 Снег", 75: "🌨 Сильный снег",
    80: "🌧 Ливень", 95: "⛈ Гроза", 96: "⛈ Гроза с градом", 99: "⛈ Сильная гроза с градом"
}

# Хранилище режимов пользователей
user_mode = {}


def tg_api(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main_menu(chat_id, text="Выбери режим:"):
    tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": [
            [{"text": "🎮 GeoGuessr режим", "callback_data": "mode_geo"}],
            [{"text": "🔍 OSINT режим",     "callback_data": "mode_osint"}],
            [{"text": "💡 Как искать локацию", "callback_data": "hints"}],
        ]}
    })


def extract_coords(text):
    if not text:
        return None
    m = re.search(r"COORDS\s*[:：]\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", text, re.IGNORECASE)
    if m:
        try:
            return (float(m.group(1)), float(m.group(2)))
        except:
            return None
    return None


def get_weather_info(lat, lon):
    try:
        w_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        with urllib.request.urlopen(w_url, timeout=10) as r:
            cw = json.loads(r.read()).get("current_weather", {})
            temp = cw.get("temperature", "?")
            wind = cw.get("windspeed", "?")
            desc = WEATHER_CODES.get(cw.get("weathercode", 0), "❓")

        s_url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&formatted=0"
        with urllib.request.urlopen(s_url, timeout=10) as r:
            res = json.loads(r.read()).get("results", {})

        def fmt(iso):
            try:
                return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M")
            except:
                return "?"

        offset = round(lon / 15)
        local_time = (datetime.utcnow() + timedelta(hours=offset)).strftime("%H:%M")
        sign = "+" if offset >= 0 else ""

        return (
            f"\n\n🌤 Погода: {desc}, {temp}°C"
            f"\n💨 Ветер: {wind} км/ч"
            f"\n🕐 Местное время: {local_time} (UTC{sign}{offset})"
            f"\n🌅 Восход: {fmt(res.get('sunrise','?'))}"
            f"\n🌇 Закат: {fmt(res.get('sunset','?'))}"
        )
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return ""


def analyze(chat_id, file_id, mode):
    status = tg_api("sendMessage", {"chat_id": chat_id, "text": "🛰 Анализирую фото..."})

    try:
        f_info = tg_api("getFile", {"file_id": file_id})
        f_path = f_info["result"]["file_path"]
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f_path}") as r:
            img_b64 = base64.b64encode(r.read()).decode()

        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS[mode]},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": "Найди это место."}
                ]}
            ]
        }

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=60) as r:
            full_text = json.loads(r.read())["choices"][0]["message"]["content"]

        coords = extract_coords(full_text)
        clean_text = full_text.split("COORDS:")[0].strip()
        weather = get_weather_info(coords[0], coords[1]) if coords else ""
        final_text = clean_text + weather

        kb = {"inline_keyboard": []}
        if coords:
            kb["inline_keyboard"].append([{
                "text": "🗺 Открыть на карте",
                "url": f"https://www.google.com/maps?q={coords[0]},{coords[1]}"
            }])
        kb["inline_keyboard"].append([{"text": "🔄 Новое фото", "callback_data": "back"}])
        kb["inline_keyboard"].append([{"text": "◀️ Главное меню", "callback_data": "back"}])

        tg_api("deleteMessage", {"chat_id": chat_id, "message_id": status["result"]["message_id"]})
        tg_api("sendMessage", {"chat_id": chat_id, "text": final_text, "reply_markup": kb})

    except Exception as e:
        logger.exception("Error")
        tg_api("sendMessage", {"chat_id": chat_id, "text": "❌ Ошибка. Проверь баланс OpenRouter."})


def handle_update(up):
    try:
        if "message" in up:
            msg = up["message"]
            chat_id = msg["chat"]["id"]
            if "text" in msg and msg["text"] == "/start":
                main_menu(chat_id, "👁 GeoOracle\n\nВыбери режим:")
            elif "photo" in msg:
                mode = user_mode.get(chat_id, "geo")
                threading.Thread(target=analyze, args=(chat_id, msg["photo"][-1]["file_id"], mode)).start()

        elif "callback_query" in up:
            cb = up["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            data = cb["data"]
            tg_api("answerCallbackQuery", {"callback_query_id": cb["id"]})

            if data == "mode_geo":
                user_mode[chat_id] = "geo"
                tg_api("sendMessage", {"chat_id": chat_id, "text": "🎮 GeoGuessr режим активирован!\n\nПришли скриншот из игры."})
            elif data == "mode_osint":
                user_mode[chat_id] = "osint"
                tg_api("sendMessage", {"chat_id": chat_id, "text": "🔍 OSINT режим активирован!\n\nПришли фото для анализа."})
            elif data == "hints":
                tg_api("sendMessage", {
                    "chat_id": chat_id,
                    "text": HINTS_TEXT,
                    "reply_markup": {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "back"}]]}
                })
            elif data == "back":
                main_menu(chat_id, "Выбери режим:")

    except Exception as e:
        logger.exception("Handle update error")


def poll():
    offset = 0
    while True:
        try:
            res = tg_api("getUpdates", {"offset": offset, "timeout": 20})
            for up in res.get("result", []):
                offset = up["update_id"] + 1
                handle_update(up)
        except:
            time.sleep(5)


if __name__ == "__main__":
    def run_h():
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
        HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()

    threading.Thread(target=run_h, daemon=True).start()
    poll()