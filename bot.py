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

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set!")

MODEL_ID = "google/gemma-4-31b-it:free"

GEO_KNOWLEDGE_BASE = """
=== БАЗА ЗНАНИЙ ГЕОЛОКАЦИИ ===

СТОЛБЫ И ИЗОЛЯТОРЫ:
- Южная Австралия: Stobie Pole — бетон между двумя стальными балками
- Венгрия/Румыния: Holey Pole — бетонный с прямоугольными отверстиями до земли
- Бразилия/Парагвай: Ladder Pole — прямоугольный бетонный с "лестницей" внизу
- Япония/Тайвань: желто-черные полосы внизу столба
- США/Канада: большие металлические бочки-трансформаторы на деревянных столбах
- Испания/Франция: бетонные столбы с "лестничным" профилем

ДОРОГИ И ПОКРЫТИЕ:
- Бетонные плиты с поперечными швами каждые 5-10м: США (хайвеи), Филиппины, Россия
- Нидерланды: красный кирпич-клинкер на тротуарах
- Португалия: черно-белая мозаика "Calçada Portuguesa"
- Польша: серые и красные квадратные плитки

АРХИТЕКТУРА:
- Скандинавия: деревянные дома, фалунский красный (Швеция), минимум заборов
- Чехия/Словакия: двухцветные пастельные фасады
- Великобритания/Ирландия: темный кирпич, террасные дома; Ирландия — яркие цвета
- Таиланд: крыши с "рогами" (Chofa)
- Индонезия: крыши "Минангкабау" (загнутые вверх)
- Перу/Боливия: недостроенные вторые этажи с торчащей арматурой

РАСТИТЕЛЬНОСТЬ И ПОЧВА:
- Pinus Sylvestris (оранжевый верх ствола): Россия, Скандинавия
- Араукарии: юг Бразилии
- Масличные пальмы на плантациях: Малайзия/Индонезия
- Красная почва: Бразилия, Таиланд, Вьетнам
- Чернозём: Украина, юг России

ЯЗЫКИ И ЗНАКИ:
- Польша: Ł Ń Ż Ą Ę, нет буквы V
- Венгрия: Ő Ű Cs Dz Gy
- Вьетнам: огромное количество диакритики (₫ ơ ư)
- Турция: İ I Ğ Ş

МЕТАДАННЫЕ GOOGLE CAR:
- "Шноркель" спереди справа: Кения
- Чёрная лента на багажнике: Гана
- Пикап полиции сзади: Нигерия
- Багажник с канистрами: Монголия
- Овцы на каждой панораме: Фарерские острова

ТЕНИ И СОЛНЦЕ:
- Северное полушарие: тени в полдень падают на север
- Южное полушарие: тени в полдень падают на юг
- Тропики: в полдень тени минимальны

ЭЛЕКТРОСЕТИ:
- 60 Гц: США, Канада, Мексика, Япония (запад), Бразилия, Филиппины
- 50 Гц: Европа, Россия, Африка, Австралия, Китай, Индия
"""

SYSTEM_PROMPTS = {
    "geo": f"""Ты эксперт по визуальной геолокации. Это скриншот из GeoGuessr — определи место максимально точно.

{GEO_KNOWLEDGE_BASE}

Анализируй ВСЁ: архитектуру, знаки, разметку, растительность, рельеф, вывески, номера машин, стиль застройки. Используй базу знаний выше для точной идентификации.

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

    "osint": f"""Ты эксперт по OSINT и геолокации. Это фото для разведки — определи место максимально точно.

{GEO_KNOWLEDGE_BASE}

Анализируй ВСЁ: архитектуру, знаки, разметку, растительность, рельеф, вывески, номера машин, стиль застройки. Используй базу знаний выше для точной идентификации.

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

HINTS_TEXT = """💡 БИБЛИЯ ГЕОЛОКАЦИИ v6.0

🪵 СТОЛБЫ:
• Stobie Pole (бетон + сталь) → Южная Австралия
• Holey Pole (дырки в бетоне) → Венгрия / Румыния
• Ladder Pole (лестница внизу) → Бразилия / Парагвай
• Жёлто-чёрные полосы → Япония / Тайвань
• Бочки-трансформаторы на дереве → США / Канада

🛣️ ДОРОГИ:
• Бетонные плиты с швами → США, Филиппины, Россия
• Красный кирпич-клинкер → Нидерланды
• Чёрно-белая мозаика → Португалия
• Серые/красные квадратные плитки → Польша

🏠 АРХИТЕКТУРА:
• Фалунский красный, нет заборов → Скандинавия
• Пастельные двухцветные фасады → Чехия / Словакия
• Тёмный кирпич, террасы → Великобритания
• Крыши с "рогами" (Chofa) → Таиланд
• Арматура на недострое → Перу / Боливия

🌿 ПРИРОДА:
• Оранжевый верх сосны → Россия / Скандинавия
• Красная почва → Бразилия, Таиланд, Вьетнам
• Чернозём → Украина, юг России
• Масличные пальмы → Малайзия / Индонезия

🔤 ЯЗЫКИ:
• Ł Ń Ż Ą Ę (нет V) → Польша
• Ő Ű Cs Gy → Венгрия
• Много диакритики → Вьетнам
• İ Ğ Ş → Турция

🚗 GOOGLE CAR:
• Шноркель спереди → Кения
• Чёрная лента на багажнике → Гана
• Полицейский пикап сзади → Нигерия
• Канистры в багажнике → Монголия
• Овцы везде → Фарерские острова

☀️ ТЕНИ:
• Тени на север в полдень → Северное полушарие
• Тени на юг в полдень → Южное полушарие

⚡ ЭЛЕКТРОСЕТЬ:
• 60 Гц → США, Канада, Бразилия, Филиппины
• 50 Гц → Европа, Россия, Китай, Австралия"""

WEATHER_CODES = {
    0: "☀️ Ясно", 1: "🌤 Малооблачно", 2: "⛅️ Переменная облачность", 3: "☁️ Пасмурно",
    45: "🌫 Туман", 48: "🌫 Иней", 51: "🌧 Лёгкая морось", 53: "🌧 Морось", 55: "🌧 Сильная морось",
    61: "🌧 Лёгкий дождь", 63: "🌧 Дождь", 65: "🌧 Сильный дождь",
    71: "🌨 Лёгкий снег", 73: "🌨 Снег", 75: "🌨 Сильный снег",
    80: "🌧 Ливень", 95: "⛈ Гроза", 96: "⛈ Гроза с градом", 99: "⛈ Сильная гроза с градом"
}

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


def main_menu(chat_id, text="👁 GeoOracle\n\nВыбери режим:"):
    tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": [
            [{"text": "🎮 GeoGuessr режим", "callback_data": "mode_geo"}],
            [{"text": "🔍 OSINT режим",     "callback_data": "mode_osint"}],
            [{"text": "💡 Библия геолокации", "callback_data": "hints"}],
        ]}
    })


def extract_coords(text):
    if not text:
        return None
    m = re.search(r"COORDS\s*[:：]\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)", text, re.IGNORECASE)
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
            f"\n🌅 Восход: {fmt(res.get('sunrise', '?'))}"
            f"\n🌇 Закат: {fmt(res.get('sunset', '?'))}"
        )
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return ""


def analyze(chat_id, file_id, mode):
    status = tg_api("sendMessage", {"chat_id": chat_id, "text": "🛰 Анализирую фото..."})

    try:
        f_info = tg_api("getFile", {"file_id": file_id})
        f_path = f_info["result"]["file_path"]
        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f_path}", timeout=30
        ) as r:
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
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://geooracle-bot.onrender.com",
                "X-Title": "GeoOracle"
            }
        )

        with urllib.request.urlopen(req, timeout=60) as r:
            full_text = json.loads(r.read())["choices"][0]["message"]["content"]

        coords = extract_coords(full_text)
        clean_text = re.sub(r"COORDS\s*[:：]\s*-?\d+\.?\d*\s*,\s*-?\d+\.?\d*", "", full_text, flags=re.IGNORECASE).strip()
        weather = get_weather_info(coords[0], coords[1]) if coords else ""
        final_text = clean_text + weather

        kb = {"inline_keyboard": []}
        if coords:
            kb["inline_keyboard"].append([{
                "text": "🗺 Открыть на карте",
                "url": f"https://www.google.com/maps?q={coords[0]},{coords[1]}"
            }])
        kb["inline_keyboard"].append([{"text": "◀️ Главное меню", "callback_data": "back"}])

        try:
            tg_api("deleteMessage", {"chat_id": chat_id, "message_id": status["result"]["message_id"]})
        except:
            pass

        tg_api("sendMessage", {"chat_id": chat_id, "text": final_text, "reply_markup": kb})

    except urllib.error.HTTPError as e:
        logger.error(f"HTTP Error {e.code}: {e.reason}")
        try:
            tg_api("deleteMessage", {"chat_id": chat_id, "message_id": status["result"]["message_id"]})
        except:
            pass
        if e.code == 402:
            tg_api("sendMessage", {"chat_id": chat_id, "text": "❌ Нет баланса на OpenRouter. Пополни счёт на openrouter.ai"})
        elif e.code == 429:
            tg_api("sendMessage", {"chat_id": chat_id, "text": "⏳ Слишком много запросов. Подожди минуту и попробуй снова."})
        else:
            tg_api("sendMessage", {"chat_id": chat_id, "text": f"❌ Ошибка API: {e.code}. Попробуй позже."})

    except Exception as e:
        logger.exception("Analyze error")
        try:
            tg_api("deleteMessage", {"chat_id": chat_id, "message_id": status["result"]["message_id"]})
        except:
            pass
        tg_api("sendMessage", {"chat_id": chat_id, "text": "❌ Что-то пошло не так. Попробуй ещё раз."})


def handle_update(up):
    try:
        if "message" in up:
            msg = up["message"]
            chat_id = msg["chat"]["id"]
            if "text" in msg and msg["text"] == "/start":
                main_menu(chat_id)
            elif "photo" in msg:
                mode = user_mode.get(chat_id, "osint")
                threading.Thread(target=analyze, args=(chat_id, msg["photo"][-1]["file_id"], mode), daemon=True).start()

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
                main_menu(chat_id)

    except Exception as e:
        logger.exception("Handle update error")


def poll():
    offset = 0
    logger.info("GeoOracle started polling...")
    while True:
        try:
            res = tg_api("getUpdates", {"offset": offset, "timeout": 20})
            for up in res.get("result", []):
                offset = up["update_id"] + 1
                handle_update(up)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    def run_health():
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            def log_message(self, *args):
                pass
        HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()

    threading.Thread(target=run_health, daemon=True).start()
    logger.info("Health server started")
    poll()