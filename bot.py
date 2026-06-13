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
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Топ-3 модели для консенсуса
MODELS = [
    {"id": "claude", "name": "Claude 3.5 Sonnet", "emoji": "🧠", "model": "anthropic/claude-sonnet-4-5"},
    {"id": "gemini", "name": "Gemini 2.0 Flash", "emoji": "✨", "model": "google/gemini-2.0-flash-001"},
    {"id": "qwen",   "name": "Qwen 2.5 VL",      "emoji": "🔍", "model": "qwen/qwen2.5-vl-72b-instruct"},
]

SYSTEM_PROMPT = """Ты эксперт по визуальной геолокации. Твоя задача — найти место по фото.
Определяй страну, город, архитектурные особенности, дорожные знаки и растительность.

Отвечай СТРОГО в формате:
🌍 ЛОКАЦИЯ: [Страна, Город]
📊 УВЕРЕННОСТЬ: [0-100]%
🔍 АНАЛИЗ: [3-4 предложения с уликами по методу Rainbolt]
COORDS: lat, lon

Всегда заканчивай ответ строкой COORDS: широта, долгота."""

user_mode = {}

# --- Вспомогательные функции ---

def haversine_km(c1, c2):
    lat1, lon1 = c1
    lat2, lon2 = c2
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * 
         math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def extract_coords(text):
    if not text: return None
    m = re.search(r"COORDS\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", text, re.IGNORECASE)
    if m:
        try: return (float(m.group(1)), float(m.group(2)))
        except: return None
    return None

def extract_conf(text):
    if not text: return 0
    m = re.search(r"(\d{1,3})\s*%", text)
    return int(m.group(1)) if m else 50

# --- Telegram API ---

def tg_api(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())

def send_msg(chat_id, text, kb=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "reply_markup": kb}
    return tg_api("sendMessage", data)

# --- Основная логика ---

def call_model(model_info, img_b64):
    payload = {
        "model": model_info["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "Где это фото было сделано?"}
            ]}
        ]
    }
    req = urllib.request.Request(OPENROUTER_URL, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
            text = res["choices"][0]["message"]["content"]
            return {"name": model_info["name"], "emoji": model_info["emoji"], "text": text, 
                    "coords": extract_coords(text), "conf": extract_conf(text)}
    except:
        return {"name": model_info["name"], "emoji": model_info["emoji"], "text": None, "coords": None, "conf": 0}

def analyze_photo(chat_id, file_id):
    # Уведомление о начале
    status = send_msg(chat_id, "⌛ Три нейросети начали совместный поиск...")
    status_id = status["result"]["message_id"]

    # Качаем фото
    f_info = tg_api("getFile", {"file_id": file_id})
    f_path = f_info["result"]["file_path"]
    f_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f_path}"
    with urllib.request.urlopen(f_url) as r:
        img_b64 = base64.b64encode(r.read()).decode()

    # Запускаем модели параллельно
    results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(lambda m: call_model(m, img_b64), MODELS))

    # Удаляем статус
    tg_api("deleteMessage", {"chat_id": chat_id, "message_id": status_id})

    # Математика консенсуса
    valid_coords = [r for r in results if r["coords"]]
    final_coords = None
    summary = []
    
    for r in results:
        status_icon = "✅" if r["text"] else "❌"
        summary.append(f"{r['emoji']} {r['name']}: {status_icon}")

    if valid_coords:
        # Взвешенное среднее
        lats = [r["coords"][0] * (r["conf"]/100) for r in valid_coords]
        lons = [r["coords"][1] * (r["conf"]/100) for r in valid_coords]
        weights = [r["conf"]/100 for r in valid_coords]
        final_coords = (sum(lats)/sum(weights), sum(lons)/sum(weights))

    # Главный ответ (от самой уверенной модели)
    best_res = max(results, key=lambda x: x["conf"] if x["text"] else -1)
    
    header = " | ".join(summary)
    main_text = best_res["text"].split("COORDS:")[0] if best_res["text"] else "Ошибка анализа."
    
    full_resp = f"{header}\n\n{main_text}"
    
    kb = None
    if final_coords:
        maps_url = f"https://www.google.com/maps?q={final_coords[0]},{final_coords[1]}"
        kb = {"inline_keyboard": [[{"text": "🗺 Открыть консенсус-карту", "url": maps_url}]]}

    send_msg(chat_id, full_resp, kb)

# --- Обработка команд ---

def handle_update(update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        if "photo" in msg:
            analyze_photo(chat_id, msg["photo"][-1]["file_id"])
        elif "text" in msg and msg["text"] == "/start":
            send_msg(chat_id, "Привет! Пришли мне фото, и я найду его на карте с помощью трех нейросетей.")

# --- Поллинг ---

def poll():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            with urllib.request.urlopen(url, timeout=40) as r:
                data = json.loads(r.read())
                for up in data.get("result", []):
                    offset = up["update_id"] + 1
                    handle_update(up)
        except: time.sleep(5)

class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever).start()
    poll()