import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "/data/warehouse.db")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = _connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cities (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS warehouses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            city_id     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            address     TEXT,
            room        TEXT,
            responsible TEXT,
            FOREIGN KEY (city_id) REFERENCES cities(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            warehouse_id INTEGER NOT NULL,
            name         TEXT NOT NULL,
            FOREIGN KEY (warehouse_id) REFERENCES warehouses(id) ON DELETE CASCADE,
            UNIQUE(warehouse_id, name)
        )
    """)

    _recreate_catalog_if_needed(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name     TEXT NOT NULL,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            is_active     INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_cities (
            user_id INTEGER NOT NULL,
            city_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, city_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (city_id) REFERENCES cities(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_favorites (
            user_id    INTEGER NOT NULL,
            catalog_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, catalog_id),
            FOREIGN KEY (user_id)    REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (catalog_id) REFERENCES catalog(id) ON DELETE CASCADE
        )
    """)

    _recreate_operations_if_needed(conn)
    _recreate_transfers_if_needed(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_user    ON operations(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_catalog ON operations(catalog_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_time    ON operations(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_wh      ON operations(warehouse_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_type    ON operations(operation_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tr_to       ON transfers(to_user_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tr_from     ON transfers(from_user_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_user    ON user_favorites(user_id)")

    defaults = {
        "show_favorites":            "1",
        "show_recent":               "1",
        "show_stock":                "1",
        "recent_limit":              "10",
        "lock_cooldown_sec":         "30",
        "lock_relay_pulse_max_ms":   "7000",
        "lock_relay_pulse_min_ms":   "3000",
        "lock_debounce_ms":          "500",
        "lock_open_timeout_sec":     "30",
        "tool_hold_days_alert":      "0",
        "label_cols_material":       "2",
        "label_rows_material":       "6",
        "label_cols_tool":           "2",
        "label_rows_tool":           "6",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    
    init_lock_tables(conn)
    _migrate_locks_if_needed(conn)
    _migrate_lock_seen(conn)
    _migrate_locks_per_lock_settings(conn)
    _migrate_locks_keys_columns(conn)
    init_key_tables(conn)
    init_tool_tables(conn)
    conn.commit()
    conn.close()


def _recreate_catalog_if_needed(conn):
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='catalog'"
    ).fetchone()
    if exists:
        info = conn.execute("PRAGMA table_info(catalog)").fetchall()
        article_col = next((r for r in info if r[1] == "article"), None)
        has_sort = any(r[1] == "sort_order" for r in info)
        article_notnull = article_col[3] if article_col else 0
        if article_notnull or not has_sort:
            print("[init_db] catalog устарела — пересоздаю")
            conn.execute("DROP TABLE IF EXISTS catalog")
            exists = None
    conn.commit()

    if not exists:
        conn.execute("""
            CREATE TABLE catalog (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id  INTEGER NOT NULL,
                article       TEXT DEFAULT '',
                name          TEXT NOT NULL,
                short_name    TEXT,
                segment       TEXT,
                applicability TEXT,
                unit          TEXT DEFAULT 'шт',
                location_id   INTEGER,
                sort_order    INTEGER DEFAULT 1000,
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id) ON DELETE CASCADE,
                FOREIGN KEY (location_id)  REFERENCES locations(id) ON DELETE SET NULL
            )
        """)
        conn.commit()


def _recreate_operations_if_needed(conn):
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='operations'"
    ).fetchone()
    if exists:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(operations)").fetchall()}
        required = {"user_id", "catalog_id", "warehouse_id", "quantity",
                    "operation_type", "timestamp", "note"}
        if not required.issubset(cols):
            print("[init_db] operations устарела — пересоздаю (все операции удалятся)")
            conn.execute("DROP TABLE operations")
            exists = None

    if not exists:
        conn.execute("""
            CREATE TABLE operations (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                catalog_id     INTEGER NOT NULL,
                warehouse_id   INTEGER NOT NULL,
                quantity       REAL,
                operation_type TEXT,
                note           TEXT,
                timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)      REFERENCES users(id),
                FOREIGN KEY (catalog_id)   REFERENCES catalog(id),
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
            )
        """)
        conn.commit()


def _recreate_transfers_if_needed(conn):
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='transfers'"
    ).fetchone()
    if exists:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transfers)").fetchall()}
        required = {"from_user_id", "to_user_id", "catalog_id", "quantity", "status"}
        if not required.issubset(cols):
            print("[init_db] transfers устарела — пересоздаю")
            conn.execute("DROP TABLE transfers")
            exists = None

    if not exists:
        conn.execute("""
            CREATE TABLE transfers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id INTEGER NOT NULL,
                to_user_id   INTEGER NOT NULL,
                catalog_id   INTEGER NOT NULL,
                quantity     REAL NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at  DATETIME,
                FOREIGN KEY (from_user_id) REFERENCES users(id),
                FOREIGN KEY (to_user_id)   REFERENCES users(id),
                FOREIGN KEY (catalog_id)   REFERENCES catalog(id)
            )
        """)
        conn.commit()


# =============== НАСТРОЙКИ ===============

def get_setting(key, default=None):
    conn = _connect()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def get_bool_setting(key, default=True):
    v = get_setting(key)
    if v is None:
        return default
    return v == "1"


def set_setting(key, value):
    conn = _connect()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, str(value)))
    conn.commit()
    conn.close()


def get_all_settings():
    conn = _connect()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# =============== ГОРОДА ===============

def list_cities():
    conn = _connect()
    rows = conn.execute("SELECT id, name FROM cities ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_city(city_id):
    conn = _connect()
    row = conn.execute("SELECT id, name FROM cities WHERE id = ?", (city_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_city(name):
    conn = _connect()
    conn.execute("INSERT INTO cities (name) VALUES (?)", (name.strip(),))
    conn.commit()
    conn.close()


def update_city(city_id, name):
    conn = _connect()
    conn.execute("UPDATE cities SET name = ? WHERE id = ?", (name.strip(), city_id))
    conn.commit()
    conn.close()


def delete_city(city_id):
    conn = _connect()
    conn.execute("DELETE FROM cities WHERE id = ?", (city_id,))
    conn.commit()
    conn.close()


# =============== СКЛАДЫ ===============

def list_warehouses(city_id=None):
    conn = _connect()
    if city_id:
        rows = conn.execute("""
            SELECT w.*, c.name AS city_name
            FROM warehouses w
            LEFT JOIN cities c ON c.id = w.city_id
            WHERE w.city_id = ?
            ORDER BY c.name, w.name
        """, (city_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT w.*, c.name AS city_name
            FROM warehouses w
            LEFT JOIN cities c ON c.id = w.city_id
            ORDER BY c.name, w.name
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_warehouse(warehouse_id):
    conn = _connect()
    row = conn.execute("""
        SELECT w.*, c.name AS city_name
        FROM warehouses w
        LEFT JOIN cities c ON c.id = w.city_id
        WHERE w.id = ?
    """, (warehouse_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_warehouse(city_id, name, address, room, responsible):
    conn = _connect()
    conn.execute(
        "INSERT INTO warehouses (city_id, name, address, room, responsible) "
        "VALUES (?, ?, ?, ?, ?)",
        (city_id, name.strip(), address.strip(), room.strip(), responsible.strip()),
    )
    conn.commit()
    conn.close()


def update_warehouse(warehouse_id, city_id, name, address, room, responsible):
    conn = _connect()
    conn.execute(
        "UPDATE warehouses SET city_id = ?, name = ?, address = ?, room = ?, responsible = ? "
        "WHERE id = ?",
        (city_id, name.strip(), address.strip(), room.strip(), responsible.strip(), warehouse_id),
    )
    conn.commit()
    conn.close()


def delete_warehouse(warehouse_id):
    conn = _connect()
    conn.execute("DELETE FROM warehouses WHERE id = ?", (warehouse_id,))
    conn.commit()
    conn.close()


# =============== ЛОКАЦИИ ===============

def list_locations(warehouse_id=None):
    conn = _connect()
    if warehouse_id:
        rows = conn.execute("""
            SELECT l.*, w.name AS warehouse_name
            FROM locations l
            LEFT JOIN warehouses w ON w.id = l.warehouse_id
            WHERE l.warehouse_id = ?
            ORDER BY l.name
        """, (warehouse_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT l.*, w.name AS warehouse_name
            FROM locations l
            LEFT JOIN warehouses w ON w.id = l.warehouse_id
            ORDER BY w.name, l.name
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_location(location_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_location(warehouse_id, name):
    conn = _connect()
    conn.execute("INSERT INTO locations (warehouse_id, name) VALUES (?, ?)",
                 (warehouse_id, name.strip()))
    conn.commit()
    conn.close()


def update_location(location_id, warehouse_id, name):
    conn = _connect()
    conn.execute("UPDATE locations SET warehouse_id = ?, name = ? WHERE id = ?",
                 (warehouse_id, name.strip(), location_id))
    conn.commit()
    conn.close()


def delete_location(location_id):
    conn = _connect()
    conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    conn.commit()
    conn.close()


# =============== КАТАЛОГ ===============

def search_catalog(warehouse_id, query, limit=500):
    conn = _connect()
    like = "%" + query.lower().strip() + "%"
    rows = conn.execute("""
        SELECT c.*, l.name AS location_name
        FROM catalog c
        LEFT JOIN locations l ON l.id = c.location_id
        WHERE c.warehouse_id = ?
          AND (
              LOWER(c.name) LIKE ?
              OR LOWER(COALESCE(c.short_name, ''))    LIKE ?
              OR LOWER(COALESCE(c.segment, ''))       LIKE ?
              OR LOWER(COALESCE(c.applicability, '')) LIKE ?
              OR LOWER(c.article)                     LIKE ?
          )
        ORDER BY c.sort_order, c.name
        LIMIT ?
    """, (warehouse_id, like, like, like, like, like, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_catalog_for_warehouse(warehouse_id, limit=1000):
    conn = _connect()
    rows = conn.execute("""
        SELECT c.*, l.name AS location_name
        FROM catalog c
        LEFT JOIN locations l ON l.id = c.location_id
        WHERE c.warehouse_id = ?
        ORDER BY c.sort_order, c.name
        LIMIT ?
    """, (warehouse_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_catalog_item(catalog_id):
    conn = _connect()
    row = conn.execute("""
        SELECT c.*, l.name AS location_name, w.name AS warehouse_name,
               w.city_id AS warehouse_city_id
        FROM catalog c
        LEFT JOIN locations  l ON l.id = c.location_id
        LEFT JOIN warehouses w ON w.id = c.warehouse_id
        WHERE c.id = ?
    """, (catalog_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_catalog_item(warehouse_id, article, name, short_name, segment,
                        applicability, unit, location_id, sort_order=1000):
    conn = _connect()
    existing = conn.execute("""
        SELECT id FROM catalog
        WHERE warehouse_id = ?
          AND LOWER(name) = LOWER(?)
          AND COALESCE(segment, '') = COALESCE(?, '')
          AND COALESCE(applicability, '') = COALESCE(?, '')
    """, (warehouse_id, name.strip(), segment, applicability)).fetchone()
    if existing:
        conn.close()
        raise sqlite3.IntegrityError("duplicate")

    conn.execute("""
        INSERT INTO catalog
        (warehouse_id, article, name, short_name, segment, applicability,
         unit, location_id, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (warehouse_id, (article or "").strip(), name.strip(),
          short_name or None, segment or None, applicability or None,
          unit or "шт", location_id, sort_order))
    conn.commit()
    conn.close()


def update_catalog_item(catalog_id, warehouse_id, article, name, short_name,
                        segment, applicability, unit, location_id, sort_order):
    conn = _connect()
    conn.execute("""
        UPDATE catalog
        SET warehouse_id = ?, article = ?, name = ?, short_name = ?,
            segment = ?, applicability = ?, unit = ?, location_id = ?,
            sort_order = ?
        WHERE id = ?
    """, (warehouse_id, (article or "").strip(), name.strip(), short_name or None,
          segment or None, applicability or None, unit or "шт", location_id,
          sort_order, catalog_id))
    conn.commit()
    conn.close()


def update_catalog_sort(catalog_id, sort_order):
    conn = _connect()
    conn.execute("UPDATE catalog SET sort_order = ? WHERE id = ?", (sort_order, catalog_id))
    conn.commit()
    conn.close()


def delete_catalog_item(catalog_id):
    conn = _connect()
    conn.execute("DELETE FROM catalog WHERE id = ?", (catalog_id,))
    conn.commit()
    conn.close()


def list_catalog(warehouse_id=None, limit=1000):
    conn = _connect()
    if warehouse_id:
        rows = conn.execute("""
            SELECT c.*, l.name AS location_name, w.name AS warehouse_name
            FROM catalog c
            LEFT JOIN locations  l ON l.id = c.location_id
            LEFT JOIN warehouses w ON w.id = c.warehouse_id
            WHERE c.warehouse_id = ?
            ORDER BY c.sort_order, c.name
            LIMIT ?
        """, (warehouse_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT c.*, l.name AS location_name, w.name AS warehouse_name
            FROM catalog c
            LEFT JOIN locations  l ON l.id = c.location_id
            LEFT JOIN warehouses w ON w.id = c.warehouse_id
            ORDER BY w.name, c.sort_order, c.name
            LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def catalog_count():
    conn = _connect()
    n = conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
    conn.close()
    return n


# =============== ОСТАТКИ ===============

STOCK_FORMULA = """
    SUM(CASE operation_type
        WHEN 'income'       THEN  quantity
        WHEN 'return'       THEN  quantity
        WHEN 'transfer_in'  THEN  quantity
        WHEN 'take'         THEN -quantity
        WHEN 'transfer_out' THEN -quantity
        WHEN 'usage'        THEN -quantity
        WHEN 'writeoff'     THEN -quantity
        WHEN 'inventory'    THEN  quantity
        ELSE 0
    END)
"""


def get_warehouse_stock(warehouse_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT catalog_id, """ + STOCK_FORMULA + """ AS stock
        FROM operations
        WHERE warehouse_id = ?
        GROUP BY catalog_id
    """, (warehouse_id,)).fetchall()
    conn.close()
    return {r["catalog_id"]: (r["stock"] or 0) for r in rows}


def get_user_backpack_stock(user_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT catalog_id,
               SUM(CASE operation_type
                       WHEN 'take'         THEN  quantity
                       WHEN 'transfer_in'  THEN  quantity
                       WHEN 'return'       THEN -quantity
                       WHEN 'transfer_out' THEN -quantity
                       ELSE 0
                   END) AS balance
        FROM operations
        WHERE user_id = ?
        GROUP BY catalog_id
    """, (user_id,)).fetchall()
    conn.close()
    return {r["catalog_id"]: (r["balance"] or 0) for r in rows}


def get_recent_catalog_ids(user_id, limit=10):
    conn = _connect()
    rows = conn.execute("""
        SELECT catalog_id, MAX(timestamp) AS last_time
        FROM operations
        WHERE user_id = ? AND operation_type = 'take'
        GROUP BY catalog_id
        ORDER BY last_time DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [r["catalog_id"] for r in rows]


# =============== ИЗБРАННОЕ ===============

def list_favorites(user_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT catalog_id FROM user_favorites WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return [r["catalog_id"] for r in rows]


def toggle_favorite(user_id, catalog_id):
    conn = _connect()
    exists = conn.execute(
        "SELECT 1 FROM user_favorites WHERE user_id = ? AND catalog_id = ?",
        (user_id, catalog_id),
    ).fetchone()
    if exists:
        conn.execute("DELETE FROM user_favorites WHERE user_id = ? AND catalog_id = ?",
                     (user_id, catalog_id))
        added = False
    else:
        conn.execute("INSERT INTO user_favorites (user_id, catalog_id) VALUES (?, ?)",
                     (user_id, catalog_id))
        added = True
    conn.commit()
    conn.close()
    return added


# =============== ОПЕРАЦИИ ===============

def add_operation(user_id, catalog_id, warehouse_id, quantity, op_type, note=None):
    conn = _connect()
    conn.execute("""
        INSERT INTO operations
        (user_id, catalog_id, warehouse_id, quantity, operation_type, note)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, catalog_id, warehouse_id, quantity, op_type, note))
    conn.commit()
    conn.close()


def add_operations_bulk(user_id, warehouse_id, items, op_type="take"):
    conn = _connect()
    for catalog_id, qty in items:
        conn.execute("""
            INSERT INTO operations
            (user_id, catalog_id, warehouse_id, quantity, operation_type)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, catalog_id, warehouse_id, qty, op_type))
    conn.commit()
    conn.close()


def add_inventory_bulk(warehouse_id, changes, admin_id):
    """
    changes — список (catalog_id, delta, note).
    delta — со знаком (+ излишек, − недостача).
    Записывает операции типа 'inventory'.
    """
    conn = _connect()
    for catalog_id, delta, note in changes:
        if delta == 0:
            continue
        conn.execute("""
            INSERT INTO operations
            (user_id, catalog_id, warehouse_id, quantity, operation_type, note)
            VALUES (?, ?, ?, ?, 'inventory', ?)
        """, (admin_id, catalog_id, warehouse_id, delta, note))
    conn.commit()
    conn.close()


def list_operations(limit=200):
    conn = _connect()
    rows = conn.execute("""
        SELECT o.id, o.timestamp, o.quantity, o.operation_type, o.note,
               u.full_name, u.username,
               o.catalog_id, o.warehouse_id,
               c.article, c.name, c.short_name,
               c.segment, c.applicability, c.unit,
               w.name AS warehouse_name,
               ci.name AS city_name
        FROM operations o
        LEFT JOIN users        u  ON u.id = o.user_id
        LEFT JOIN catalog      c  ON c.id = o.catalog_id
        LEFT JOIN warehouses   w  ON w.id = o.warehouse_id
        LEFT JOIN cities       ci ON ci.id = w.city_id
        ORDER BY o.timestamp DESC, o.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_operation(operation_id):
    conn = _connect()
    row = conn.execute("""
        SELECT o.*, u.full_name, u.username,
               c.name, c.short_name, c.segment, c.applicability, c.unit
        FROM operations o
        LEFT JOIN users   u ON u.id = o.user_id
        LEFT JOIN catalog c ON c.id = o.catalog_id
        WHERE o.id = ?
    """, (operation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_operation(operation_id, catalog_id, warehouse_id, quantity, op_type, note=None):
    conn = _connect()
    conn.execute(
        "UPDATE operations SET catalog_id = ?, warehouse_id = ?, quantity = ?, "
        "operation_type = ?, note = ? WHERE id = ?",
        (catalog_id, warehouse_id, quantity, op_type, note, operation_id),
    )
    conn.commit()
    conn.close()


def delete_operation(operation_id):
    conn = _connect()
    conn.execute("DELETE FROM operations WHERE id = ?", (operation_id,))
    conn.commit()
    conn.close()


def clear_operations():
    conn = _connect()
    conn.execute("DELETE FROM operations")
    conn.execute("DELETE FROM transfers")
    conn.commit()
    conn.close()


# =============== ПОЛЬЗОВАТЕЛИ ===============

def get_user_by_username(username):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users():
    conn = _connect()
    rows = conn.execute("""
        SELECT id, full_name, username, role, is_active
        FROM users ORDER BY full_name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_cities(user_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT c.id, c.name
        FROM user_cities uc
        JOIN cities c ON c.id = uc.city_id
        WHERE uc.user_id = ?
        ORDER BY c.name
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_user_cities(user_id, city_ids):
    conn = _connect()
    conn.execute("DELETE FROM user_cities WHERE user_id = ?", (user_id,))
    for cid in city_ids:
        conn.execute("INSERT INTO user_cities (user_id, city_id) VALUES (?, ?)",
                     (user_id, cid))
    conn.commit()
    conn.close()


def create_user(full_name, username, password_hash, role="user", city_ids=None):
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO users (full_name, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (full_name, username, password_hash, role),
    )
    user_id = cur.lastrowid
    for cid in (city_ids or []):
        conn.execute("INSERT INTO user_cities (user_id, city_id) VALUES (?, ?)",
                     (user_id, cid))
    conn.commit()
    conn.close()


def update_user(user_id, full_name=None, role=None, is_active=None,
                password_hash=None, city_ids=None):
    conn = _connect()
    fields, values = [], []
    if full_name is not None:
        fields.append("full_name = ?"); values.append(full_name)
    if role is not None:
        fields.append("role = ?"); values.append(role)
    if is_active is not None:
        fields.append("is_active = ?"); values.append(int(is_active))
    if password_hash is not None:
        fields.append("password_hash = ?"); values.append(password_hash)
    if fields:
        values.append(user_id)
        conn.execute("UPDATE users SET " + ", ".join(fields) + " WHERE id = ?", values)
    if city_ids is not None:
        conn.execute("DELETE FROM user_cities WHERE user_id = ?", (user_id,))
        for cid in city_ids:
            conn.execute("INSERT INTO user_cities (user_id, city_id) VALUES (?, ?)",
                         (user_id, cid))
    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = _connect()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


# =============== РЮКЗАК И ПЕРЕДАЧИ ===============

def get_user_backpack(user_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT
            c.id AS catalog_id, c.article, c.name, c.short_name,
            c.segment, c.applicability, c.unit,
            w.name AS warehouse_name,
            SUM(CASE o.operation_type
                    WHEN 'take'         THEN  o.quantity
                    WHEN 'transfer_in'  THEN  o.quantity
                    WHEN 'return'       THEN -o.quantity
                    WHEN 'transfer_out' THEN -o.quantity
                    WHEN 'usage'        THEN -o.quantity
                    ELSE 0
                END) AS raw_balance,
            COALESCE((
                SELECT SUM(t.quantity) FROM transfers t
                WHERE t.from_user_id = ?
                  AND t.catalog_id   = c.id
                  AND t.status       = 'pending'
            ), 0) AS pending_out
        FROM operations o
        JOIN catalog c ON c.id = o.catalog_id
        LEFT JOIN warehouses w ON w.id = c.warehouse_id
        WHERE o.user_id = ?
        GROUP BY c.id
    """, (user_id, user_id)).fetchall()
    conn.close()

    result = []
    for r in rows:
        balance = r["raw_balance"] - r["pending_out"]
        if balance <= 0:
            continue
        item = dict(r)
        item["balance"] = balance
        result.append(item)
    return result


def create_transfer(from_user_id, to_user_id, catalog_id, quantity):
    conn = _connect()
    conn.execute(
        "INSERT INTO transfers (from_user_id, to_user_id, catalog_id, quantity) "
        "VALUES (?, ?, ?, ?)",
        (from_user_id, to_user_id, catalog_id, quantity),
    )
    conn.commit()
    conn.close()


def get_transfer(transfer_id):
    conn = _connect()
    row = conn.execute("""
        SELECT t.*,
               fu.full_name AS from_name,
               tu.full_name AS to_name,
               c.name, c.short_name, c.segment, c.applicability, c.unit
        FROM transfers t
        LEFT JOIN users   fu ON fu.id = t.from_user_id
        LEFT JOIN users   tu ON tu.id = t.to_user_id
        LEFT JOIN catalog c  ON c.id = t.catalog_id
        WHERE t.id = ?
    """, (transfer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_incoming_transfers(user_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT t.id, t.catalog_id, t.quantity, t.created_at,
               fu.full_name AS from_name,
               c.name, c.short_name, c.segment, c.applicability, c.unit
        FROM transfers t
        LEFT JOIN users   fu ON fu.id = t.from_user_id
        LEFT JOIN catalog c  ON c.id = t.catalog_id
        WHERE t.to_user_id = ? AND t.status = 'pending'
        ORDER BY t.created_at DESC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_outgoing_transfers(user_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT t.id, t.catalog_id, t.quantity, t.created_at, t.status,
               tu.full_name AS to_name,
               c.name, c.short_name, c.segment, c.applicability, c.unit
        FROM transfers t
        LEFT JOIN users   tu ON tu.id = t.to_user_id
        LEFT JOIN catalog c  ON c.id = t.catalog_id
        WHERE t.from_user_id = ?
        ORDER BY t.created_at DESC
        LIMIT 50
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def accept_transfer(transfer_id):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM transfers WHERE id = ? AND status = 'pending'",
        (transfer_id,),
    ).fetchone()
    if not row:
        conn.close()
        return False

    t = dict(row)

    available = conn.execute("""
        SELECT COALESCE(SUM(
            CASE operation_type
                WHEN 'take'         THEN  quantity
                WHEN 'transfer_in'  THEN  quantity
                WHEN 'return'       THEN -quantity
                WHEN 'transfer_out' THEN -quantity
                ELSE 0
            END
        ), 0)
        FROM operations
        WHERE user_id = ? AND catalog_id = ?
    """, (t["from_user_id"], t["catalog_id"])).fetchone()[0]

    reserved = conn.execute("""
        SELECT COALESCE(SUM(quantity), 0) FROM transfers
        WHERE from_user_id = ? AND catalog_id = ? AND status = 'pending' AND id != ?
    """, (t["from_user_id"], t["catalog_id"], transfer_id)).fetchone()[0]

    if available - reserved < t["quantity"]:
        conn.close()
        return False

    wh = conn.execute("SELECT warehouse_id FROM catalog WHERE id = ?",
                      (t["catalog_id"],)).fetchone()
    warehouse_id = wh["warehouse_id"] if wh else 0

    conn.execute("""
        INSERT INTO operations (user_id, catalog_id, warehouse_id, quantity, operation_type)
        VALUES (?, ?, ?, ?, 'transfer_out')
    """, (t["from_user_id"], t["catalog_id"], warehouse_id, t["quantity"]))
    conn.execute("""
        INSERT INTO operations (user_id, catalog_id, warehouse_id, quantity, operation_type)
        VALUES (?, ?, ?, ?, 'transfer_in')
    """, (t["to_user_id"], t["catalog_id"], warehouse_id, t["quantity"]))
    conn.execute("UPDATE transfers SET status = 'accepted', resolved_at = CURRENT_TIMESTAMP "
                 "WHERE id = ?", (transfer_id,))
    conn.commit()
    conn.close()
    return True


def reject_transfer(transfer_id):
    conn = _connect()
    conn.execute("UPDATE transfers SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP "
                 "WHERE id = ? AND status = 'pending'", (transfer_id,))
    conn.commit()
    conn.close()


def cancel_transfer(transfer_id, user_id):
    conn = _connect()
    conn.execute("UPDATE transfers SET status = 'cancelled', resolved_at = CURRENT_TIMESTAMP "
                 "WHERE id = ? AND status = 'pending' AND from_user_id = ?",
                 (transfer_id, user_id))
    conn.commit()
    conn.close()


# =============== ОТЧЁТЫ ===============

def _op_filter_sql(date_from=None, date_to=None, warehouse_id=None, user_id=None):
    where = ["1=1"]
    params = []
    if date_from:
        where.append("DATE(o.timestamp) >= DATE(?)")
        params.append(date_from)
    if date_to:
        where.append("DATE(o.timestamp) <= DATE(?)")
        params.append(date_to)
    if warehouse_id:
        where.append("o.warehouse_id = ?")
        params.append(warehouse_id)
    if user_id:
        where.append("o.user_id = ?")
        params.append(user_id)
    return " AND ".join(where), params


def get_stock_report(warehouse_id=None, city_id=None):
    conn = _connect()
    sql = """
        SELECT
            c.id AS catalog_id,
            ci.name AS city_name,
            w.name  AS warehouse_name,
            w.id    AS warehouse_id,
            c.article, c.name, c.short_name,
            c.segment, c.applicability, c.unit,
            l.name  AS location_name,
            COALESCE(""" + STOCK_FORMULA + """, 0) AS stock
        FROM catalog c
        LEFT JOIN warehouses w  ON w.id = c.warehouse_id
        LEFT JOIN cities     ci ON ci.id = w.city_id
        LEFT JOIN locations  l  ON l.id = c.location_id
        LEFT JOIN operations o  ON o.catalog_id = c.id
        WHERE 1=1
    """
    params = []
    if warehouse_id:
        sql += " AND c.warehouse_id = ?"
        params.append(warehouse_id)
    if city_id:
        sql += " AND w.city_id = ?"
        params.append(city_id)
    sql += """
        GROUP BY c.id
        ORDER BY ci.name, w.name, c.sort_order, c.name
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_backpacks_report(warehouse_id=None, user_id=None):
    conn = _connect()
    sql = """
        SELECT
            u.id AS user_id, u.full_name, u.username,
            ci.name AS city_name,
            w.name  AS warehouse_name,
            c.id AS catalog_id, c.article, c.name, c.short_name,
            c.segment, c.applicability, c.unit,
            SUM(CASE o.operation_type
                WHEN 'take'         THEN  o.quantity
                WHEN 'transfer_in'  THEN  o.quantity
                WHEN 'return'       THEN -o.quantity
                WHEN 'transfer_out' THEN -o.quantity
                ELSE 0
            END) AS balance
        FROM operations o
        JOIN users   u  ON u.id = o.user_id
        JOIN catalog c  ON c.id = o.catalog_id
        LEFT JOIN warehouses w  ON w.id = c.warehouse_id
        LEFT JOIN cities     ci ON ci.id = w.city_id
        WHERE 1=1
    """
    params = []
    if warehouse_id:
        sql += " AND c.warehouse_id = ?"
        params.append(warehouse_id)
    if user_id:
        sql += " AND u.id = ?"
        params.append(user_id)
    sql += """
        GROUP BY u.id, c.id
        HAVING balance > 0
        ORDER BY u.full_name, c.name
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_operations_report(date_from=None, date_to=None, warehouse_id=None,
                          user_id=None, op_type=None, limit=10000):
    conn = _connect()
    where, params = _op_filter_sql(date_from, date_to, warehouse_id, user_id)
    if op_type:
        where += " AND o.operation_type = ?"
        params.append(op_type)
    sql = """
        SELECT
            o.id, o.timestamp, o.operation_type, o.quantity, o.note,
            u.full_name, u.username,
            c.article, c.name, c.short_name,
            c.segment, c.applicability, c.unit,
            w.name AS warehouse_name,
            ci.name AS city_name
        FROM operations o
        LEFT JOIN users      u  ON u.id = o.user_id
        LEFT JOIN catalog    c  ON c.id = o.catalog_id
        LEFT JOIN warehouses w  ON w.id = o.warehouse_id
        LEFT JOIN cities     ci ON ci.id = w.city_id
        WHERE """ + where + """
        ORDER BY o.timestamp DESC, o.id DESC
        LIMIT ?
    """
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary_users_report(date_from=None, date_to=None, warehouse_id=None, user_id=None):
    conn = _connect()
    where, params = _op_filter_sql(date_from, date_to, warehouse_id, user_id)
    sql = """
        SELECT
            u.id AS user_id, u.full_name, u.username,
            c.id AS catalog_id, c.article, c.name, c.short_name,
            c.segment, c.applicability, c.unit,
            w.name AS warehouse_name,
            ci.name AS city_name,
            SUM(o.quantity) AS total
        FROM operations o
        JOIN users   u  ON u.id = o.user_id
        JOIN catalog c  ON c.id = o.catalog_id
        LEFT JOIN warehouses w  ON w.id = o.warehouse_id
        LEFT JOIN cities     ci ON ci.id = w.city_id
        WHERE o.operation_type = 'take' AND """ + where + """
        GROUP BY u.id, c.id
        ORDER BY u.full_name, c.name
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary_items_report(date_from=None, date_to=None,
                             warehouse_id=None, city_id=None):
    conn = _connect()
    sql = """
        SELECT
            c.id AS catalog_id, c.article, c.name, c.short_name,
            c.segment, c.applicability, c.unit,
            w.id AS warehouse_id, w.name AS warehouse_name,
            ci.name AS city_name,
            COALESCE(SUM(CASE WHEN o.operation_type='income'       THEN o.quantity ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN o.operation_type='take'         THEN o.quantity ELSE 0 END), 0) AS taken,
            COALESCE(SUM(CASE WHEN o.operation_type='return'       THEN o.quantity ELSE 0 END), 0) AS returned,
            COALESCE(SUM(CASE WHEN o.operation_type='transfer_out' THEN o.quantity ELSE 0 END), 0) AS transfer_out,
            COALESCE(SUM(CASE WHEN o.operation_type='transfer_in'  THEN o.quantity ELSE 0 END), 0) AS transfer_in,
            COALESCE(SUM(CASE WHEN o.operation_type='usage'        THEN o.quantity ELSE 0 END), 0) AS usage,
            COALESCE(SUM(CASE WHEN o.operation_type='writeoff'     THEN o.quantity ELSE 0 END), 0) AS writeoff,
            COALESCE(SUM(CASE WHEN o.operation_type='inventory'    THEN o.quantity ELSE 0 END), 0) AS inventory,
            COALESCE(""" + STOCK_FORMULA + """, 0) AS stock
        FROM catalog c
        LEFT JOIN warehouses w  ON w.id = c.warehouse_id
        LEFT JOIN cities     ci ON ci.id = w.city_id
        LEFT JOIN operations o  ON o.catalog_id = c.id
    """
    where = ["1=1"]
    params = []
    if warehouse_id:
        where.append("c.warehouse_id = ?")
        params.append(warehouse_id)
    if city_id:
        where.append("w.city_id = ?")
        params.append(city_id)
    if date_from:
        where.append("(o.timestamp IS NULL OR DATE(o.timestamp) >= DATE(?))")
        params.append(date_from)
    if date_to:
        where.append("(o.timestamp IS NULL OR DATE(o.timestamp) <= DATE(?))")
        params.append(date_to)
    sql += " WHERE " + " AND ".join(where)
    sql += """
        GROUP BY c.id
        ORDER BY ci.name, w.name, c.name
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transfers_report(date_from=None, date_to=None, status=None):
    conn = _connect()
    where = ["1=1"]
    params = []
    if date_from:
        where.append("DATE(t.created_at) >= DATE(?)")
        params.append(date_from)
    if date_to:
        where.append("DATE(t.created_at) <= DATE(?)")
        params.append(date_to)
    if status:
        where.append("t.status = ?")
        params.append(status)
    sql = """
        SELECT
            t.id, t.created_at, t.resolved_at, t.status, t.quantity,
            fu.full_name AS from_name, fu.username AS from_username,
            tu.full_name AS to_name,   tu.username AS to_username,
            c.article, c.name, c.short_name,
            c.segment, c.applicability, c.unit,
            w.name AS warehouse_name,
            ci.name AS city_name
        FROM transfers t
        LEFT JOIN users      fu ON fu.id = t.from_user_id
        LEFT JOIN users      tu ON tu.id = t.to_user_id
        LEFT JOIN catalog    c  ON c.id = t.catalog_id
        LEFT JOIN warehouses w  ON w.id = c.warehouse_id
        LEFT JOIN cities     ci ON ci.id = w.city_id
        WHERE """ + " AND ".join(where) + """
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 10000
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# =============== ИНСТРУМЕНТЫ ===============

def init_tool_tables(conn):
    """Создаёт таблицы для инструментов."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tools (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            city_id       INTEGER NOT NULL,
            warehouse_id  INTEGER NOT NULL,
            inventory_no  TEXT NOT NULL,
            name          TEXT NOT NULL,
            serial_no     TEXT,
            status        TEXT NOT NULL DEFAULT 'stock',
            holder_id     INTEGER,
            taken_at      DATETIME,
            photo         TEXT,
            note          TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (city_id)      REFERENCES cities(id) ON DELETE CASCADE,
            FOREIGN KEY (warehouse_id) REFERENCES warehouses(id) ON DELETE CASCADE,
            FOREIGN KEY (holder_id)    REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE(city_id, inventory_no)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_id     INTEGER NOT NULL,
            user_id     INTEGER,
            event_type  TEXT NOT NULL,
            note        TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tool_id) REFERENCES tools(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_city  ON tools(city_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_wh    ON tools(warehouse_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_holder ON tools(holder_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_log_tool ON tool_log(tool_id)")


def list_tools(city_id=None, warehouse_id=None, status=None):
    conn = _connect()
    sql = """
        SELECT t.*,
               c.name AS city_name,
               w.name AS warehouse_name,
               u.full_name AS holder_name,
               u.username AS holder_username
        FROM tools t
        LEFT JOIN cities     c ON c.id = t.city_id
        LEFT JOIN warehouses w ON w.id = t.warehouse_id
        LEFT JOIN users      u ON u.id = t.holder_id
        WHERE 1=1
    """
    params = []
    if city_id:
        sql += " AND t.city_id = ?"
        params.append(city_id)
    if warehouse_id:
        sql += " AND t.warehouse_id = ?"
        params.append(warehouse_id)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.inventory_no"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tool(tool_id):
    conn = _connect()
    row = conn.execute("""
        SELECT t.*,
               c.name AS city_name,
               w.name AS warehouse_name,
               u.full_name AS holder_name,
               u.username AS holder_username
        FROM tools t
        LEFT JOIN cities     c ON c.id = t.city_id
        LEFT JOIN warehouses w ON w.id = t.warehouse_id
        LEFT JOIN users      u ON u.id = t.holder_id
        WHERE t.id = ?
    """, (tool_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_tools_by_holder(user_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT t.*,
               c.name AS city_name,
               w.name AS warehouse_name
        FROM tools t
        LEFT JOIN cities     c ON c.id = t.city_id
        LEFT JOIN warehouses w ON w.id = t.warehouse_id
        WHERE t.holder_id = ? AND t.status = 'taken'
        ORDER BY t.inventory_no
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def next_inventory_no(city_id, name):
    """
    Генерирует следующий инвентарный номер в пределах города.
    Формат: '<Первое слово> <N>' — например, 'Перфоратор 1'.
    Проверяет занятые номера.
    """
    first_word = (name.strip().split() or ["Инструмент"])[0]
    conn = _connect()
    rows = conn.execute("""
        SELECT inventory_no FROM tools
        WHERE city_id = ? AND inventory_no LIKE ?
    """, (city_id, first_word + " %")).fetchall()
    conn.close()

    max_n = 0
    for r in rows:
        try:
            n = int(r["inventory_no"].split()[-1])
            if n > max_n:
                max_n = n
        except (ValueError, IndexError):
            continue

    return f"{first_word} {max_n + 1}"


def create_tool(city_id, warehouse_id, inventory_no, name, serial_no=None, note=None):
    conn = _connect()
    cur = conn.execute("""
        INSERT INTO tools
        (city_id, warehouse_id, inventory_no, name, serial_no, note, status)
        VALUES (?, ?, ?, ?, ?, ?, 'stock')
    """, (city_id, warehouse_id, inventory_no.strip(), name.strip(),
          serial_no or None, note or None))
    tool_id = cur.lastrowid
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, NULL, 'created', 'Инструмент добавлен в систему')
    """, (tool_id,))
    conn.commit()
    conn.close()
    return tool_id


def update_tool(tool_id, city_id, warehouse_id, inventory_no, name,
                serial_no=None, note=None):
    conn = _connect()
    conn.execute("""
        UPDATE tools
        SET city_id = ?, warehouse_id = ?, inventory_no = ?, name = ?,
            serial_no = ?, note = ?
        WHERE id = ?
    """, (city_id, warehouse_id, inventory_no.strip(), name.strip(),
          serial_no or None, note or None, tool_id))
    conn.commit()
    conn.close()


def delete_tool(tool_id):
    """Удаляет инструмент и всю его историю (CASCADE)."""
    from core.images import delete_tool_photo
    tool = get_tool(tool_id)
    if tool and tool.get("photo"):
        try:
            delete_tool_photo(tool["photo"])
        except Exception:
            pass

    conn = _connect()
    conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
    conn.commit()
    conn.close()


def set_tool_photo(tool_id, filename):
    conn = _connect()
    conn.execute("UPDATE tools SET photo = ? WHERE id = ?", (filename, tool_id))
    conn.commit()
    conn.close()


# =============== СТАТУСЫ ИНСТРУМЕНТА ===============

def take_tool(tool_id, user_id, note=None):
    """Сотрудник берёт инструмент. Статус → taken."""
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] != "stock":
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET status = 'taken', holder_id = ?, taken_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (user_id, tool_id))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'take', ?)
    """, (tool_id, user_id, note or "Взял инструмент"))
    conn.commit()
    conn.close()
    return True


def return_tool(tool_id, user_id, note=None):
    """Сотрудник возвращает инструмент. Статус → stock."""
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] != "taken":
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET status = 'stock', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'return', ?)
    """, (tool_id, user_id, note or "Вернул инструмент"))
    conn.commit()
    conn.close()
    return True


def repair_tool(tool_id, user_id, note=None):
    """Отправить инструмент в ремонт. Статус → repair."""
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] not in ("stock", "taken"):
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET status = 'repair', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'repair', ?)
    """, (tool_id, user_id, note or "Отправлен в ремонт"))
    conn.commit()
    conn.close()
    return True


def mark_tool_lost(tool_id, user_id, note=None):
    """Пометить инструмент как потерянный."""
    conn = _connect()
    conn.execute("""
        UPDATE tools
        SET status = 'lost', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'lost', ?)
    """, (tool_id, user_id, note or "Инструмент потерян"))
    conn.commit()
    conn.close()


def decommission_tool(tool_id, user_id, note=None):
    """Списать инструмент (износ)."""
    conn = _connect()
    conn.execute("""
        UPDATE tools
        SET status = 'decommissioned', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'decommissioned', ?)
    """, (tool_id, user_id, note or "Инструмент списан"))
    conn.commit()
    conn.close()


def create_tool_transfer(tool_id, to_user_id, from_user_id):
    """Передача инструмента от одного сотрудника другому."""
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] != "taken" or tool["holder_id"] != from_user_id:
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET holder_id = ?, taken_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (to_user_id, tool_id))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'transfer', ?)
    """, (tool_id, from_user_id, f"Передан сотруднику id={to_user_id}"))
    conn.commit()
    conn.close()
    return True


def get_tool_log(tool_id, limit=100):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.event_type, l.note, l.created_at,
               u.full_name, u.username
        FROM tool_log l
        LEFT JOIN users u ON u.id = l.user_id
        WHERE l.tool_id = ?
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (tool_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_tool_log(limit=200):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.event_type, l.note, l.created_at,
               u.full_name, u.username,
               t.id AS tool_id, t.inventory_no, t.name AS tool_name,
               c.name AS city_name
        FROM tool_log l
        LEFT JOIN users u ON u.id = l.user_id
        LEFT JOIN tools t ON t.id = l.tool_id
        LEFT JOIN cities c ON c.id = t.city_id
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def tools_count(city_id=None):
    conn = _connect()
    if city_id:
        n = conn.execute("SELECT COUNT(*) FROM tools WHERE city_id = ?",
                         (city_id,)).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    conn.close()
    return n


# ============================================================
# ============ БЛОК: ШКАФЧИКИ С КЛЮЧАМИ =====================
# ============ (добавлен force-скриптом) ====================
# ============================================================


def init_lock_tables(conn):
    """Создаёт таблицы для шкафчиков."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS locks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            warehouse_id   INTEGER NOT NULL,
            name           TEXT NOT NULL,
            arduino_id     TEXT UNIQUE NOT NULL,
            pending_open   INTEGER NOT NULL DEFAULT 0,
            requested_by   INTEGER,
            requested_at   DATETIME,
            is_open        INTEGER NOT NULL DEFAULT 0,
            last_event_at  DATETIME,
            FOREIGN KEY (warehouse_id) REFERENCES warehouses(id) ON DELETE CASCADE,
            FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lock_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lock_id     INTEGER NOT NULL,
            user_id     INTEGER,
            event_type  TEXT NOT NULL,
            note        TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lock_id) REFERENCES locks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locklog_lock ON lock_log(lock_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locklog_time ON lock_log(created_at)")


def _migrate_locks_if_needed(conn):
    """Добавляет last_open_at в locks, если её нет."""
    if not _table_exists(conn, "locks"):
        return
    cols = _table_cols(conn, "locks")
    if "last_open_at" not in cols:
        conn.execute("ALTER TABLE locks ADD COLUMN last_open_at DATETIME")
        print("[init_db] locks: добавлена колонка last_open_at")
    conn.commit()


def _table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _table_cols(conn, name):
    return {r[1] for r in conn.execute("PRAGMA table_info(" + name + ")").fetchall()}


# ============ Функции шкафчиков ============

def list_locks(warehouse_id=None):
    conn = _connect()
    if warehouse_id:
        rows = conn.execute("""
            SELECT l.*, w.name AS warehouse_name, w.city_id
            FROM locks l
            LEFT JOIN warehouses w ON w.id = l.warehouse_id
            WHERE l.warehouse_id = ?
            ORDER BY l.name
        """, (warehouse_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT l.*, w.name AS warehouse_name, w.city_id
            FROM locks l
            LEFT JOIN warehouses w ON w.id = l.warehouse_id
            ORDER BY w.name, l.name
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_lock(lock_id):
    conn = _connect()
    row = conn.execute("""
        SELECT l.*, w.name AS warehouse_name, w.city_id
        FROM locks l
        LEFT JOIN warehouses w ON w.id = l.warehouse_id
        WHERE l.id = ?
    """, (lock_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_lock_by_arduino_id(arduino_id):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM locks WHERE arduino_id = ?", (arduino_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_lock(warehouse_id, name, arduino_id):
    conn = _connect()
    conn.execute(
        "INSERT INTO locks (warehouse_id, name, arduino_id) VALUES (?, ?, ?)",
        (warehouse_id, name.strip(), arduino_id.strip()),
    )
    conn.commit()
    conn.close()


def update_lock(lock_id, warehouse_id, name, arduino_id):
    conn = _connect()
    conn.execute(
        "UPDATE locks SET warehouse_id = ?, name = ?, arduino_id = ? WHERE id = ?",
        (warehouse_id, name.strip(), arduino_id.strip(), lock_id),
    )
    conn.commit()
    conn.close()


def delete_lock(lock_id):
    conn = _connect()
    conn.execute("DELETE FROM locks WHERE id = ?", (lock_id,))
    conn.commit()
    conn.close()


def request_lock_open(lock_id, user_id):
    conn = _connect()
    conn.execute("""
        UPDATE locks
        SET pending_open = 1,
            requested_by = ?,
            requested_at = CURRENT_TIMESTAMP,
            last_open_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (user_id, lock_id))
    conn.execute("""
        INSERT INTO lock_log (lock_id, user_id, event_type, note)
        VALUES (?, ?, 'open_request', 'Сотрудник запросил открытие')
    """, (lock_id, user_id))
    conn.commit()
    conn.close()


def cancel_lock_open(lock_id):
    conn = _connect()
    conn.execute("UPDATE locks SET pending_open = 0, requested_by = NULL WHERE id = ?",
                 (lock_id,))
    conn.commit()
    conn.close()


def consume_lock_open(lock_id):
    conn = _connect()
    conn.execute("UPDATE locks SET pending_open = 0 WHERE id = ?", (lock_id,))
    conn.commit()
    conn.close()


def log_lock_event(lock_id, event_type, note=None, user_id=None):
    conn = _connect()
    conn.execute("""
        INSERT INTO lock_log (lock_id, user_id, event_type, note)
        VALUES (?, ?, ?, ?)
    """, (lock_id, user_id, event_type, note))
    if event_type == "opened":
        conn.execute("""
            UPDATE locks
            SET is_open = 1,
                last_event_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (lock_id,))
    elif event_type in ("closed", "timeout"):
        conn.execute("""
            UPDATE locks
            SET is_open = 0,
                last_event_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (lock_id,))
    conn.commit()
    conn.close()


def get_lock_log(lock_id, limit=100):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.event_type, l.note, l.created_at,
               u.full_name, u.username
        FROM lock_log l
        LEFT JOIN users u ON u.id = l.user_id
        WHERE l.lock_id = ?
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (lock_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_lock_log(limit=200):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.event_type, l.note, l.created_at,
               u.full_name, u.username,
               lk.name AS lock_name,
               lk.id AS lock_id,
               w.name AS warehouse_name
        FROM lock_log l
        LEFT JOIN users u ON u.id = l.user_id
        LEFT JOIN locks lk ON lk.id = l.lock_id
        LEFT JOIN warehouses w ON w.id = lk.warehouse_id
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_lock_cooldown_left(lock_id, cooldown_sec=30):
    conn = _connect()
    row = conn.execute(
        "SELECT last_open_at FROM locks WHERE id = ?", (lock_id,)
    ).fetchone()
    conn.close()
    if not row or not row["last_open_at"]:
        return 0
    from datetime import datetime
    try:
        last = datetime.fromisoformat(str(row["last_open_at"]))
    except (ValueError, TypeError):
        return 0
    now = datetime.utcnow()
    passed = (now - last).total_seconds()
    left = cooldown_sec - passed
    return int(left) if left > 0 else 0


# ============================================================
# ============ БЛОК: ИНСТРУМЕНТЫ ============================
# ============ (добавлен force-скриптом) ====================
# ============================================================


def init_tool_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tools (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            city_id       INTEGER NOT NULL,
            warehouse_id  INTEGER NOT NULL,
            inventory_no  TEXT NOT NULL,
            name          TEXT NOT NULL,
            serial_no     TEXT,
            status        TEXT NOT NULL DEFAULT 'stock',
            holder_id     INTEGER,
            taken_at      DATETIME,
            photo         TEXT,
            note          TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (city_id)      REFERENCES cities(id) ON DELETE CASCADE,
            FOREIGN KEY (warehouse_id) REFERENCES warehouses(id) ON DELETE CASCADE,
            FOREIGN KEY (holder_id)    REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE(city_id, inventory_no)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_id     INTEGER NOT NULL,
            user_id     INTEGER,
            event_type  TEXT NOT NULL,
            note        TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tool_id) REFERENCES tools(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_city   ON tools(city_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_wh     ON tools(warehouse_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_holder ON tools(holder_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_log_tool ON tool_log(tool_id)")


def list_tools(city_id=None, warehouse_id=None, status=None):
    conn = _connect()
    sql = """
        SELECT t.*,
               c.name AS city_name,
               w.name AS warehouse_name,
               u.full_name AS holder_name,
               u.username AS holder_username
        FROM tools t
        LEFT JOIN cities     c ON c.id = t.city_id
        LEFT JOIN warehouses w ON w.id = t.warehouse_id
        LEFT JOIN users      u ON u.id = t.holder_id
        WHERE 1=1
    """
    params = []
    if city_id:
        sql += " AND t.city_id = ?"
        params.append(city_id)
    if warehouse_id:
        sql += " AND t.warehouse_id = ?"
        params.append(warehouse_id)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.inventory_no"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tool(tool_id):
    conn = _connect()
    row = conn.execute("""
        SELECT t.*,
               c.name AS city_name,
               w.name AS warehouse_name,
               u.full_name AS holder_name,
               u.username AS holder_username
        FROM tools t
        LEFT JOIN cities     c ON c.id = t.city_id
        LEFT JOIN warehouses w ON w.id = t.warehouse_id
        LEFT JOIN users      u ON u.id = t.holder_id
        WHERE t.id = ?
    """, (tool_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_tools_by_holder(user_id):
    conn = _connect()
    rows = conn.execute("""
        SELECT t.*,
               c.name AS city_name,
               w.name AS warehouse_name
        FROM tools t
        LEFT JOIN cities     c ON c.id = t.city_id
        LEFT JOIN warehouses w ON w.id = t.warehouse_id
        WHERE t.holder_id = ? AND t.status = 'taken'
        ORDER BY t.inventory_no
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def next_inventory_no(city_id, name):
    first_word = (name.strip().split() or ["Инструмент"])[0]
    conn = _connect()
    rows = conn.execute("""
        SELECT inventory_no FROM tools
        WHERE city_id = ? AND inventory_no LIKE ?
    """, (city_id, first_word + " %")).fetchall()
    conn.close()
    max_n = 0
    for r in rows:
        try:
            n = int(r["inventory_no"].split()[-1])
            if n > max_n:
                max_n = n
        except (ValueError, IndexError):
            continue
    return f"{first_word} {max_n + 1}"


def create_tool(city_id, warehouse_id, inventory_no, name, serial_no=None, note=None):
    conn = _connect()
    cur = conn.execute("""
        INSERT INTO tools
        (city_id, warehouse_id, inventory_no, name, serial_no, note, status)
        VALUES (?, ?, ?, ?, ?, ?, 'stock')
    """, (city_id, warehouse_id, inventory_no.strip(), name.strip(),
          serial_no or None, note or None))
    tool_id = cur.lastrowid
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, NULL, 'created', 'Инструмент добавлен в систему')
    """, (tool_id,))
    conn.commit()
    conn.close()
    return tool_id


def update_tool(tool_id, city_id, warehouse_id, inventory_no, name,
                serial_no=None, note=None):
    conn = _connect()
    conn.execute("""
        UPDATE tools
        SET city_id = ?, warehouse_id = ?, inventory_no = ?, name = ?,
            serial_no = ?, note = ?
        WHERE id = ?
    """, (city_id, warehouse_id, inventory_no.strip(), name.strip(),
          serial_no or None, note or None, tool_id))
    conn.commit()
    conn.close()


def delete_tool(tool_id):
    from core.images import delete_tool_photo
    tool = get_tool(tool_id)
    if tool and tool.get("photo"):
        try:
            delete_tool_photo(tool["photo"])
        except Exception:
            pass
    conn = _connect()
    conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
    conn.commit()
    conn.close()


def set_tool_photo(tool_id, filename):
    conn = _connect()
    conn.execute("UPDATE tools SET photo = ? WHERE id = ?", (filename, tool_id))
    conn.commit()
    conn.close()


def take_tool(tool_id, user_id, note=None):
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] != "stock":
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET status = 'taken', holder_id = ?, taken_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (user_id, tool_id))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'take', ?)
    """, (tool_id, user_id, note or "Взял инструмент"))
    conn.commit()
    conn.close()
    return True


def return_tool(tool_id, user_id, note=None):
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] != "taken":
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET status = 'stock', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'return', ?)
    """, (tool_id, user_id, note or "Вернул инструмент"))
    conn.commit()
    conn.close()
    return True


def repair_tool(tool_id, user_id, note=None):
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] not in ("stock", "taken"):
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET status = 'repair', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'repair', ?)
    """, (tool_id, user_id, note or "Отправлен в ремонт"))
    conn.commit()
    conn.close()
    return True


def mark_tool_lost(tool_id, user_id, note=None):
    conn = _connect()
    conn.execute("""
        UPDATE tools
        SET status = 'lost', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'lost', ?)
    """, (tool_id, user_id, note or "Инструмент потерян"))
    conn.commit()
    conn.close()


def decommission_tool(tool_id, user_id, note=None):
    conn = _connect()
    conn.execute("""
        UPDATE tools
        SET status = 'decommissioned', holder_id = NULL, taken_at = NULL
        WHERE id = ?
    """, (tool_id,))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'decommissioned', ?)
    """, (tool_id, user_id, note or "Инструмент списан"))
    conn.commit()
    conn.close()


def create_tool_transfer(tool_id, to_user_id, from_user_id):
    conn = _connect()
    tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if not tool or tool["status"] != "taken" or tool["holder_id"] != from_user_id:
        conn.close()
        return False
    conn.execute("""
        UPDATE tools
        SET holder_id = ?, taken_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (to_user_id, tool_id))
    conn.execute("""
        INSERT INTO tool_log (tool_id, user_id, event_type, note)
        VALUES (?, ?, 'transfer', ?)
    """, (tool_id, from_user_id, f"Передан сотруднику id={to_user_id}"))
    conn.commit()
    conn.close()
    return True


def get_tool_log(tool_id, limit=100):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.event_type, l.note, l.created_at,
               u.full_name, u.username
        FROM tool_log l
        LEFT JOIN users u ON u.id = l.user_id
        WHERE l.tool_id = ?
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (tool_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_tool_log(limit=200):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.event_type, l.note, l.created_at,
               u.full_name, u.username,
               t.id AS tool_id, t.inventory_no, t.name AS tool_name,
               c.name AS city_name
        FROM tool_log l
        LEFT JOIN users u ON u.id = l.user_id
        LEFT JOIN tools t ON t.id = l.tool_id
        LEFT JOIN cities c ON c.id = t.city_id
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def tools_count(city_id=None):
    conn = _connect()
    if city_id:
        n = conn.execute("SELECT COUNT(*) FROM tools WHERE city_id = ?",
                         (city_id,)).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    conn.close()
    return n


def get_catalog_items_by_ids(ids):
    """Возвращает список позиций каталога по списку ID (для печати этикеток)."""
    if not ids:
        return []
    conn = _connect()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute("""
        SELECT c.*, l.name AS location_name, w.name AS warehouse_name
        FROM catalog c
        LEFT JOIN locations  l ON l.id = c.location_id
        LEFT JOIN warehouses w ON w.id = c.warehouse_id
        WHERE c.id IN (""" + placeholders + """)
        ORDER BY c.warehouse_id, c.sort_order, c.name
    """, ids).fetchall()
    conn.close()
    return [dict(r) for r in rows]



# =============== HEARTBEAT ШКАФЧИКА ===============

def _migrate_lock_seen(conn):
    """Добавляет last_seen_at в locks, если её нет."""
    if not _table_exists(conn, "locks"):
        return
    cols = _table_cols(conn, "locks")
    if "last_seen_at" not in cols:
        conn.execute("ALTER TABLE locks ADD COLUMN last_seen_at DATETIME")
        print("[init_db] locks: добавлена колонка last_seen_at")
    conn.commit()


def update_lock_seen(lock_id, door_open=None):
    """
    Обновляет last_seen_at. Если door_open передан (0/1) — синхронизирует is_open.
    НЕ пишет в лог — только статус.
    """
    conn = _connect()
    if door_open is None:
        conn.execute("UPDATE locks SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (lock_id,))
    else:
        conn.execute("""
            UPDATE locks
            SET last_seen_at = CURRENT_TIMESTAMP,
                is_open = ?
            WHERE id = ?
        """, (1 if door_open else 0, lock_id))
    conn.commit()
    conn.close()


def get_lock_seen_age(lock_id):
    """Возвращает кол-во секунд с последнего heartbeat. None — если не было."""
    conn = _connect()
    row = conn.execute(
        "SELECT last_seen_at FROM locks WHERE id = ?", (lock_id,)
    ).fetchone()
    conn.close()
    if not row or not row["last_seen_at"]:
        return None
    from datetime import datetime
    try:
        last = datetime.fromisoformat(str(row["last_seen_at"]))
    except (ValueError, TypeError):
        return None
    now = datetime.utcnow()
    return int((now - last).total_seconds())


def check_unauthorized_open(lock_id, door_open):
    """
    Проверяет: это переход из закрыто в открыто без команды?
    Возвращает True, если открытие подозрительное.
    НЕ пишет в лог — только возвращает True/False.
    """
    if door_open != 1:
        return False

    conn = _connect()
    row = conn.execute(
        "SELECT is_open, requested_at FROM locks WHERE id = ?", (lock_id,)
    ).fetchone()
    conn.close()

    if not row:
        return False

    # Уже было открыто — это не переход, не считаем
    if row["is_open"] == 1:
        return False

    # Если команда была за последние 5 минут — это нормальное открытие
    if row["requested_at"]:
        from datetime import datetime
        try:
            req = datetime.fromisoformat(str(row["requested_at"]))
            now = datetime.utcnow()
            elapsed = (now - req).total_seconds()
            if elapsed < 300:
                return False
        except (ValueError, TypeError):
            pass

    return True


def _migrate_locks_per_lock_settings(conn):
    """Добавляет колонки с таймингами в locks, если их нет."""
    if not _table_exists(conn, "locks"):
        return
    cols = _table_cols(conn, "locks")
    added = []
    for col, typ in (
        ("relay_max_ms", "INTEGER"),
        ("relay_min_ms", "INTEGER"),
        ("debounce_ms", "INTEGER"),
        ("cooldown_sec", "INTEGER"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE locks ADD COLUMN {col} {typ}")
            added.append(col)
    if added:
        print(f"[init_db] locks: добавлены колонки {', '.join(added)}")
    conn.commit()


def update_lock_timings(lock_id, relay_max_ms, relay_min_ms, debounce_ms, cooldown_sec):
    """Обновляет тайминги шкафчика."""
    conn = _connect()
    conn.execute("""
        UPDATE locks
        SET relay_max_ms = ?, relay_min_ms = ?, debounce_ms = ?, cooldown_sec = ?
        WHERE id = ?
    """, (relay_max_ms, relay_min_ms, debounce_ms, cooldown_sec, lock_id))
    conn.commit()
    conn.close()


def get_lock_timings(lock_id):
    """
    Возвращает тайминги шкафчика. Если NULL — берём из settings.
    """
    lock = get_lock(lock_id)
    if not lock:
        return None

    def _resolve(key_lock, key_setting, default):
        v = lock.get(key_lock)
        if v is not None:
            return int(v)
        v = get_setting(key_setting)
        if v:
            try:
                return int(v)
            except (ValueError, TypeError):
                pass
        return default

    return {
        "relay_max_ms": _resolve("relay_max_ms", "lock_relay_pulse_max_ms", 7000),
        "relay_min_ms": _resolve("relay_min_ms", "lock_relay_pulse_min_ms", 3000),
        "debounce_ms":  _resolve("debounce_ms",  "lock_debounce_ms",        500),
        "cooldown_sec": _resolve("cooldown_sec", "lock_cooldown_sec",        30),
    }


# ============================================================
# =============== КЛЮЧИ В ШКАФЧИКЕ ===========================
# ============================================================

def init_key_tables(conn):
    """Создаёт таблицы для ключей."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            lock_id         INTEGER NOT NULL,
            slot_code       TEXT NOT NULL,
            name            TEXT,
            description     TEXT,
            resistance_adc  INTEGER,
            resistance_ohm  REAL,
            current_adc     INTEGER,
            is_present      INTEGER NOT NULL DEFAULT 0,
            last_change_at  DATETIME,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lock_id) REFERENCES locks(id) ON DELETE CASCADE,
            UNIQUE(lock_id, slot_code)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS key_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lock_id     INTEGER NOT NULL,
            slot_code   TEXT NOT NULL,
            user_id     INTEGER,
            event_type  TEXT NOT NULL,
            note        TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lock_id) REFERENCES locks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keylog_lock ON key_log(lock_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keylog_time ON key_log(created_at)")


def _migrate_locks_keys_columns(conn):
    """Добавляет колонки для калибровки в locks."""
    if not _table_exists(conn, "locks"):
        return
    cols = _table_cols(conn, "locks")
    added = []
    for col, typ, default in (
        ("calibration_requested", "INTEGER", "0"),
        ("calibration_started_at", "DATETIME", None),
    ):
        if col not in cols:
            if default is not None:
                conn.execute(f"ALTER TABLE locks ADD COLUMN {col} {typ} DEFAULT {default}")
            else:
                conn.execute(f"ALTER TABLE locks ADD COLUMN {col} {typ}")
            added.append(col)
    if added:
        print(f"[init_db] locks: добавлены колонки {', '.join(added)}")
    conn.commit()


# ============ CRUD КЛЮЧЕЙ ============

def list_keys(lock_id):
    """Все 48 гнёзд для шкафчика (в том числе пустые)."""
    conn = _connect()
    rows = conn.execute("""
        SELECT * FROM keys WHERE lock_id = ? ORDER BY slot_code
    """, (lock_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_key(lock_id, slot_code):
    conn = _connect()
    row = conn.execute("""
        SELECT * FROM keys WHERE lock_id = ? AND slot_code = ?
    """, (lock_id, slot_code)).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_key(lock_id, slot_code, name=None, description=None):
    """Создаёт или обновляет ключ в гнезде."""
    conn = _connect()
    existing = conn.execute("""
        SELECT id FROM keys WHERE lock_id = ? AND slot_code = ?
    """, (lock_id, slot_code)).fetchone()

    if existing:
        conn.execute("""
            UPDATE keys SET name = ?, description = ?
            WHERE lock_id = ? AND slot_code = ?
        """, (name, description, lock_id, slot_code))
    else:
        conn.execute("""
            INSERT INTO keys (lock_id, slot_code, name, description)
            VALUES (?, ?, ?, ?)
        """, (lock_id, slot_code, name, description))
    conn.commit()
    conn.close()


def update_key_calibration(lock_id, slot_code, adc_value, ohm_value=None):
    """Записывает эталонное сопротивление для гнезда."""
    conn = _connect()
    conn.execute("""
        UPDATE keys SET resistance_adc = ?, resistance_ohm = ?
        WHERE lock_id = ? AND slot_code = ?
    """, (adc_value, ohm_value, lock_id, slot_code))
    conn.commit()
    conn.close()


def delete_key_data(lock_id, slot_code):
    """Убирает данные ключа (но гнездо остаётся)."""
    conn = _connect()
    conn.execute("""
        UPDATE keys SET name = NULL, description = NULL,
            resistance_adc = NULL, resistance_ohm = NULL
        WHERE lock_id = ? AND slot_code = ?
    """, (lock_id, slot_code))
    conn.commit()
    conn.close()


def init_empty_slots(lock_id):
    """Создаёт записи для всех 48 гнёзд (A1..C16), если их нет."""
    conn = _connect()
    existing = {r["slot_code"] for r in conn.execute(
        "SELECT slot_code FROM keys WHERE lock_id = ?", (lock_id,)
    ).fetchall()}

    for row_letter in ("A", "B", "C"):
        for n in range(1, 17):
            code = f"{row_letter}{n}"
            if code not in existing:
                conn.execute("""
                    INSERT INTO keys (lock_id, slot_code) VALUES (?, ?)
                """, (lock_id, code))
    conn.commit()
    conn.close()


# ============ КАЛИБРОВКА ============

def find_calibration_conflicts(lock_id, tolerance=30):
    """
    Ищет гнёзда с похожими сопротивлениями (потенциальные конфликты).
    Возвращает список групп: [[slot1, slot2], [slot3, slot4, slot5], ...]
    """
    conn = _connect()
    rows = conn.execute("""
        SELECT slot_code, resistance_adc FROM keys
        WHERE lock_id = ? AND resistance_adc IS NOT NULL
        ORDER BY resistance_adc
    """, (lock_id,)).fetchall()
    conn.close()

    conflicts = []
    used = set()

    for i, r1 in enumerate(rows):
        if r1["slot_code"] in used:
            continue
        group = [r1["slot_code"]]
        for r2 in rows[i+1:]:
            if r2["slot_code"] in used:
                continue
            if abs(r1["resistance_adc"] - r2["resistance_adc"]) <= tolerance:
                group.append(r2["slot_code"])
                used.add(r2["slot_code"])
        if len(group) > 1:
            used.add(r1["slot_code"])
            conflicts.append(group)

    return conflicts


def get_calibration_progress(lock_id):
    """
    Возвращает:
      - calibrated: сколько гнёзд с ключами
      - total: всего гнёзд (48)
      - requested: идёт ли калибровка сейчас
      - scan_complete: калибровка завершена (ESP32 прислал результат)
    """
    conn = _connect()
    total = conn.execute("SELECT COUNT(*) FROM keys WHERE lock_id = ?", (lock_id,)).fetchone()[0]
    calibrated = conn.execute("""
        SELECT COUNT(*) FROM keys WHERE lock_id = ? AND resistance_adc IS NOT NULL
    """, (lock_id,)).fetchone()[0]
    row = conn.execute("""
        SELECT calibration_requested FROM locks WHERE id = ?
    """, (lock_id,)).fetchone()
    conn.close()

    requested = bool(row and row["calibration_requested"])

    return {
        "calibrated": calibrated,
        "total": total,
        "requested": requested,
        "scan_complete": (not requested) and calibrated > 0,
    }


def find_slot_by_adc(lock_id, adc_value, tolerance=30):
    """
    Ищет гнездо с ближайшим эталонным значением ADC.
    Возвращает slot_code или None.
    """
    conn = _connect()
    row = conn.execute("""
        SELECT slot_code, resistance_adc
        FROM keys
        WHERE lock_id = ? AND resistance_adc IS NOT NULL
        ORDER BY ABS(resistance_adc - ?) ASC
        LIMIT 1
    """, (lock_id, adc_value)).fetchone()
    conn.close()

    if not row:
        return None
    if abs(row["resistance_adc"] - adc_value) <= tolerance:
        return row["slot_code"]
    return None


# ============ СОСТОЯНИЕ ГНЁЗД И ЛОГИ ============

def update_slot_state(lock_id, slot_code, is_present, adc_value=None):
    """
    Обновляет состояние гнезда. Если изменилось — пишет в лог.
    Возвращает True, если состояние изменилось.
    """
    conn = _connect()
    row = conn.execute("""
        SELECT is_present, name FROM keys
        WHERE lock_id = ? AND slot_code = ?
    """, (lock_id, slot_code)).fetchone()

    if not row:
        conn.close()
        return False

    old_present = bool(row["is_present"])
    new_present = bool(is_present)

    # Обновляем текущее значение ADC
    if adc_value is not None:
        conn.execute("""
            UPDATE keys SET current_adc = ? WHERE lock_id = ? AND slot_code = ?
        """, (adc_value, lock_id, slot_code))

    if old_present == new_present:
        conn.commit()
        conn.close()
        return False

    # Состояние изменилось
    conn.execute("""
        UPDATE keys SET is_present = ?, last_change_at = CURRENT_TIMESTAMP
        WHERE lock_id = ? AND slot_code = ?
    """, (1 if is_present else 0, lock_id, slot_code))

    event_type = "key_taken" if not is_present else "key_returned"
    key_name = row["name"] or slot_code

    conn.execute("""
        INSERT INTO key_log (lock_id, slot_code, event_type, note)
        VALUES (?, ?, ?, ?)
    """, (lock_id, slot_code, event_type, f"Ключ «{key_name}»"))
    conn.commit()
    conn.close()
    return True


def link_key_event_to_user(lock_id, user_id, event_type=None):
    """
    Привязывает последние события без пользователя к конкретному user_id.
    Используется при открытии шкафчика — кто открыл, тот и взял.
    """
    conn = _connect()
    if event_type:
        conn.execute("""
            UPDATE key_log SET user_id = ?
            WHERE id IN (
                SELECT id FROM key_log
                WHERE lock_id = ? AND user_id IS NULL AND event_type = ?
                ORDER BY created_at DESC LIMIT 10
            )
        """, (user_id, lock_id, event_type))
    else:
        conn.execute("""
            UPDATE key_log SET user_id = ?
            WHERE id IN (
                SELECT id FROM key_log
                WHERE lock_id = ? AND user_id IS NULL
                ORDER BY created_at DESC LIMIT 10
            )
        """, (user_id, lock_id))
    conn.commit()
    conn.close()


def get_key_log(lock_id, limit=200):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.slot_code, l.event_type, l.note, l.created_at,
               u.full_name, u.username,
               k.name AS key_name
        FROM key_log l
        LEFT JOIN users u ON u.id = l.user_id
        LEFT JOIN keys  k ON k.lock_id = l.lock_id AND k.slot_code = l.slot_code
        WHERE l.lock_id = ?
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (lock_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_key_log(limit=500):
    conn = _connect()
    rows = conn.execute("""
        SELECT l.id, l.slot_code, l.event_type, l.note, l.created_at,
               u.full_name, u.username,
               k.name AS key_name,
               lk.name AS lock_name,
               lk.id AS lock_id,
               w.name AS warehouse_name
        FROM key_log l
        LEFT JOIN users u  ON u.id = l.user_id
        LEFT JOIN keys  k  ON k.lock_id = l.lock_id AND k.slot_code = l.slot_code
        LEFT JOIN locks lk ON lk.id = l.lock_id
        LEFT JOIN warehouses w ON w.id = lk.warehouse_id
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_keys_in_lock(lock_id):
    """Сколько ключей сейчас в шкафчике (is_present=1)."""
    conn = _connect()
    n = conn.execute("""
        SELECT COUNT(*) FROM keys WHERE lock_id = ? AND is_present = 1
    """, (lock_id,)).fetchone()[0]
    total = conn.execute("""
        SELECT COUNT(*) FROM keys
        WHERE lock_id = ? AND resistance_adc IS NOT NULL
    """, (lock_id,)).fetchone()[0]
    conn.close()
    return {"present": n, "total": total}


# ============ ЗАПРОС КАЛИБРОВКИ ============

def request_calibration(lock_id):
    """Помечает шкафчик для калибровки. ESP32 при следующем poll получит команду."""
    conn = _connect()
    conn.execute("""
        UPDATE locks
        SET calibration_requested = 1,
            calibration_started_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (lock_id,))
    conn.commit()
    conn.close()


def consume_calibration(lock_id):
    conn = _connect()
    conn.execute("""
        UPDATE locks SET calibration_requested = 0 WHERE id = ?
    """, (lock_id,))
    conn.commit()
    conn.close()


def is_calibration_requested(lock_id):
    conn = _connect()
    row = conn.execute("""
        SELECT calibration_requested FROM locks WHERE id = ?
    """, (lock_id,)).fetchone()
    conn.close()
    return bool(row and row["calibration_requested"])
