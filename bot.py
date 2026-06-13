import base64
import os
import re
import math
import struct
import io
import urllib.request
import urllib.parse
import json
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set!")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
JUDGE_MODEL = "anthropic/claude-sonnet-4-6"

AGENTS = [
    {
        "id": 1,
        "name": "Архитектор",
        "emoji": "🏛️",
        "role": "Архитектура и городская среда",
        "model": "anthropic/claude-sonnet-4-6",
        "focus": (
            "Ты — эксперт по АРХИТЕКТУРЕ и ГОРОДСКОЙ СРЕДЕ. Твоя специализация:\n"
            "• Стили зданий, материалы фасадов, типовая застройка по регионам\n"
            "• Крыши (черепица, шифер, плоские), окна, балконы, дымоходы\n"
            "• Планировка улиц, ширина дорог, тротуары, бордюры\n"
            "• Типовое советское/европейское/азиатское/американское жильё\n"
            "• Заборы, ограждения, городская мебель, фонари"
        ),
    },
    {
        "id": 2,
        "name": "Натуралист",
        "emoji": "🌿",
        "role": "Природа и ландшафт",
        "model": "google/gemini-2.5-pro",
        "focus": (
            "Ты — эксперт по ПРИРОДЕ и ЛАНДШАФТУ. Твоя специализация:\n"
            "• Тип растительности (тропики/умеренный/пустыня/тайга), породы деревьев\n"
            "• Цвет и тип почвы, рельеф, горы, водоёмы\n"
            "• Климатические признаки, сезон, угол и направление солнца → полушарие\n"
            "• Сельскохозяйственные культуры, поля, луга\n"
            "• Геологические особенности, характер местности"
        ),
    },
    {
        "id": 3,
        "name": "Культуролог",
        "emoji": "🚃",
        "role": "Культура и транспорт",
        "model": "qwen/qwen2.5-vl-72b-instruct",
        "focus": (
            "Ты — эксперт по КУЛЬТУРЕ и ТРАНСПОРТУ. Твоя специализация:\n"
            "• Письменность и язык на вывесках (кириллица, латиница, иероглифы, арабский)\n"
            "• Автомобили: марки, номерные знаки, цвет и формат табличек\n"
            "• Сторона движения (лево/право), общественный транспорт\n"
            "• Одежда людей, культурные и религиозные маркеры\n"
            "• Бренды, магазины, реклама, специфичные для региона"
        ),
    },
    {
        "id": 4,
        "name": "Детектив",
        "emoji": "🔧",
        "role": "Инфраструктура и детали",
        "model": "openai/gpt-4o",
        "focus": (
            "Ты — эксперт по ИНФРАСТРУКТУРЕ и МЕЛКИМ ДЕТАЛЯМ. Твоя специализация:\n"
            "• Столбы (дырки, основания, форма), болларды (цвет, форма)\n"
            "• Дорожная разметка (цвет линий), дорожные знаки, светофоры\n"
            "• Электропровода, трансформаторы, люки, ограждения дорог\n"
            "• Покрытие дорог, мелкие технические детали\n"
            "• Метод Rainbolt: болларды, столбы, разметка как маркеры стран"
        ),
    },
]

OUTPUT_FORMAT = (
    "Отвечай СТРОГО на русском языке в таком формате:\n\n"
    "🌍 ЛОКАЦИЯ: [страна, регион, город — максимально точно]\n"
    "📊 УВЕРЕННОСТЬ: [0-100]%\n"
    "🔍 АРГУМЕНТЫ: [2-4 коротких ключевых наблюдения именно по твоей специализации, "
    "которые ты заметил на ЭТОМ фото]\n"
    "COORDS: lat, lon\n\n"
    "ВАЖНО: строка COORDS обязательна и должна содержать численные координаты "
    "(широта, долгота) твоей лучшей догадки, например: COORDS: 48.8566, 2.3522"
)

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

# ====
#  TELEGRAM API
# ====

def tg_request(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    data = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_request("sendMessage", data)

def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        return tg_request("editMessageText", data)
    except Exception as e:
        logger.error(f"edit_message error: {e}")
        return None

def answer_callback(callback_id):
    try:
        tg_request("answerCallbackQuery", {"callback_query_id": callback_id})
    except Exception as e:
        logger.error(f"answer_callback error: {e}")

def main_keyboard():
    return {"inline_keyboard": [
        [{"text": "🎮 GeoGuessr режим", "callback_data": "mode_geo"}],
        [{"text": "🔍 OSINT режим", "callback_data": "mode_osint"}],
        [{"text": "💡 Как искать локацию", "callback_data": "hints"}],
    ]}

def back_keyboard():
    return {"inline_keyboard": [[{"text": "◀️ Главное меню", "callback_data": "back"}]]}

def result_keyboard(lat=None, lon=None):
    rows = []
    if lat is not None and lon is not None:
        maps_url = f"https://www.google.com/maps?q={lat},{lon}"
        rows.append([{"text": "🗺️ Открыть на карте", "url": maps_url}])
    rows.append([{"text": "◀️ Главное меню", "callback_data": "back"}])
    return {"inline_keyboard": rows}

# ====
#  OPENROUTER
# ====

def call_openrouter(model, messages, max_tokens=900, timeout=120):
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/vercachev/geooracle-bot",
            "X-Title": "GeoOracle Multi-Agent Debate",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]

# ====
#  КООРДИНАТЫ
# ====

COORDS_RE = re.compile(
    r"COORDS\s*[:：]?\s*\(?\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE
)

def extract_coords(text):
    if not text:
        return None
    m = COORDS_RE.search(text)
    if not m:
        return None
    try:
        lat = float(m.group(1))
        lon = float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return (lat, lon)
    except (ValueError, TypeError):
        pass
    return None

def haversine_km(c1, c2):
    lat1, lon1 = c1
    lat2, lon2 = c2
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def max_disagreement_km(coords_list):
    pts = [c for c in coords_list if c]
    if len(pts) < 2:
        return 0.0
    return max(haversine_km(pts[i], pts[j])
               for i in range(len(pts)) for j in range(i + 1, len(pts)))

def strip_coords_line(text):
    if not text:
        return ""
    lines = [ln for ln in text.splitlines() if not ln.strip().upper().startswith("COORDS")]
    return "\n".join(lines).strip()

# ====
#  EXIF
# ====

def _read_exif_ifd(data, offset, endian):
    tags = {}
    try:
        count = struct.unpack_from(endian + "H", data, offset)[0]
        offset += 2
        for _ in range(count):
            tag, typ, cnt = struct.unpack_from(endian + "HHI", data, offset)
            val_offset = offset + 8
            if typ == 2:
                str_offset = struct.unpack_from(endian + "I", data, val_offset)[0]
                raw = data[str_offset: str_offset + cnt]
                tags[tag] = raw.rstrip(b"\x00").decode("utf-8", errors="replace")
            elif typ == 5:
                r_offset = struct.unpack_from(endian + "I", data, val_offset)[0]
                num, den = struct.unpack_from(endian + "II", data, r_offset)
                tags[tag] = (num, den)
            elif typ == 3 and cnt == 1:
                tags[tag] = struct.unpack_from(endian + "H", data, val_offset)[0]
            offset += 12
    except Exception:
        pass
    return tags

def extract_exif(raw_bytes):
    result = {}
    try:
        if raw_bytes[:2] != b"\xff\xd8":
            return result
        pos = 2
        while pos < len(raw_bytes) - 1:
            marker = raw_bytes[pos:pos+2]
            if marker == b"\xff\xe1":
                length = struct.unpack_from(">H", raw_bytes, pos + 2)[0]
                app1 = raw_bytes[pos + 4: pos + 2 + length]
                if app1[:6] != b"Exif\x00\x00":
                    break
                tiff = app1[6:]
                endian = ">" if tiff[:2] == b"MM" else "<"
                ifd0_offset = struct.unpack_from(endian + "I", tiff, 4)[0]
                ifd0 = _read_exif_ifd(tiff, ifd0_offset, endian)
                if 0x0132 in ifd0:
                    result["datetime"] = ifd0[0x0132]
                if 0x0110 in ifd0:
                    result["camera"] = ifd0[0x0110]
                if 0x010F in ifd0:
                    result["make"] = ifd0[0x010F]
                if 0x8825 in ifd0:
                    gps_offset = ifd0[0x8825]
                    if isinstance(gps_offset, tuple):
                        gps_offset = gps_offset[0]
                    gps = _read_exif_ifd(tiff, gps_offset, endian)
                    def rational_to_deg(tag_id):
                        val = gps.get(tag_id)
                        if not val:
                            return None
                        try:
                            r_offset = val if isinstance(val, int) else val[0]
                            d_n, d_d = struct.unpack_from(endian + "II", tiff, r_offset)
                            m_n, m_d = struct.unpack_from(endian + "II", tiff, r_offset + 8)
                            s_n, s_d = struct.unpack_from(endian + "II", tiff, r_offset + 16)
                            deg = d_n / d_d + (m_n / m_d) / 60 + (s_n / s_d) / 3600
                            return round(deg, 6)
                        except Exception:
                            return None
                    lat = rational_to_deg(2)
                    lon = rational_to_deg(4)
                    lat_ref = gps.get(1, "N")
                    lon_ref = gps.get(3, "E")
                    if lat is not None and lon is not None:
                        if lat_ref == "S":
                            lat = -lat
                        if lon_ref == "W":
                            lon = -lon
                        result["gps_lat"] = lat
                        result["gps_lon"] = lon
                break
            else:
                if pos + 3 >= len(raw_bytes):
                    break
                seg_len = struct.unpack_from(">H", raw_bytes, pos + 2)[0]
                pos += 2 + seg_len
    except Exception as e:
        logger.warning(f"EXIF parse error: {e}")
    return result

def format_exif_for_prompt(exif):
    if not exif:
        return ""
    parts = []
    if "datetime" in exif:
        parts.append(f"Дата/время съёмки: {exif['datetime']}")
    if "make" in exif or "camera" in exif:
        cam = f"{exif.get('make', '')} {exif.get('camera', '')}".strip()
        parts.append(f"Камера: {cam}")
    if "gps_lat" in exif and "gps_lon" in exif:
        parts.append(f"GPS из EXIF: {exif['gps_lat']:.5f}, {exif['gps_lon']:.5f}")
    return "\n".join(parts)

# ====
#  URL ПАРСИНГ
# ====

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

def extract_urls(text):
    if not text:
        return []
    return URL_RE.findall(text)

def fetch_url_context(url, max_chars=1500):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GeoOracle/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(80000)
            charset = "utf-8"
            ct = r.headers.get("Content-Type", "")
            m = re.search(r"charset=([\w-]+)", ct)
            if m:
                charset = m.group(1)
            html = raw.decode(charset, errors="replace")

        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]

        desc = ""
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
                      html, re.IGNORECASE)
        if not m:
            m = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
                          html, re.IGNORECASE)
        if m:
            desc = re.sub(r"\s+", " ", m.group(1)).strip()[:400]

        og_desc = ""
        m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
                      html, re.IGNORECASE)
        if not m:
            m = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']',
                          html, re.IGNORECASE)
        if m:
            og_desc = re.sub(r"\s+", " ", m.group(1)).strip()[:400]

        parts = []
        if title:
            parts.append(f"Заголовок: {title}")
        if desc:
            parts.append(f"Описание: {desc}")
        if og_desc and og_desc != desc:
            parts.append(f"OG-описание: {og_desc}")

        context = "\n".join(parts)
        return context[:max_chars] if context else ""
    except Exception as e:
        logger.warning(f"fetch_url_context failed for {url}: {e}")
        return ""

# ====
#  ПОГОДА
# ====

def get_weather(lat, lon):
    try:
        params = urllib.parse.urlencode({
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": "temperature_2m,weathercode,windspeed_10m,precipitation,is_day",
            "timezone": "auto",
            "forecast_days": 1,
        })
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())

        cur = data.get("current", {})
        tz_abbr = data.get("timezone_abbreviation", "")
        cur_time = cur.get("time", "")
        temp = cur.get("temperature_2m")
        wcode = cur.get("weathercode")
        wind = cur.get("windspeed_10m")
        precip = cur.get("precipitation")
        is_day = cur.get("is_day")

        WMO = {
            0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
            45: "туман", 48: "изморозь",
            51: "лёгкая морось", 53: "морось", 55: "сильная морось",
            61: "лёгкий дождь", 63: "дождь", 65: "сильный дождь",
            71: "лёгкий снег", 73: "снег", 75: "сильный снег",
            80: "ливень", 81: "сильный ливень", 82: "очень сильный ливень",
            95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
        }
        weather_desc = WMO.get(wcode, f"код {wcode}") if wcode is not None else "неизвестно"
        day_night = "день" if is_day else "ночь"

        parts = []
        if cur_time:
            parts.append(f"🕐 Местное время: {cur_time} ({tz_abbr})")
        parts.append(f"🌤️ Погода сейчас: {weather_desc}")
        if temp is not None:
            parts.append(f"🌡️ Температура: {temp}°C")
        if wind is not None:
            parts.append(f"💨 Ветер: {wind} км/ч")
        if precip is not None and precip > 0:
            parts.append(f"🌧️ Осадки: {precip} мм")
        parts.append(f"☀️ Время суток: {day_night}")

        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"get_weather failed: {e}")
        return ""

# ====
#  АГЕНТЫ
# ====

def agent_system_prompt(agent):
    return (
        f"Ты — агент-специалист команды GeoOracle по определению геолокации по фотографии.\n"
        f"{agent['focus']}\n\n"
        "Сосредоточься ИМЕННО на своей зоне ответственности, но дай итоговую догадку о локации.\n\n"
        + OUTPUT_FORMAT
    )

def run_agent(agent, image_b64, mode, others_text=None, round_num=1, extra_context=""):
    sys_prompt = agent_system_prompt(agent)
    if round_num == 1:
        base = (
            "Это скриншот из игры GeoGuessr. " if mode == "geo"
            else "Это фотография для OSINT-анализа. "
        ) + "Проанализируй фото со своей экспертной точки зрения и дай оценку локации."
        if extra_context:
            base += f"\n\n📎 ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ОТ ПОЛЬЗОВАТЕЛЯ:\n{extra_context}\n\nИспользуй этот контекст как дополнительные улики."
        user_text = base
    else:
        user_text = (
            "Это РАУНД ДЕБАТОВ. Ниже — мнения других агентов-специалистов по этому же фото:\n\n"
            f"{others_text}\n\n"
            "Изучи их аргументы. Если они убедительны и противоречат твоей версии — "
            "скорректируй свою оценку. Если уверен в своей версии — отстаивай её с аргументами. "
            "Дай ОБНОВЛЁННУЮ оценку в том же формате."
        )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]

    result = {"agent": agent, "text": None, "coords": None, "error": None}
    try:
        text = call_openrouter(agent["model"], messages, max_tokens=700)
        result["text"] = text
        result["coords"] = extract_coords(text)
    except Exception as e:
        logger.error(f"Agent {agent['name']} ({agent['model']}) failed: {e}")
        result["error"] = str(e)
    return result

def run_round_parallel(image_b64, mode, others_map=None, round_num=1, extra_context=""):
    results = [None] * len(AGENTS)

    def worker(idx, agent):
        others_text = others_map.get(agent["id"]) if others_map else None
        results[idx] = run_agent(agent, image_b64, mode, others_text, round_num, extra_context)

    with ThreadPoolExecutor(max_workers=len(AGENTS)) as ex:
        futures = [ex.submit(worker, i, a) for i, a in enumerate(AGENTS)]
        for f in futures:
            f.result()
    return results

def build_others_map(results):
    others_map = {}
    for agent in AGENTS:
        chunks = []
        for r in results:
            if r["agent"]["id"] == agent["id"]:
                continue
            if r["text"]:
                summary = strip_coords_line(r["text"])
                coords = r["coords"]
                coord_str = f" (координаты: {coords[0]:.4f}, {coords[1]:.4f})" if coords else ""
                chunks.append(
                    f"--- {r['agent']['emoji']} {r['agent']['name']} "
                    f"({r['agent']['role']}){coord_str} ---\n{summary}"
                )
        others_map[agent["id"]] = "\n\n".join(chunks) if chunks else "Другие агенты не дали ответа."
    return others_map

# ====
#  СУДЬЯ
# ====

JUDGE_SYSTEM = (
    "Ты — ГЛАВНЫЙ СУДЬЯ команды GeoOracle по геолокации. "
    "Перед тобой выступили 4 агента-специалиста (архитектура, природа, культура/транспорт, "
    "инфраструктура), которые провели несколько раундов дебатов о локации на фотографии.\n\n"
    "Твоя задача: критически взвесить ВСЕ аргументы, выявить самые надёжные улики, "
    "разрешить противоречия и вынести ОКОНЧАТЕЛЬНЫЙ вердикт о местоположении.\n\n"
    "Отвечай СТРОГО на русском в таком формате:\n\n"
    "🌍 ЛОКАЦИЯ:\n"
    "Страна: [страна]\n"
    "Регион/Город: [регион и город]\n"
    "Район/Улица: [если возможно]\n\n"
    "📊 ИТОГОВАЯ УВЕРЕННОСТЬ: [0-100]%\n\n"
    "⚖️ ОБОСНОВАНИЕ ВЕРДИКТА:\n"
    "[3-5 предложений: какие аргументы агентов оказались решающими и почему, "
    "как разрешены противоречия]\n\n"
    "COORDS: lat, lon\n\n"
    "Строка COORDS обязательна — это финальные численные координаты твоего вердикта."
)

def run_judge(image_b64, all_rounds_text, mode):
    user_text = (
        ("Фото — скриншот GeoGuessr.\n\n" if mode == "geo" else "Фото для OSINT-анализа.\n\n")
        + "Ниже полный протокол дебатов агентов по раундам:\n\n"
        + all_rounds_text
        + "\n\nВынеси окончательный вердикт о локации."
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]
    text = call_openrouter(JUDGE_MODEL, messages, max_tokens=1000)
    return text

def build_protocol(rounds):
    parts = []
    for rnum, results in rounds:
        parts.append(f"═══ РАУНД {rnum} ═══")
        for r in results:
            a = r["agent"]
            if r["text"]:
                coords = r["coords"]
                coord_str = f"\nКоординаты: {coords[0]:.4f}, {coords[1]:.4f}" if coords else ""
                parts.append(
                    f"{a['emoji']} {a['name']} ({a['role']}):\n"
                    f"{strip_coords_line(r['text'])}{coord_str}"
                )
            else:
                parts.append(f"{a['emoji']} {a['name']} ({a['role']}): [нет ответа]")
        parts.append("")
    return "\n".join(parts)

# ====
#  ФОРМАТИРОВАНИЕ
# ====

def short_location(text):
    if not text:
        return "—"
    for ln in text.splitlines():
        if "ЛОКАЦИЯ" in ln.upper():
            val = ln.split(":", 1)[-1].strip()
            if val:
                return val[:80]
    for ln in text.splitlines():
        s = ln.strip()
        if s:
            return s[:80]
    return "—"

def short_confidence(text):
    if not text:
        return None
    m = re.search(r"(\d{1,3})\s*%", text)
    if m:
        return m.group(1)
    return None

def format_debate_summary(rounds):
    final_round = rounds[-1][1]
    lines = ["🗣️ *Дебаты агентов* (итоговые позиции):", ""]
    for r in final_round:
        a = r["agent"]
        if r["text"]:
            loc = short_location(r["text"])
            conf = short_confidence(r["text"])
            conf_str = f" — {conf}%" if conf else ""
            lines.append(f"{a['emoji']} *{a['name']}*: {loc}{conf_str}")
        else:
            lines.append(f"{a['emoji']} *{a['name']}*: ⚠️ недоступен")
    return "\n".join(lines)

# ====
#  ДЕБАТЫ
# ====

def run_debate(image_b64, mode, progress_cb=None, extra_context=""):
    rounds = []

    if progress_cb:
        progress_cb("🧠 *Раунд 1/3*: агенты дают независимые оценки...")
    r1 = run_round_parallel(image_b64, mode, round_num=1, extra_context=extra_context)
    rounds.append((1, r1))

    if progress_cb:
        progress_cb("🔄 *Раунд 2/3*: агенты видят чужие аргументы и корректируют позиции...")
    others_map = build_others_map(r1)
    r2 = run_round_parallel(image_b64, mode, others_map=others_map, round_num=2, extra_context=extra_context)
    rounds.append((2, r2))

    coords2 = [r["coords"] for r in r2]
    disagreement = max_disagreement_km(coords2)
    logger.info(f"Disagreement after round 2: {disagreement:.0f} km")
    if disagreement > 150:
        if progress_cb:
            progress_cb(f"⚡ *Раунд 3/3*: сильные расхождения (~{int(disagreement)} км), финальная корректировка...")
        others_map3 = build_others_map(r2)
        r3 = run_round_parallel(image_b64, mode, others_map=others_map3, round_num=3, extra_context=extra_context)
        rounds.append((3, r3))

    if progress_cb:
        progress_cb("⚖️ *Судья* анализирует все аргументы и выносит вердикт...")
    protocol = build_protocol(rounds)
    if extra_context:
        protocol = f"📎 КОНТЕКСТ ОТ ПОЛЬЗОВАТЕЛЯ:\n{extra_context}\n\n" + protocol
    try:
        verdict = run_judge(image_b64, protocol, mode)
    except Exception as e:
        logger.error(f"Judge failed: {e}")
        verdict = None

    return rounds, verdict

# ====
#  ОБРАБОТКА ФОТО
# ====

def fetch_image_raw(file_id):
    file_info = tg_request("getFile", {"file_id": file_id})
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(file_url, timeout=60) as r:
        raw = r.read()
    return raw, base64.b64encode(raw).decode()

def process_photo(chat_id, file_id, mode, caption=""):
    mode_text = "🎮 GeoGuessr" if mode == "geo" else "🔍 OSINT"
    sent = send_message(chat_id, f"{mode_text} | 👁️ Запускаю Multi-Agent Debate...")
    status_msg_id = sent["result"]["message_id"]

    def progress(text):
        edit_message(chat_id, status_msg_id, f"{mode_text} | {text}")

    try:
        raw_bytes, image_b64 = fetch_image_raw(file_id)
    except Exception as e:
        logger.error(f"fetch image failed: {e}")
        edit_message(chat_id, status_msg_id, "❌ Не удалось загрузить фото. Попробуй ещё раз.")
        return

    # EXIF
    exif = extract_exif(raw_bytes)
    exif_text = format_exif_for_prompt(exif)
    logger.info(f"EXIF extracted: {exif}")

    exif_coords = None
    if "gps_lat" in exif and "gps_lon" in exif:
        exif_coords = (exif["gps_lat"], exif["gps_lon"])

    # URL-контекст из caption
    url_context_parts = []
    if caption:
        urls = extract_urls(caption)
        if urls:
            progress("🔗 Загружаю контекст из ссылок...")
            for url in urls[:2]:
                ctx = fetch_url_context(url)
                if ctx:
                    url_context_parts.append(f"Источник ({url}):\n{ctx}")

    # Собираем extra_context
    extra_parts = []
    if caption:
        clean_caption = URL_RE.sub("", caption).strip()
        if clean_caption:
            extra_parts.append(f"Комментарий пользователя: {clean_caption}")
    if exif_text:
        extra_parts.append(f"EXIF метаданные фото:\n{exif_text}")
    if url_context_parts:
        extra_parts.extend(url_context_parts)
    extra_context = "\n\n".join(extra_parts)

    if exif_coords:
        progress(f"📍 GPS из EXIF: {exif_coords[0]:.5f}, {exif_coords[1]:.5f} — передаю агентам...")

    try:
        rounds, verdict = run_debate(image_b64, mode, progress_cb=progress, extra_context=extra_context)
    except Exception as e:
        logger.error(f"debate failed: {e}")
        edit_message(chat_id, status_msg_id, "❌ Ошибка при анализе. Попробуй ещё раз.")
        return

    try:
        tg_request("deleteMessage", {"chat_id": chat_id, "message_id": status_msg_id})
    except Exception:
        pass

    # Краткая сводка дебатов
    summary = format_debate_summary(rounds)
    try:
        send_message(chat_id, summary)
    except Exception as e:
        logger.error(f"send summary failed: {e}")
        send_message(chat_id, summary, parse_mode=None)

    # Координаты: судья → EXIF → последний раунд
    coords = extract_coords(verdict) if verdict else None
    if not coords and exif_coords:
        coords = exif_coords
    if not coords:
        last_coords = [r["coords"] for r in rounds[-1][1] if r["coords"]]
        if last_coords:
            coords = last_coords[0]

    # Погода
    weather_text = ""
    if coords:
        weather_text = get_weather(coords[0], coords[1])

    if verdict:
        verdict_display = strip_coords_line(verdict)
        final_text = f"👁️ *ФИНАЛЬНЫЙ ВЕРДИКТ ОРАКУЛА*\n\n{verdict_display}"
        if coords:
            final_text += f"\n\n📍 Координаты: `{coords[0]:.5f}, {coords[1]:.5f}`"
        if exif_coords:
            final_text += f"\n📷 GPS из EXIF: `{exif_coords[0]:.5f}, {exif_coords[1]:.5f}`"
        if weather_text:
            final_text += f"\n\n🌍 *Погода в локации прямо сейчас:*\n{weather_text}"
    else:
        final_text = "👁️ *ФИНАЛЬНЫЙ ВЕРДИКТ*\n\n⚠️ Судья не смог вынести вердикт, но агенты высказались выше."
        if weather_text and coords:
            final_text += f"\n\n🌍 *Погода в предполагаемой локации:*\n{weather_text}"

    kb = result_keyboard(coords[0], coords[1]) if coords else main_keyboard()
    try:
        send_message(chat_id, final_text, kb)
    except Exception as e:
        logger.error(f"send verdict failed: {e}")
        send_message(chat_id, final_text, kb, parse_mode=None)

# ====
#  HANDLE UPDATE
# ====

def handle_update(update):
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]

            if "text" in msg and msg["text"].startswith("/start"):
                send_message(
                    chat_id,
                    "👁️ *GeoOracle* — определяю локации по фото\n\n"
                    "🧠 Работает система *Multi-Agent Debate*: 4 ИИ-агента спорят и "
                    "находят локацию, а судья выносит финальный вердикт.\n\n"
                    "Выбери режим работы:",
                    main_keyboard(),
                )

            elif "photo" in msg:
                mode = user_mode.get(chat_id, "osint")
                file_id = msg["photo"][-1]["file_id"]
                caption = msg.get("caption", "")
                process_photo(chat_id, file_id, mode, caption=caption)

            elif "document" in msg:
                doc = msg["document"]
                mime = doc.get("mime_type", "")
                if mime.startswith("image/"):
                    mode = user_mode.get(chat_id, "osint")
                    file_id = doc["file_id"]
                    caption = msg.get("caption", "")
                    send_message(chat_id, "📎 Получил фото как файл — извлеку EXIF метаданные!")
                    process_photo(chat_id, file_id, mode, caption=caption)
                else:
                    send_message(chat_id, "⚠️ Поддерживаются только изображения.")

        elif "callback_query" in update:
            cb = update["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            message_id = cb["message"]["message_id"]
            data = cb["data"]
            answer_callback(cb["id"])

            if data == "mode_geo":
                user_mode[chat_id] = "geo"
                edit_message(chat_id, message_id,
                             "🎮 *GeoGuessr режим*\n\nОтправь скриншот из игры!\n\n📸 Жду фото...",
                             back_keyboard())
            elif data == "mode_osint":
                user_mode[chat_id] = "osint"
                edit_message(chat_id, message_id,
                             "🔍 *OSINT режим*\n\nОтправь любое фото!\n\n📸 Жду фото...",
                             back_keyboard())
            elif data == "hints":
                edit_message(chat_id, message_id, HINTS_TEXT, back_keyboard())
            elif data == "back":
                edit_message(
                    chat_id, message_id,
                    "👁️ *GeoOracle* — определяю локации по фото\n\n"
                    "🧠 Система *Multi-Agent Debate* готова к работе.\n\nВыбери режим работы:",
                    main_keyboard(),
                )

    except Exception as e:
        logger.error(f"Error handling update: {e}")

# ====
#  POLLING + HEALTH CHECK
# ====

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
            time.sleep(5)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    logger.info("Starting GeoOracle bot (Multi-Agent Debate)...")
    server = HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server started")
    poll()
