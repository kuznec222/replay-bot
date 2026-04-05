import os
import io
import requests
from flask import Flask, request, jsonify
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

PHOTO_X        = 360
PHOTO_Y        = 747
PHOTO_W        = 429
PHOTO_H        = 356
CORNER_R       = 22
YEAR_X         = 1127
YEAR_Y         = 1135
YEAR_COLOR     = "#FF8C00"
YEAR_FONT_SIZE = 63
TEMPLATE_PATH  = "template.png"
FONT_PATH      = "font.ttf"


def download_photo(url):
    try:
        resp = requests.get(url, timeout=30)
        return resp.content if resp.status_code == 200 else None
    except Exception as e:
        print(f"[download error] {e}")
        return None


def build_card(photo_bytes, year):
    try:
        user_photo = Image.open(io.BytesIO(photo_bytes)).convert('RGB')
        w, h = user_photo.size
        target_ratio = PHOTO_W / PHOTO_H
        current_ratio = w / h
        if current_ratio > target_ratio:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            user_photo = user_photo.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            user_photo = user_photo.crop((0, top, w, top + new_h))
        user_photo = user_photo.resize((PHOTO_W, PHOTO_H), Image.LANCZOS)
        user_rgba = user_photo.convert('RGBA')
        mask = Image.new('L', (PHOTO_W, PHOTO_H), 0)
        dm = ImageDraw.Draw(mask)
        dm.rounded_rectangle([0, 0, PHOTO_W-1, PHOTO_H-1], radius=CORNER_R, fill=255)
        user_rgba.putalpha(mask)
        template = Image.open(TEMPLATE_PATH).convert('RGBA')
        template.paste(user_rgba, (PHOTO_X, PHOTO_Y), user_rgba)
        draw = ImageDraw.Draw(template)
        try:
            font = ImageFont.truetype(FONT_PATH, YEAR_FONT_SIZE)
        except Exception:
            font = ImageFont.load_default()
        draw.text((YEAR_X, YEAR_Y), str(year), fill=YEAR_COLOR, font=font)
        output = io.BytesIO()
        template.convert('RGB').save(output, format='JPEG', quality=95)
        output.seek(0)
        return output
    except Exception as e:
        print(f"[build_card error] {e}")
        return None


def send_photo(chat_id, photo, caption=""):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("card.jpg", photo, "image/jpeg")},
        timeout=30,
    )
    return resp.status_code == 200


def send_message(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=15,
    )


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "alive", "service": "RE_PLAY card bot"})


@app.route("/debug", methods=["GET"])
def debug():
    try:
        from PIL import Image
        img = Image.open(TEMPLATE_PATH)
        return jsonify({
            "template_size": f"{img.size[0]}x{img.size[1]}",
            "photo_x": PHOTO_X,
            "photo_y": PHOTO_Y,
            "photo_w": PHOTO_W,
            "photo_h": PHOTO_H,
            "year_x": YEAR_X,
            "year_y": YEAR_Y
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/process", methods=["POST"])
def process():
    data = request.get_json(force=True, silent=True) or request.form.to_dict() or request.args.to_dict()
    if not data:
        return jsonify({"status": "error", "message": "no json"}), 400

    chat_id        = data.get("chat_id")
    attachment_url = data.get("attachment_url")
    year           = data.get("year", "????")

    if not chat_id or not attachment_url:
        return jsonify({"status": "error", "message": "missing fields"}), 400

    try:
        year_int = int(str(year).strip())
        if not (1950 <= year_int <= 2026):
            send_message(chat_id, "⚠️ Введи корректный год, например: 2015")
            return jsonify({"status": "error"}), 400
    except ValueError:
        send_message(chat_id, "⚠️ Год должен быть числом, например: 2015")
        return jsonify({"status": "error"}), 400

    photo_bytes = download_photo(attachment_url)
    if not photo_bytes:
        send_message(chat_id, "❌ Не удалось загрузить фото. Попробуй ещё раз.")
        return jsonify({"status": "error"}), 500

    card = build_card(photo_bytes, year_int)
    if not card:
        send_message(chat_id, "❌ Ошибка обработки. Попробуй другое фото.")
        return jsonify({"status": "error"}), 500

    ok = send_photo(chat_id, card, caption="🎉 Твой паспорт RE_PLAY Community!")
    if ok:
        return jsonify({"status": "ok"})
    else:
        return jsonify({"status": "error", "message": "telegram send failed"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
