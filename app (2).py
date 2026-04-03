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

# Загружаем AI-модель один раз при старте сервера (не при каждом запросе)
# u2net_human_seg — лёгкая модель (~170 МБ), работает на бесплатном плане Railway
print("[startup] Загружаем модель rembg (u2net_human_seg)...")
REMBG_SESSION = new_session("u2net_human_seg")
print("[startup] Модель загружена ✓")

# Координаты и размер области фото на шаблоне (в пикселях)
# Измерь в Figma или Photoshop — левый верхний угол области фото
PHOTO_X      = 158   # отступ от левого края шаблона
PHOTO_Y      = 520   # отступ от верхнего края шаблона
PHOTO_W      = 278   # ширина области фото
PHOTO_H      = 278   # высота области фото
CORNER_R     = 18    # радиус скругления углов фото

# Координаты и стиль текста ГОДА
# Год рисуется поверх области — шаблон должен быть без цифр года
YEAR_X       = 628   # координата X начала текста года
YEAR_Y       = 778   # координата Y текста года
YEAR_COLOR   = "#FF8C00"  # оранжевый цвет как в шаблоне
YEAR_FONT_SIZE = 36

# Путь к шаблону (лежит рядом с app.py)
TEMPLATE_PATH = "template.png"

# Путь к шрифту (загрузи нужный шрифт и положи рядом)
FONT_PATH = "font.ttf"

# ─────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────

def remove_background(image_bytes: bytes) -> bytes | None:
    """Убирает фон локально через rembg (бесплатно, без внешних API)"""
    try:
        # Открываем исходное фото
        input_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

        # Убираем фон — всё происходит локально на сервере
        output_img = remove(input_img, session=REMBG_SESSION)

        # Конвертируем обратно в bytes
        out = io.BytesIO()
        output_img.save(out, format="PNG")
        out.seek(0)
        return out.read()
    except Exception as e:
        print(f"[rembg error] {e}")
        return None


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Скругляет углы изображения"""
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1],
                            radius=radius, fill=255)
    img.putalpha(mask)
    return img


def download_photo_by_url(url: str) -> bytes | None:
    """Скачивает фото по прямой ссылке (attachment_url из Salebot)"""
    try:
        resp = requests.get(url, timeout=30)
        return resp.content if resp.status_code == 200 else None
    except Exception as e:
        print(f"[download error] {e}")
        return None


def build_card(photo_bytes: bytes, year: str) -> io.BytesIO | None:
    """Собирает финальную карточку"""

    # 1. Убираем фон
    no_bg = remove_background(photo_bytes)
    if not no_bg:
        return None

    # 2. Открываем фото пользователя и шаблон
    user_img  = Image.open(io.BytesIO(no_bg)).convert("RGBA")
    template  = Image.open(TEMPLATE_PATH).convert("RGBA")

    # 3. Масштабируем фото под размер области
    user_img = user_img.resize((PHOTO_W, PHOTO_H), Image.LANCZOS)

    # 4. Скругляем углы фото
    user_img = round_corners(user_img, CORNER_R)

    # 5. Вставляем фото в шаблон
    template.paste(user_img, (PHOTO_X, PHOTO_Y), user_img)

    # 6. Накладываем год текстом
    draw = ImageDraw.Draw(template)
    try:
        font = ImageFont.truetype(FONT_PATH, YEAR_FONT_SIZE)
    except Exception:
        # Если шрифт не найден — берём системный дефолтный
        font = ImageFont.load_default()
        print("[warn] Шрифт не найден, используется дефолтный")

    draw.text((YEAR_X, YEAR_Y), str(year), fill=YEAR_COLOR, font=font)

    # 7. Конвертируем в JPEG и возвращаем
    output = io.BytesIO()
    template.convert("RGB").save(output, format="JPEG", quality=95)
    output.seek(0)
    return output


def send_photo_to_telegram(chat_id: str | int, photo: io.BytesIO,
                            caption: str = "") -> bool:
    """Отправляет готовое фото пользователю через Telegram Bot API"""
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("card.jpg", photo, "image/jpeg")},
        timeout=30,
    )
    return resp.status_code == 200


def send_message(chat_id: str | int, text: str) -> None:
    """Отправляет текстовое сообщение"""
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=15,
    )


# ─────────────────────────────────────────
#  ОСНОВНОЙ ENDPOINT — вебхук от Salebot
# ─────────────────────────────────────────

@app.route("/process", methods=["POST"])
def process():
    """
    Ожидает JSON от Salebot:
    {
        "chat_id":        "123456789",
        "attachment_url": "https://...",   ← #{attachment_url} из Salebot
        "year":           "2012"
    }
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error", "message": "no json"}), 400

    chat_id        = data.get("chat_id")
    attachment_url = data.get("attachment_url")
    year           = data.get("year", "????")

    if not chat_id or not attachment_url:
        return jsonify({"status": "error", "message": "missing chat_id or attachment_url"}), 400

    # Валидация года
    try:
        year_int = int(year)
        if not (1950 <= year_int <= 2025):
            send_message(chat_id, "⚠️ Укажи корректный год (например: 2015)")
            return jsonify({"status": "error", "message": "invalid year"}), 400
    except ValueError:
        send_message(chat_id, "⚠️ Год должен быть числом (например: 2015)")
        return jsonify({"status": "error", "message": "year not a number"}), 400

    # Сообщаем пользователю что обрабатываем
    send_message(chat_id, "⏳ Создаём твою карточку, подожди несколько секунд...")

    # Скачиваем фото по прямой ссылке от Salebot
    photo_bytes = download_photo_by_url(attachment_url)
    if not photo_bytes:
        send_message(chat_id, "❌ Не удалось загрузить фото. Попробуй ещё раз.")
        return jsonify({"status": "error", "message": "cant download photo"}), 500

    # Собираем карточку
    card = build_card(photo_bytes, year)
    if not card:
        send_message(chat_id, "❌ Ошибка обработки фото. Попробуй загрузить другое фото.")
        return jsonify({"status": "error", "message": "build_card failed"}), 500

    # Отправляем результат
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
