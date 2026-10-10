"""
Создаёт warehouse.db с нуля и добавляет админа.
Запуск: python scripts/make_admin.py
"""
import os
import sqlite3
import sys
import getpass

try:
    import bcrypt
except ImportError:
    print("Установи bcrypt: pip install bcrypt")
    sys.exit(1)


DB_PATH = os.getenv("DB_PATH", "warehouse.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS cities (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS warehouses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    city_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    address     TEXT,
    room        TEXT,
    responsible TEXT
);

CREATE TABLE IF NOT EXISTS locations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id INTEGER NOT NULL,
    name         TEXT NOT NULL,
    UNIQUE(warehouse_id, name)
);

CREATE TABLE IF NOT EXISTS catalog (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id  INTEGER NOT NULL,
    article       TEXT DEFAULT '',
    name          TEXT NOT NULL,
    short_name    TEXT,
    segment       TEXT,
    applicability TEXT,
    unit          TEXT DEFAULT 'шт',
    location_id   INTEGER,
    sort_order    INTEGER DEFAULT 1000
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_cities (
    user_id INTEGER NOT NULL,
    city_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, city_id)
);

CREATE TABLE IF NOT EXISTS user_favorites (
    user_id    INTEGER NOT NULL,
    catalog_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, catalog_id)
);

CREATE TABLE IF NOT EXISTS operations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER,
    catalog_id     INTEGER NOT NULL,
    warehouse_id   INTEGER NOT NULL,
    quantity       REAL,
    operation_type TEXT,
    note           TEXT,
    timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transfers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL,
    to_user_id   INTEGER NOT NULL,
    catalog_id   INTEGER NOT NULL,
    quantity     REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at  DATETIME
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ops_user    ON operations(user_id);
CREATE INDEX IF NOT EXISTS idx_ops_catalog ON operations(catalog_id);
CREATE INDEX IF NOT EXISTS idx_ops_time    ON operations(timestamp);
CREATE INDEX IF NOT EXISTS idx_ops_wh      ON operations(warehouse_id);
CREATE INDEX IF NOT EXISTS idx_ops_type    ON operations(operation_type);
CREATE INDEX IF NOT EXISTS idx_tr_to       ON transfers(to_user_id, status);
CREATE INDEX IF NOT EXISTS idx_tr_from     ON transfers(from_user_id, status);
"""


def main():
    print("Файл базы: " + os.path.abspath(DB_PATH))
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)

    defaults = {
        "show_favorites": "1",
        "show_recent":    "1",
        "show_stock":     "1",
        "recent_limit":   "10",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()

    users = conn.execute("SELECT id, full_name, username, role FROM users").fetchall()
    print("Пользователей сейчас: " + str(len(users)))
    for u in users:
        print("  #" + str(u["id"]) + " " + u["full_name"] + " (" + u["username"] + ") — " + u["role"])
    print()

    full_name = input("ФИО админа: ").strip()
    username = input("Логин: ").strip()
    if not full_name or not username:
        print("ФИО и логин обязательны.")
        return

    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        print("Логин «" + username + "» уже занят.")
        return

    password = getpass.getpass("Пароль: ")
    password2 = getpass.getpass("Повторите: ")
    if password != password2 or not password:
        print("Пароли не совпадают или пустые.")
        return

    pwd_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    cur = conn.execute(
        "INSERT INTO users (full_name, username, password_hash, role, is_active) "
        "VALUES (?, ?, ?, 'admin', 1)",
        (full_name, username, pwd_hash),
    )
    admin_id = cur.lastrowid

    cities = conn.execute("SELECT id, name FROM cities ORDER BY name").fetchall()
    if not cities:
        name = input("Городов нет. Создать город (по умолчанию «Основной»): ").strip() or "Основной"
        cur = conn.execute("INSERT INTO cities (name) VALUES (?)", (name,))
        city_id = cur.lastrowid
        print("Создан город: " + name)
    else:
        print("Доступные города:")
        for c in cities:
            print("  #" + str(c["id"]) + " " + c["name"])
        city_id = cities[0]["id"]
        print("Привязываю админа к городу #" + str(city_id))

    conn.execute("INSERT INTO user_cities (user_id, city_id) VALUES (?, ?)", (admin_id, city_id))
    conn.commit()
    conn.close()

    print()
    print("=" * 50)
    print("✅ Админ создан: " + full_name + " (" + username + ")")
    print("Файл базы: " + os.path.abspath(DB_PATH))
    print("=" * 50)


if __name__ == "__main__":
    main()
