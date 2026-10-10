"""
Резервные копии SQLite-базы.

- Копирование через sqlite3.Connection.backup() — безопасно при работающем сервере.
- Хранение N последних копий в /data/backups/.
- Автозапуск раз в день (если сегодня копии ещё нет).
"""
import os
import sqlite3
from datetime import datetime, timedelta

# Пути
DB_PATH = os.getenv("DB_PATH", "/data/warehouse.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "/data/backups")
KEEP_COUNT = int(os.getenv("BACKUP_KEEP", "7"))


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def backup_filename(dt=None):
    dt = dt or datetime.now()
    return f"warehouse_{dt:%Y-%m-%d_%H-%M}.db"


def create_backup(tag="manual"):
    """
    Создаёт копию базы. Возвращает путь к файлу или None при ошибке.
    Использует нативный sqlite3.backup() — безопасно при работе сервера.
    """
    if not os.path.exists(DB_PATH):
        print(f"[backup] База {DB_PATH} не найдена")
        return None

    if os.path.getsize(DB_PATH) == 0:
        print(f"[backup] База пустая, копия не создаётся")
        return None

    ensure_backup_dir()

    filename = backup_filename()
    dest = os.path.join(BACKUP_DIR, filename)

    # Если файл с таким именем уже есть (запуск в ту же минуту)
    if os.path.exists(dest):
        return dest

    try:
        src_conn = sqlite3.connect(DB_PATH)
        dst_conn = sqlite3.connect(dest)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
        print(f"[backup] Копия создана: {filename} ({tag})")
        prune_old_backups()
        return dest
    except Exception as e:
        print(f"[backup] Ошибка: {e}")
        return None


def list_backups():
    """
    Возвращает список бэкапов: [(filename, path, size, mtime), ...]
    Сортировка — от новых к старым.
    """
    ensure_backup_dir()
    items = []
    for name in os.listdir(BACKUP_DIR):
        if not name.startswith("warehouse_") or not name.endswith(".db"):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            stat = os.stat(path)
            items.append({
                "filename": name,
                "path": path,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime),
            })
        except OSError:
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def prune_old_backups():
    """Удаляет старые копии, оставляя KEEP_COUNT последних."""
    items = list_backups()
    if len(items) <= KEEP_COUNT:
        return
    for item in items[KEEP_COUNT:]:
        try:
            os.remove(item["path"])
            print(f"[backup] Удалён старый: {item['filename']}")
        except OSError:
            pass


def backup_today_exists():
    """Есть ли копия за сегодня."""
    today = datetime.now().date()
    for item in list_backups():
        if item["mtime"].date() == today:
            return True
    return False


def auto_backup_if_needed():
    """Создаёт копию, если сегодня её ещё не было."""
    if backup_today_exists():
        print("[backup] Копия за сегодня уже есть")
        return None
    return create_backup(tag="auto")
