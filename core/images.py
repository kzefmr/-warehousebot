"""
Обработка фото инструментов.
Уменьшает до 320×240 и сохраняет как JPEG ~50 КБ.
"""
import os
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    Image = None


UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads/tools")
MAX_SIZE = (320, 240)
JPEG_QUALITY = 80


def ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_tool_photo(tool_id: int, file_bytes: bytes) -> str:
    """
    Сохраняет фото инструмента, уменьшая до 320×240.
    Возвращает имя файла (например, '12.jpg').
    """
    if Image is None:
        raise RuntimeError("Pillow не установлен")

    ensure_upload_dir()

    img = Image.open(BytesIO(file_bytes))

    # Конвертируем в RGB (если PNG с прозрачностью или другое)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Уменьшаем с сохранением пропорций
    img.thumbnail(MAX_SIZE, Image.LANCZOS)

    filename = f"{tool_id}.jpg"
    path = os.path.join(UPLOAD_DIR, filename)

    # Сохраняем с качеством 80 → ~30-50 КБ
    img.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)

    return filename


def delete_tool_photo(filename: str):
    if not filename:
        return
    path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(path):
        os.remove(path)


def get_photo_path(filename: str) -> str:
    return os.path.join(UPLOAD_DIR, filename)
