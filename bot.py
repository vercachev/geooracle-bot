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

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

MODEL_ID = "anthropic/claude-sonnet-4-6"

SYSTEM_PROMPT = """Ты эксперт по визуальной геолокации. Твоя задача — определить место по фотографии максимально точно.

Анализируй ВСЁ: архитектуру, знаки, разметку, растительность, рельеф, вывески, номера машин, стиль застройки.

Отвечай СТРОГО на русском языке в следующем формате:

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

# --- ПОГОДА / ВРЕМЯ / СОЛНЦЕ ---
WEATHER_CODES = {
    0: "☀️ Ясно", 1: "🌤 Малооблачно", 2: "⛅️ Переменная облачность", 3: "☁️ Пасмурно",
    45: "🌫 Туман", 48: "🌫 Иней", 51: "🌧 Лёгкая морось", 53: "🌧 Морось", 55: "🌧 Сильная морось",
    61: "🌧 Лёгкий дождь", 63: "🌧 Дождь", 65: "🌧 Сильный дождь", 71: "🌨 Лёгкий снег", 73: "🌨 Снег", 75: "🌨 Сильный снег",
    80: "🌧 Ливень", 95: "⛈ Гроза", 96: "⛈ Гроза с градом", 99: "⛈ Сильная гроза с градом"
}

def get_weather_info(lat, lon):
    """Бесплатные API: погода + время + солнце. Без ключей."""
    try:
        # Погода
        w_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        with urllib.request.urlopen(w_url, timeout=10) as r:
            w_data = json.loads(r.read())
            cw = w_data.get("current_weather", {})
            temp = cw.get("temperature", "?")
            wind = cw.get("windspeed", "?")
            code = cw.get("weathercode", 0)
            desc = WEATHER_CODES.get(code, "❓ Неизвестно")

        # Солнце
        s_url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&formatted=0"
        with urllib.request.urlopen(s_url, timeout=10) as r:
            s_data = json.loads(r.read())
            res = s_data.get("results", {})
            sunrise = res.get("sunrise", "?")
            sunset = res.get("sunset", "?")

        # Время (грубая оценка по долготе: 15° = 1 час)
        offset = round(lon / 15)
        utc_now = datetime.utcnow()
        local_time = utc_now + timedelta(hours=offset)
        time_str = local_time.strftime("%H:%M")

        def fmt_iso(iso):
            if iso == "?":
                return "?"
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                return dt.strftime("%H:%M")
            except:
                return "?"

        return {
            "temp": temp,
            "wind": wind,
            "desc": desc,
            "time": time_str,
            "offset": offset,
            "sunrise": fmt_iso(sunrise),
            "sunset": fmt_iso(sunset)
        }
    except Exception as e:
        logger.error(f"Weather fetch error: {e}")
        return None

# --- UTILS ---
def tg_api(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

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

# --- LOGIC ---
def analyze(chat_id, file_id):
    status = tg_api("sendMessage", {"chat_id": chat_id, "text": "🛰 *Анализирую фото...*", "parse_mode": "Markdown"})

    try:
        f_info = tg_api("getFile", {"file_id": file_id})
        f_path = f_info["result"]["file_path"]
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f_path}") as r:
            img_b64 = base64.b64encode(r.read()).decode()

        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": "Найди это место."}
                ]}
            ]
        }

        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                                     data=json.dumps(payload).encode(),
                                     headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                              "Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
            full_text = res["choices"][0]["message"]["content"]

        coords = extract_coords(full_text)
        clean_text = full_text.split("COORDS:")[0].strip()

        # Добавляем погоду, время, солнце
        extra = ""
        if coords:
            w = get_weather_info(coords[0], coords[1])
            if w:
                sign = "+" if w["offset"] >= 0 else ""
                extra = (
                    f"\n\n🌤 Погода: {w['desc']}, {w['temp']}°C"
                    f"\n💨 Ветер: {w['wind']} км/ч"
                    f"\n🕐 Местное время: {w['time']} (UTC{sign}{w['offset']})"
                    f"\n🌅 Восход: {w['sunrise']}"
                    f"\n🌇 Закат: {w['sunset']}"
                )

        final_text = clean_text + extra

        kb = {"inline_keyboard": []}
        if coords:
            kb["inline_keyboard"].append([{"text": "🗺 Открыть на карте", "url": f"https://www.google.com/maps?q={coords[0]},{coords[1]}"}])
        kb["inline_keyboard"].append([{"text": "🔄 Новое фото", "callback_data": "reset"}])

        tg_api("deleteMessage", {"chat_id": chat_id, "message_id": status["result"]["message_id"]})
        tg_api("sendMessage", {"chat_id": chat_id, "text": final_text, "reply_markup": kb})

    except Exception as e:
        logger.exception("Error")
        tg_api("sendMessage", {"chat_id": chat_id, "text": "❌ Ошибка при обращении к ИИ. Проверь баланс OpenRouter."})

def handle_update(up):
    if "message" in up:
        msg = up["message"]
        chat_id = msg["chat"]["id"]
        if "text" in msg and msg.get("text") == "/start":
            tg_api("sendMessage", {"chat_id": chat_id, "text": "📸 Пришли мне фото, и я определю локацию с погодой и временем!"})
        elif "photo" in msg:
            threading.Thread(target=analyze, args=(chat_id, msg["photo"][-1]["file_id"])).start()
    elif "callback_query" in up:
        tg_api("sendMessage", {"chat_id": up["callback_query"]["message"]["chat"]["id"], "text": "📸 Жду фото!"})

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