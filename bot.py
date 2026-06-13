import base64
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

MODELS = [
    {"name": "Claude", "emoji": "🧠", "model": "anthropic/claude-sonnet-4-5"},
    {"name": "Gemini", "emoji": "✨", "model": "google/gemini-2.0-flash-001"},
    {"name": "Qwen",   "emoji": "🔍", "model": "qwen/qwen2.5-vl-72b-instruct"},
]

SYSTEM_PROMPT = """Ты эксперт по геолокации. Определи место по фото.
Отвечай СТРОГО на русском языке:
🌍 ЛОКАЦИЯ: [Страна, Город]
📊 УВЕРЕННОСТЬ: [0-100]%
🔍 УЛИКИ: [Кратко перечисли основные зацепки]
COORDS: lat, lon"""

# --- UTILS ---
def tg_api(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())

def extract_coords(text):
    if not text: return None
    m = re.search(r"COORDS\s*[:：]\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", text, re.IGNORECASE)
    if m:
        try: return (float(m.group(1)), float(m.group(2)))
        except: return None
    return None

def call_model(m_info, img_b64):
    payload = {
        "model": m_info["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "Где это снято?"}
            ]}
        ]
    }
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", 
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=50) as r:
            content = json.loads(r.read())["choices"][0]["message"]["content"]
            return {"name": m_info["name"], "emoji": m_info["emoji"], "text": content, "coords": extract_coords(content)}
    except Exception as e:
        logger.error(f"Error {m_info['name']}: {e}")
        return None

# --- LOGIC ---
def analyze(chat_id, file_id):
    status = tg_api("sendMessage", {"chat_id": chat_id, "text": "🛰 *Запускаю консенсус-анализ 3-х нейросетей...*", "parse_mode": "Markdown"})
    
    try:
        f_info = tg_api("getFile", {"file_id": file_id})
        f_path = f_info["result"]["file_path"]
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f_path}") as r:
            img_b64 = base64.b64encode(r.read()).decode()

        with ThreadPoolExecutor(max_workers=3) as ex:
            results = list(filter(None, ex.map(lambda m: call_model(m, img_b64), MODELS)))

        if not results:
            tg_api("editMessageText", {"chat_id": chat_id, "message_id": status["result"]["message_id"], "text": "❌ Ошибка: нейросети долго не отвечали. Попробуй еще раз."})
            return

        # Берем текст от лучшей модели (первой ответившей)
        main_res = results[0]
        clean_text = main_res["text"].split("COORDS:")[0].strip()
        
        # Считаем средние координаты (Консенсус)
        all_coords = [r["coords"] for r in results if r["coords"]]
        final_coords = None
        if all_coords:
            avg_lat = sum(c[0] for c in all_coords) / len(all_coords)
            avg_lon = sum(c[1] for c in all_coords) / len(all_coords)
            final_coords = (avg_lat, avg_lon)

        # Собираем статусную строку
        status_line = " | ".join([f"{r['emoji']} ✅" for r in results])
        final_text = f"{status_line}\n\n{clean_text}"
        
        # Кнопки
        kb = {"inline_keyboard": []}
        if final_coords:
            kb["inline_keyboard"].append([{"text": "🗺 Открыть на карте (Консенсус)", "url": f"https://www.google.com/maps?q={final_coords[0]},{final_coords[1]}"}])
        kb["inline_keyboard"].append([{"text": "🔄 Перепроверить другое фото", "callback_data": "start"}])

        tg_api("deleteMessage", {"chat_id": chat_id, "message_id": status["result"]["message_id"]})
        tg_api("sendMessage", {"chat_id": chat_id, "text": final_text, "reply_markup": kb, "parse_mode": "Markdown"})

    except Exception as e:
        logger.exception("Fail")
        tg_api("sendMessage", {"chat_id": chat_id, "text": f"❌ Произошла ошибка при анализе."})

def handle_update(up):
    if "message" in up:
        msg = up["message"]
        chat_id = msg["chat"]["id"]
        if "text" in msg and msg["text"] == "/start":
            tg_api("sendMessage", {"chat_id": chat_id, "text": "📸 Пришли фото места, и я определю его локацию с помощью 3-х систем ИИ."})
        elif "photo" in msg:
            threading.Thread(target=analyze, args=(chat_id, msg["photo"][-1]["file_id"])).start()
    elif "callback_query" in up:
        chat_id = up["callback_query"]["message"]["chat"]["id"]
        tg_api("sendMessage", {"chat_id": chat_id, "text": "📸 Жду новое фото!"})

def poll():
    offset = 0
    while True:
        try:
            res = tg_api("getUpdates", {"offset": offset, "timeout": 20})
            for up in res.get("result", []):
                offset = up["update_id"] + 1
                handle_update(up)
        except: time.sleep(5)

if __name__ == "__main__":
    def run_h():
        from http.server import HTTPServer, BaseHTTPRequestHandler
        class H(BaseHTTPRequestHandler):
            def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()
    threading.Thread(target=run_h, daemon=True).start()
    poll()