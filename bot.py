import base64
import os
import re
import math
import urllib.request
import urllib.error
import json
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set!")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

AGENTS = [
    {
        "id": 1,
        "name": "Архитектор",
        "emoji": "🏛️",
        "model": "anthropic/claude-haiku-4-5",
        "focus": (
            "Ты — эксперт по АРХИТЕКТУРЕ и ГОРОДСКОЙ СРЕДЕ.\n"
            "• Стили зданий, материалы фасадов, типовая застройка по регионам\n"
            "• Крыши, окна, балконы, дымоходы\n"
            "• Планировка улиц, тротуары, бордюры\n"
            "• Советское/европейское/азиатское/американское жильё\n"
            "• Заборы, фонари, городская мебель"
        ),
    },
    {
        "id": 2,
        "name": "Натуралист",
        "emoji": "🌿",
        "model": "google/gemini-2.0-flash-001",
        "focus": (
            "Ты — эксперт по ПРИРОДЕ и ЛАНДШАФТУ.\n"
            "• Тип растительности, породы деревьев\n"
            "• Цвет почвы, рельеф, горы, водоёмы\n"
            "• Климат, сезон, угол солнца → полушарие\n"
            "• Сельхозкультуры, поля\n"
            "• Геологические особенности"
        ),
    },
    {
        "id": 3,
        "name": "Культуролог",
        "emoji": "🚃",
        "model": "anthropic/claude-haiku-4-5",
        "focus": (
            "Ты — эксперт по КУЛЬТУРЕ и ТРАНСПОРТУ.\n"
            "• Письменность на вывесках (кириллица/латиница/иероглифы/арабский)\n"
            "• Номерные знаки, марки авто, сторона движения\n"
            "• Одежда людей, религиозные маркеры\n"
            "• Бренды, реклама, специфичные для региона"
        ),
    },
    {
        "id": 4,
        "name": "Детектив",
        "emoji": "🔍",
        "model": "anthropic/claude-haiku-4-5",
        "focus": (
            "Ты — эксперт по ИНФРАСТРУКТУРЕ и МЕЛКИМ ДЕТАЛЯМ.\n"
            "• Столбы (дырки, форма основания), болларды (цвет, форма)\n"
            "• Дорожная разметка (цвет линий), знаки, светофоры\n"
            "• Электропровода, трансформаторы, люки\n"
            "• Метод Rainbolt: болларды/столбы/разметка как маркеры стран"
        ),
    },
]

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


# ─── Telegram helpers ────────────────────────────────────────────────────────

def tg_request(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    data = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_request("sendMessage", data)


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        return tg_request("editMessageText", data)
    except Exception as e:
        logger.error(f"edit_message error: {e}")


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
    return {"inline_keyboard": [
        [{"text": "◀️ Главное меню", "callback_data": "back"}]
    ]}


def result_keyboard(lat=None, lon=None):
    rows = []
    if lat is not None and lon is not None:
        maps_url = f"https://www.google.com/maps?q={lat},{lon}"
        rows.append([{"text": "🗺️ Открыть на карте", "url": maps_url}])
    rows.append([{"text": "◀️ Главное меню", "callback_data": "back"}])
    return {"inline_keyboard": rows}


# ─── OpenRouter ───────────────────────────────────────────────────────────────

def call_openrouter(model, messages, max_tokens=800, timeout=90):
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
            "X-Title": "GeoOracle",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    choices = data.get("choices")
    if not choices:
        raise ValueError(f"No choices: {data}")
    return choices[0]["message"]["content"]


def fetch_image_b64(file_id):
    file_info = tg_request("getFile", {"file_id": file_id})
    file_path = file_info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return base64.b64encode(r.read()).decode()


# ─── Parsing helpers ──────────────────────────────────────────────────────────

COORDS_RE = re.compile(
    r"COORDS\s*[:：]?\s*\(?\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE,
)
CONF_RE = re.compile(r"(\d{1,3})\s*%")
REGION_RE = re.compile(
    r"РЕГИОН\s*[:：]\s*(.+)", re.IGNORECASE
)


def extract_coords(text):
    if not text:
        return None
    m = COORDS_RE.search(text)
    if not m:
        return None
    try:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return (lat, lon)
    except (ValueError, TypeError):
        pass
    return None


def extract_confidence(text):
    if not text:
        return 0
    m = CONF_RE.search(text)
    return int(m.group(1)) if m else 0


def extract_region(text):
    if not text:
        return None
    m = REGION_RE.search(text)
    return m.group(1).strip() if m else None


def strip_coords_line(text):
    if not text:
        return ""
    lines = [ln for ln in text.splitlines()
             if not ln.strip().upper().startswith("COORDS")]
    return "\n".join(lines).strip()


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


# ─── Geo math ─────────────────────────────────────────────────────────────────

def haversine_km(c1, c2):
    lat1, lon1 = c1
    lat2, lon2 = c2
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def weighted_centroid(points):
    """
    points: list of (lat, lon, weight)
    Возвращает взвешенный центроид.
    """
    total_w = sum(w for _, _, w in points)
    if total_w == 0:
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        return (sum(lats) / len(lats), sum(lons) / len(lons))
    lat = sum(la * w for la, _, w in points) / total_w
    lon = sum(lo * w for _, lo, w in points) / total_w
    return (lat, lon)


def remove_outliers(results):
    """
    Убираем агентов чьи координаты > 500 км от медианной точки остальных.
    """
    valid = [(r, r["coords"]) for r in results if r["coords"]]
    if len(valid) < 3:
        return results  # недостаточно точек для фильтрации

    # Считаем центроид всех
    pts = [(c[0], c[1], 1) for _, c in valid]
    center = weighted_centroid(pts)

    filtered = []
    outliers = []
    for r, c in valid:
        dist = haversine_km(center, c)
        if dist > 500:
            outliers.append(r)
            logger.info(f"Outlier: {r['agent']['name']} at {c} ({dist:.0f} km from center)")
        else:
            filtered.append(r)

    # Если все выброшены — возвращаем оригинал
    if not filtered:
        return results

    # Добавляем агентов без координат обратно
    no_coords = [r for r in results if not r["coords"]]
    return filtered + no_coords


# ─── Stage 1: Region detection ────────────────────────────────────────────────

REGION_SYSTEM = (
    "Ты — быстрый геолокатор. Твоя задача: по фотографии определить "
    "КОНТИНЕНТ и РЕГИОН мира как можно точнее.\n\n"
    "Отвечай СТРОГО в формате:\n"
    "КОНТИНЕНТ: [Европа/Азия/Америка/Африка/Океания]\n"
    "РЕГИОН: [например: Восточная Европа, Юго-Восточная Азия, Южная Америка...]\n"
    "СТРАНА (если очевидно): [страна или 'неизвестно']\n"
    "УВЕРЕННОСТЬ: [0-100]%\n\n"
    "Только эти 4 строки, ничего лишнего."
)


def detect_region(image_b64, mode):
    user_text = (
        "Это скриншот из GeoGuessr." if mode == "geo"
        else "Это фото для геолокации."
    ) + " Определи континент и регион."
    messages = [
        {"role": "system", "content": REGION_SYSTEM},
        {"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]
    try:
        # Используем быструю модель для первого этапа
        return call_openrouter("google/gemini-2.0-flash-001", messages,
                               max_tokens=150, timeout=30)
    except Exception as e:
        logger.error(f"Region detection failed: {e}")
        return None


# ─── Stage 2: Agent analysis ──────────────────────────────────────────────────

AGENT_OUTPUT_FORMAT = (
    "\n\nОтвечай СТРОГО на русском в формате:\n"
    "🌍 ЛОКАЦИЯ: [страна, регион, город]\n"
    "📊 УВЕРЕННОСТЬ: [0-100]%\n"
    "🔍 АРГУМЕНТЫ: [2-4 наблюдения по твоей специализации]\n"
    "COORDS: lat, lon\n\n"
    "Строка COORDS обязательна с числовыми координатами."
)


def run_agent(agent, image_b64, mode, region_hint=None, prev_results=None, round_num=1):
    sys_prompt = agent["focus"] + AGENT_OUTPUT_FORMAT

    if round_num == 1:
        hint = ""
        if region_hint:
            hint = f"\n\nПодсказка от быстрого анализа: {region_hint}\nИспользуй это как отправную точку."
        user_text = (
            ("Скриншот GeoGuessr. " if mode == "geo" else "Фото для OSINT. ")
            + "Проанализируй со своей экспертной точки зрения." + hint
        )
    else:
        others = []
        for r in (prev_results or []):
            if r["agent"]["id"] == agent["id"] or not r["text"]:
                continue
            loc = short_location(r["text"])
            conf = extract_confidence(r["text"])
            coords = r["coords"]
            coord_str = f" ({coords[0]:.3f}, {coords[1]:.3f})" if coords else ""
            others.append(
                f"{r['agent']['emoji']} {r['agent']['name']}: {loc} — {conf}%{coord_str}"
            )
        others_text = "\n".join(others) if others else "нет данных"
        user_text = (
            f"РАУНД 2. Мнения других агентов:\n{others_text}\n\n"
            "Изучи их позиции. Скорректируй свою оценку если аргументы убедительны, "
            "или отстаивай свою версию. Дай обновлённую оценку."
        )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]

    result = {"agent": agent, "text": None, "coords": None,
              "confidence": 0, "error": None}
    try:
        text = call_openrouter(agent["model"], messages, max_tokens=600, timeout=90)
        result["text"] = text
        result["coords"] = extract_coords(text)
        result["confidence"] = extract_confidence(text)
    except Exception as e:
        logger.error(f"Agent {agent['name']} failed: {e}")
        result["error"] = str(e)
    return result


def run_agents_parallel(image_b64, mode, region_hint=None,
                        prev_results=None, round_num=1):
    results = [None] * len(AGENTS)

    def worker(idx, agent):
        results[idx] = run_agent(
            agent, image_b64, mode, region_hint, prev_results, round_num
        )

    with ThreadPoolExecutor(max_workers=len(AGENTS)) as ex:
        futures = [ex.submit(worker, i, a) for i, a in enumerate(AGENTS)]
        for f in futures:
            f.result()
    return results


# ─── Consensus calculation ────────────────────────────────────────────────────

def calculate_consensus(results):
    """
    1. Убираем выбросы (>500 км от центра)
    2. Считаем взвешенный центроид по уверенности
    3. Возвращаем финальные координаты и метаданные
    """
    filtered = remove_outliers(results)
    valid = [(r, r["coords"]) for r in filtered if r["coords"]]

    if not valid:
        return None, results, []

    outlier_names = [r["agent"]["name"] for r in results
                     if r["coords"] and r not in filtered]

    points = [(c[0], c[1], max(r["confidence"], 1)) for r, c in valid]
    centroid = weighted_centroid(points)

    return centroid, filtered, outlier_names


def build_summary(results, centroid, outlier_names, round_label=""):
    lines = []
    if round_label:
        lines.append(f"*{round_label}*\n")

    for r in results:
        a = r["agent"]
        if r["text"]:
            loc = short_location(r["text"])
            conf = r["confidence"]
            is_outlier = a["name"] in outlier_names
            outlier_mark = " ⚠️ выброс" if is_outlier else ""
            lines.append(f"{a['emoji']} *{a['name']}*: {loc} — {conf}%{outlier_mark}")
        else:
            lines.append(f"{a['emoji']} *{a['name']}*: ❌ недоступен")

    if centroid:
        lines.append(
            f"\n📍 *Консенсус*: `{centroid[0]:.5f}, {centroid[1]:.5f}`"
        )
        if outlier_names:
            lines.append(f"_(исключены как выбросы: {', '.join(outlier_names)})_")

    return "\n".join(lines)


# ─── Main analysis pipeline ───────────────────────────────────────────────────

def analyze_photo(chat_id, file_id, mode):
    mode_text = "🎮 GeoGuessr" if mode == "geo" else "🔍 OSINT"

    sent = send_message(
        chat_id,
        f"{mode_text} | 🔎 Этап 1/3: определяю регион...",
        parse_mode=None,
    )
    status_id = sent["result"]["message_id"]

    def progress(text):
        edit_message(chat_id, status_id, f"{mode_text} | {text}")

    # Загружаем фото
    try:
        image_b64 = fetch_image_b64(file_id)
    except Exception as e:
        logger.error(f"fetch image failed: {e}")
        edit_message(chat_id, status_id,
                     "❌ Не удалось загрузить фото. Попробуй ещё раз.")
        return

    # ── Этап 1: быстрое определение региона ──
    region_hint = detect_region(image_b64, mode)
    logger.info(f"Region hint: {region_hint}")

    # ── Этап 2: параллельный анализ агентов ──
    progress("🧠 Этап 2/3: агенты анализируют фото...")
    r1 = run_agents_parallel(image_b64, mode, region_hint=region_hint, round_num=1)

    centroid1, filtered1, outliers1 = calculate_consensus(r1)
    max_conf = max((r["confidence"] for r in r1 if r["text"]), default=0)

    # ── Этап 3: второй раунд если уверенность низкая ──
    final_results = r1
    round_label = "Раунд 1"

    if max_conf < 60:
        progress(f"🔄 Этап 3/3: уверенность {max_conf}% — запускаю дебаты...")
        r2 = run_agents_parallel(
            image_b64, mode, region_hint=region_hint,
            prev_results=r1, round_num=2
        )
        centroid2, filtered2, outliers2 = calculate_consensus(r2)

        # Берём раунд с лучшей максимальной уверенностью
        max_conf2 = max((r["confidence"] for r in r2 if r["text"]), default=0)
        if max_conf2 >= max_conf:
            final_results = r2
            centroid1 = centroid2
            outliers1 = outliers2
            round_label = "Раунд 2 (после дебатов)"
        else:
            round_label = "Раунд 1 (дебаты не улучшили)"
    else:
        progress("✅ Этап 3/3: высокая уверенность, финализирую...")

    # Удаляем статус
    try:
        tg_request("deleteMessage", {"chat_id": chat_id, "message_id": status_id})
    except Exception:
        pass

    # ── Отправляем результат ──
    summary = build_summary(final_results, centroid1, outliers1, round_label)

    # Лучший агент для детального ответа
    best = max(
        (r for r in final_results if r["text"]),
        key=lambda r: r["confidence"],
        default=None,
    )

    if best:
        detail = strip_coords_line(best["text"])
        a = best["agent"]
        final_text = (
            f"{summary}\n\n"
            f"─────────────────\n"
            f"👁️ *Лучший анализ* ({a['emoji']} {a['name']}, {best['confidence']}%):\n\n"
            f"{detail}"
        )
    else:
        final_text = summary + "\n\n❌ Все агенты недоступны."

    coords = centroid1
    kb = result_keyboard(coords[0], coords[1]) if coords else main_keyboard()

    try:
        send_message(chat_id, final_text, kb)
    except Exception as e:
        logger.error(f"send failed (md): {e}")
        try:
            send_message(chat_id, final_text, kb, parse_mode=None)
        except Exception as e2:
            logger.error(f"send failed (plain): {e2}")


# ─── Update handler ───────────────────────────────────────────────────────────

def handle_update(update):
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]

            if "text" in msg and msg["text"].startswith("/start"):
                send_message(
                    chat_id,
                    "👁️ *GeoOracle* — определяю локации по фото\n\n"
                    "🧠 Система: двухэтапный анализ + взвешенный консенсус координат\n\n"
                    "Выбери режим:",
                    main_keyboard(),
                )

            elif "photo" in msg:
                mode = user_mode.get(chat_id, "osint")
                file_id = msg["photo"][-1]["file_id"]
                threading.Thread(
                    target=analyze_photo, args=(chat_id, file_id, mode), daemon=True
                ).start()

            elif "document" in msg:
                doc = msg["document"]
                if doc.get("mime_type", "").startswith("image/"):
                    mode = user_mode.get(chat_id, "osint")
                    threading.Thread(
                        target=analyze_photo,
                        args=(chat_id, doc["file_id"], mode),
                        daemon=True,
                    ).start()

        elif "callback_query" in update:
            cb = update["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            message_id = cb["message"]["message_id"]
            data = cb["data"]
            answer_callback(cb["id"])

            if data == "mode_geo":
                user_mode[chat_id] = "geo"
                edit_message(
                    chat_id, message_id,
                    "🎮 GeoGuessr режим\n\nОтправь скриншот из игры!\n\n📸 Жду фото...",
                    back_keyboard(),
                )
            elif data == "mode_osint":
                user_mode[chat_id] = "osint"
                edit_message(
                    chat_id, message_id,
                    "🔍 OSINT режим\n\nОтправь любое фото!\n\n📸 Жду фото...",
                    back_keyboard(),
                )
            elif data == "hints":
                edit_message(chat_id, message_id, HINTS_TEXT, back_keyboard())
            elif data == "back":
                edit_message(
                    chat_id, message_id,
                    "👁️ GeoOracle — определяю локации по фото\n\n"
                    "🧠 Двухэтапный анализ + взвешенный консенсус\n\nВыбери режим:",
                    main_keyboard(),
                )

    except Exception as e:
        logger.error(f"handle_update error: {e}")


# ─── Polling ──────────────────────────────────────────────────────────────────

def poll():
    offset = 0
    logger.info("Starting polling...")

    try:
        tg_request("deleteWebhook", {"drop_pending_updates": True})
        logger.info("Webhook deleted, pending updates dropped")
    except Exception as e:
        logger.warning(f"deleteWebhook failed: {e}")

    while True:
        try:
            url = (f"https://api.telegram.org/bot{BOT_TOKEN}"
                   f"/getUpdates?offset={offset}&timeout=30")
            with urllib.request.urlopen(url, timeout=40) as r:
                data = json.loads(r.read())
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                threading.Thread(
                    target=handle_update, args=(update,), daemon=True
                ).start()
        except urllib.error.HTTPError as e:
            if e.code == 409:
                logger.warning("409 Conflict — another instance polling. Waiting 10s...")
                time.sleep(10)
            else:
                logger.error(f"Polling HTTP error: {e}")
                time.sleep(5)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)


# ─── Health check server ──────────────────────────────────────────────────────

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