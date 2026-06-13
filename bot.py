import base64
import os
import re
import math
import urllib.request
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
JUDGE_MODEL = "google/gemma-4-31b-it:free"

AGENTS = [
    {
        "id": 1,
        "name": "Архитектор",
        "emoji": "🏛️",
        "role": "Архитектура и городская среда",
        "model": "google/gemma-4-31b-it:free",
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
        "model": "google/gemma-4-26b-a4b:free",
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
        "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
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
        "model": "nvidia/nemotron-nano-12b-v2-vl:free",
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
    "🔍 АРГУМЕНТЫ: [2-4 коротких ключевых наблюдения именно по твоей специализации]\n"
    "COORDS: lat, lon\n\n"
    "ВАЖНО: строка COORDS обязательна и должна содержать численные координаты "
    "(широта, долгота), например: COORDS: 48.8566, 2.3522"
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

def fetch_image_b64(file_id):
    file_info = tg_request("getFile", {"file_id": file_id})
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(file_url, timeout=60) as r:
        return base64.b64encode(r.read()).decode()

COORDS_RE = re.compile(r"COORDS\s*[:：]?\s*\(?\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)", re.IGNORECASE)

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

def agent_system_prompt(agent):
    return (
        f"Ты — агент-специалист команды GeoOracle по определению геолокации по фотографии.\n"
        f"{agent['focus']}\n\n"
        "Сосредоточься ИМЕННО на своей зоне ответственности, но дай итоговую догадку о локации.\n\n"
        + OUTPUT_FORMAT
    )

def run_agent(agent, image_b64, mode, others_text=None, round_num=1):
    sys_prompt = agent_system_prompt(agent)
    if round_num == 1:
        user_text = (
            "Это скриншот из игры GeoGuessr. " if mode == "geo"
            else "Это фотография для OSINT-анализа. "
        ) + "Проанализируй фото со своей экспертной точки зрения и дай оценку локации."
    else:
        user_text = (
            "Это РАУНД ДЕБАТОВ. Ниже — мнения других агентов-специалистов по этому же фото:\n\n"
            f"{others_text}\n\n"
            "Изучи их аргументы. Если они убедительны — скорректируй свою оценку. "
            "Если уверен в своей версии — отстаивай её с аргументами. "
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

def run_round_parallel(image_b64, mode, others_map=None, round_num=1):
    results = [None] * len(AGENTS)
    def worker(idx, agent):
        others_text = others_map.get(agent["id"]) if others_map else None
        results[idx] = run_agent(agent, image_b64, mode, others_text, round_num)
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
                chunks.append(f"--- {r['agent']['emoji']} {r['agent']['name']} ({r['agent']['role']}){coord_str} ---\n{summary}")
        others_map[agent["id"]] = "\n\n".join(chunks) if chunks else "Другие агенты не дали ответа."
    return others_map

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
    "[3-5 предложений: какие аргументы агентов оказались решающими и почему]\n\n"
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
    return call_openrouter(JUDGE_MODEL, messages, max_tokens=1000)

def build_protocol(rounds):
    parts = []
    for rnum, results in rounds:
        parts.append(f"═══ РАУНД {rnum} ═══")
        for r in results:
            a = r["agent"]
            if r["text"]:
                coords = r["coords"]
                coord_str = f"\nКоординаты: {coords[0]:.4f}, {coords[1]:.4f}" if coords else ""
                parts.append(f"{a['emoji']} {a['name']} ({a['role']}):\n{strip_coords_line(r['text'])}{coord_str}")
            else:
                parts.append(f"{a['emoji']} {a['name']} ({a['role']}): [нет ответа]")
        parts.append("")
    return "\n".join(parts)

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
    return m.group(1) if m else None

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

def run_debate(image_b64, mode, progress_cb=None):
    rounds = []

    if progress_cb:
        progress_cb("🧠 *Раунд 1/3*: агенты дают независимые оценки...")
    r1 = run_round_parallel(image_b64, mode, round_num=1)
    rounds.append((1, r1))

    if progress_cb:
        progress_cb("🔄 *Раунд 2/3*: агенты видят чужие аргументы и корректируют позиции...")
    others_map = build_others_map(r1)
    r2 = run_round_parallel(image_b64, mode, others_map=others_map, round_num=2)
    rounds.append((2, r2))

    coords2 = [r["coords"] for r in r2]
    disagreement = max_disagreement_km(coords2)
    logger.info(f"Disagreement after round 2: {disagreement:.0f} km")
    if disagreement > 150:
        if progress_cb:
            progress_cb(f"⚡ *Раунд 3/3*: расхождение ~{int(disagreement)} км, финальная корректировка...")
        others_map3 = build_others_map(r2)
        r3 = run_round_parallel(image_b64, mode, others_map=others_map3, round_num=3)
        rounds.append((3, r3))

    if progress_cb:
        progress_cb("⚖️ *Судья* анализирует все аргументы и выносит вердикт...")
    protocol = build_protocol(rounds)
    try:
        verdict = run_judge(image_b64, protocol, mode)
    except Exception as e:
        logger.error(f"Judge failed: {e}")
        verdict = None

    return rounds, verdict

def process_photo(chat_id, file_id, mode):
    mode_text = "🎮 GeoGuessr" if mode == "geo" else "🔍 OSINT"
    sent = send_message(chat_id, f"{mode_text} | 👁️ Запускаю Multi-Agent Debate...")
    status_msg_id = sent["result"]["message_id"]

    def progress(text):
        edit_message(chat_id, status_msg_id, f"{mode_text} | {text}")

    try:
        image_b64 = fetch_image_b64(file_id)
    except Exception as e:
        logger.error(f"fetch image failed: {e}")
        edit_message(chat_id, status_msg_id, "❌ Не удалось загрузить фото. Попробуй ещё раз.")
        return

    try:
        rounds, verdict = run_debate(image_b64, mode, progress_cb=progress)
    except Exception as e:
        logger.error(f"debate failed: {e}")
        edit_message(chat_id, status_msg_id, "❌ Ошибка при анализе. Попробуй ещё раз.")
        return

    try:
        tg_request("deleteMessage", {"chat_id": chat_id, "message_id": status_msg_id})
    except Exception:
        pass

    summary = format_debate_summary(rounds)
    try:
        send_message(chat_id, summary)
    except Exception as e:
        logger.error(f"send summary failed: {e}")
        send_message(chat_id, summary, parse_mode=None)

    coords = extract_coords(verdict) if verdict else None
    if not coords:
        last_coords = [r["coords"] for r in rounds[-1][1] if r["coords"]]
        if last_coords:
            coords = last_coords[0]

    if verdict:
        verdict_display = strip_coords_line(verdict)
        final_text = f"👁️ *ФИНАЛЬНЫЙ ВЕРДИКТ ОРАКУЛА*\n\n{verdict_display}"
        if coords:
            final_text += f"\n\n📍 Координаты: `{coords[0]:.5f}, {coords[1]:.5f}`"
    else:
        final_text = "👁️ *ФИНАЛЬНЫЙ ВЕРДИКТ*\n\n⚠️ Судья не смог вынести вердикт, но агенты высказались выше."

    kb = result_keyboard(coords[0], coords[1]) if coords else main_keyboard()
    try:
        send_message(chat_id, final_text, kb)
    except Exception as e:
        logger.error(f"send verdict failed: {e}")
        send_message(chat_id, final_text, kb, parse_mode=None)

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
                    "находят локацию, а судья выносит финальный вердикт.\n\nВыбери режим работы:",
                    main_keyboard(),
                )

            elif "photo" in msg:
                mode = user_mode.get(chat_id, "osint")
                file_id = msg["photo"][-1]["file_id"]
                threading.Thread(target=process_photo, args=(chat_id, file_id, mode), daemon=True).start()

            elif "document" in msg:
                doc = msg["document"]
                mime = doc.get("mime_type", "")
                if mime.startswith("image/"):
                    mode = user_mode.get(chat_id, "osint")
                    file_id = doc["file_id"]
                    threading.Thread(target=process_photo, args=(chat_id, file_id, mode), daemon=True).start()

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
                edit_message(
                    chat_id, message_id,
                    "👁️ *GeoOracle* — определяю локации по фото\n\n"
                    "🧠 Система *Multi-Agent Debate* готова к работе.\n\nВыбери режим работы:",
                    main_keyboard(),
                )

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