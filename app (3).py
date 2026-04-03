import os
import io
import requests
from flask import Flask, request, jsonify
from PIL import Image, ImageDraw, ImageFont
from rembg import remove, new_session

app = Flask(__name__)

# ─────────────────────────────────────────
#  НАСТРОЙКИ — заполни перед деплоем
# ─────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")  # токен бота

# Модель грузится лениво — только при первом запросе, не при старте сервера
# Это позволяет уложиться в 512 МБ Railway
REMBG_SESSION = None

def get_session():
    global REMBG_SESSION
    if REMBG_SESSION is None:
        print("[rembg] Загружаем модель u2net_human_seg...")
        REMBG_SESSION = new_session("u2net_human_seg")
        print("[rembg] Модель загружена ✓")
    return REMBG_SESSION

# Координаты и размер области фото на шаблоне (в пикселях)
PHOTO_X      = 158
PHOTO_Y      = 520
PHOTO_W      = 278
PHOTO_H      = 278
CORNER_R     = 18

# Координаты и стиль текста ГОДА
YEAR_X       = 628
YEAR_Y       = 778
YEAR_COLOR   = "#FF8C00"
YEAR_FONT_SIZE = 36

TEMPLATE_PATH = "template.png"
FONT_PATH = "font.ttf"

# ─────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────

def remove_background(image_bytes: bytes):
    try:
        input_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        output_img = remove(input_img, session=get_session())
        out = io.BytesIO()
        output_img.save(out, format="PNG")
        out.seek(0)
        return out.read()
    except Exception as e:
        print(f"[rembg error] {e}")
        return None


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1],
                            radius=radius, fill=255)
    img.putalpha(mask)
    return img


def download_photo_by_url(url: str):
    try:
        resp = requests.get(url, timeout=30)
        return resp.content if resp.status_code == 200 else None
    except Exception as e:
        print(f"[download error] {e}")
        return None


def build_card(photo_bytes: bytes, year: str):
    no_bg = remove_background(photo_bytes)
    if not no_bg:
        return None

    user_img  = Image.open(io.BytesIO(no_bg)).convert("RGBA")
    template  = Image.open(TEMPLATE_PATH).convert("RGBA")

    user_img = user_img.resize((PHOTO_W, PHOTO_H), Image.LANCZOS)
    user_img = round_corners(user_img, CORNER_R)
    template.paste(user_img, (PHOTO_X, PHOTO_Y), user_img)

    draw = ImageDraw.Draw(template)
    try:
        font = ImageFont.truetype(FONT_PATH, YEAR_FONT_SIZE)
    except Exception:
        font = ImageFont.load_default()
        print("[warn] Шрифт не найден, используется дефолтный")

    draw.text((YEAR_X, YEAR_Y), str(year), fill=YEAR_COLOR, font=font)

    output = io.BytesIO()
    template.convert("RGB").save(output, format="JPEG", quality=95)
    output.seek(0)
    return output


def send_photo_to_telegram(chat_id, photo: io.BytesIO, caption: str = "") -> bool:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("card.jpg", photo, "image/jpeg")},
        timeout=30,
    )
    return resp.status_code == 200


def send_message(chat_id, text: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=15,
    )


# ─────────────────────────────────────────
#  ОСНОВНОЙ ENDPOINT
# ─────────────────────────────────────────

@app.route("/process", methods=["POST"])
def process():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error", "message": "no json"}), 400

    chat_id        = data.get("chat_id")
    attachment_url = data.get("attachment_url")
    year           = data.get("year", "????")

    if not chat_id or not attachment_url:
        return jsonify({"status": "error", "message": "missing chat_id or attachment_url"}), 400

    try:
        year_int = int(year)
        if not (1950 <= year_int <= 2025):
            send_message(chat_id, "⚠️ Укажи корректный год (например: 2015)")
            return jsonify({"status": "error", "message": "invalid year"}), 400
    except ValueError:
        send_message(chat_id, "⚠️ Год должен быть числом (например: 2015)")
        return jsonify({"status": "error", "message": "year not a number"}), 400

    send_message(chat_id, "⏳ Создаём твою карточку, подожди несколько секунд...")

    photo_bytes = download_photo_by_url(attachment_url)
    if not photo_bytes:
        send_message(chat_id, "❌ Не удалось загрузить фото. Попробуй ещё раз.")
        return jsonify({"status": "error", "message": "cant download photo"}), 500

    card = build_card(photo_bytes, year)
    if not card:
        send_message(chat_id, "❌ Ошибка обработки фото. Попробуй загрузить другое фото.")
        return jsonify({"status": "error", "message": "build_card failed"}), 500

    ok = send_photo_to_telegram(
        chat_id, card,
        caption="🎉 Вот твой паспорт RE_PLAY Community!"
    )

    if ok:
        return jsonify({"status": "ok"})
    else:
        return jsonify({"status": "error", "message": "telegram send failed"}), 500


# ─────────────────────────────────────────
#  HEALTHCHECK
# ─────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "alive", "service": "RE_PLAY card bot"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
