import base64
import os
import re
import urllib.request
import json
import logging
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(">>> BOT RESTARTING <<<")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

def tg_api(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read())

def handle_update(update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        if "text" in msg and msg["text"] == "/start":
            tg_api("sendMessage", {"chat_id": chat_id, "text": "✅ Бот ожил! Пришли фото, и я его проанализирую."})
        elif "photo" in msg:
            tg_api("sendMessage", {"chat_id": chat_id, "text": "📸 Фото получил! Начинаю анализ..."})
            # Тут будет логика анализа в следующем шаге, когда проверим, что бот дышит

def poll():
    offset = 0
    while True:
        try:
            res = tg_api("getUpdates", {"offset": offset, "timeout": 20})
            for up in res.get("result", []):
                offset = up["update_id"] + 1
                handle_update(up)
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    def run_s():
        class H(BaseHTTPRequestHandler):
            def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()
    threading.Thread(target=run_s, daemon=True).start()
    poll()