"""
Генерация QR-кода и парсинг префиксов.

Формат кодов:
  M47  → материал, catalog.id = 47
  T12  → инструмент, tools.id = 12
  K3   → ключ, lock_key.id = 3 (зарезервировано)
"""
from io import BytesIO

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
except ImportError:
    qrcode = None


def make_qr_png(text: str, size: int = 8) -> bytes:
    """Возвращает PNG с QR-кодом."""
    if qrcode is None:
        raise RuntimeError("Установите qrcode[pil]")

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=size,
        border=2,
    )
    qr.add_data(str(text))
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def parse_qr_code(code: str):
    """
    Парсит код, возвращает (entity_type, entity_id) или (None, None).
    Поддерживает:
      M47  → ('material', 47)
      T12  → ('tool', 12)
      K3   → ('key', 3)
      47   → ('legacy', 47) — устаревший формат (просто число)
    """
    if not code:
        return (None, None)

    code = code.strip().upper()

    if code.startswith("M"):
        try:
            return ("material", int(code[1:]))
        except ValueError:
            return (None, None)

    if code.startswith("T"):
        try:
            return ("tool", int(code[1:]))
        except ValueError:
            return (None, None)

    if code.startswith("K"):
        try:
            return ("key", int(code[1:]))
        except ValueError:
            return (None, None)

    # Старый формат — просто число
    try:
        return ("legacy", int(code))
    except ValueError:
        return (None, None)


def make_material_code(catalog_id: int) -> str:
    return f"M{catalog_id}"


def make_tool_code(tool_id: int) -> str:
    return f"T{tool_id}"


def make_key_code(key_id: int) -> str:
    return f"K{key_id}"
