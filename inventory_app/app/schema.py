"""スキーマとマイグレーション。

MIGRATIONS に SQL を末尾へ追加していくだけ。適用済みの番号は PRAGMA user_version に保存される。
(SQLAlchemy 版 = SQLAlchemy + Alembic で作ったDBも、同じ構造なので、その進み具合のまま引き継ぐ)
"""

MIGRATIONS = [
    # 1: 初期スキーマ
    """
CREATE TABLE "user" (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE category (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  uses_color INTEGER NOT NULL DEFAULT 1,
  uses_size INTEGER NOT NULL DEFAULT 0,
  uses_variant_name INTEGER NOT NULL DEFAULT 0,
  is_printable INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE item (
  id INTEGER PRIMARY KEY,
  category_id INTEGER NOT NULL REFERENCES category(id),
  brand TEXT NOT NULL DEFAULT '',
  item_no TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1,
  UNIQUE (category_id, brand, item_no)
);
CREATE TABLE variant (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  color TEXT NOT NULL DEFAULT '',
  size TEXT NOT NULL DEFAULT '',
  variant_name TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (item_id, color, size, variant_name)
);
CREATE TABLE design (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE printed_product (
  id INTEGER PRIMARY KEY,
  design_id INTEGER NOT NULL REFERENCES design(id),
  variant_id INTEGER NOT NULL REFERENCES variant(id),
  quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (design_id, variant_id)
);
CREATE TABLE stock_movement (
  id INTEGER PRIMARY KEY,
  batch_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  variant_id INTEGER REFERENCES variant(id),
  printed_product_id INTEGER REFERENCES printed_product(id),
  delta INTEGER NOT NULL CHECK (delta <> 0),
  note TEXT NOT NULL DEFAULT '',
  user_id INTEGER NOT NULL REFERENCES "user"(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK ((variant_id IS NULL) <> (printed_product_id IS NULL))
);
CREATE INDEX ix_stock_movement_batch_id ON stock_movement(batch_id);
CREATE INDEX ix_stock_movement_created_at ON stock_movement(created_at);
""",
    # 2: 品番ごとの備考(コメント)。旧 item.note の内容は備考として引き継ぐ
    """
CREATE TABLE item_note (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  target TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  is_resolved INTEGER NOT NULL DEFAULT 0,
  user_id INTEGER NOT NULL REFERENCES "user"(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_item_note_item_id ON item_note(item_id);
INSERT INTO item_note (item_id, body, user_id)
  SELECT id, note, (SELECT MIN(id) FROM "user") FROM item
  WHERE note <> '' AND EXISTS (SELECT 1 FROM "user");
""",
]


def current_version(db) -> int:
    return db.execute("PRAGMA user_version").fetchone()["user_version"]


def is_current(db) -> bool:
    """DBが最新のスキーマか。"""
    return current_version(db) >= len(MIGRATIONS)


# SQLAlchemy 版(SQLAlchemy + Alembic)のリビジョン → 対応するスキーマ番号
SQLALCHEMY_REVISIONS = {"afd0c4cd0c7c": 1, "0002itemnote": 2}


def _table_exists(db, name) -> bool:
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _sqlalchemy_version(db) -> int:
    """user_version が未設定のDBが、SQLAlchemy 版で作られたものなら、どこまで進んでいるかを返す(新規DBは0)。"""
    if not _table_exists(db, "category"):
        return 0
    row = (db.execute("SELECT version_num FROM alembic_version").fetchone()
           if _table_exists(db, "alembic_version") else None)
    if row:
        rev = row["version_num"]
        if rev not in SQLALCHEMY_REVISIONS:
            raise RuntimeError(f"未対応のSQLAlchemy版リビジョンです: {rev!r}(この版が対応していない、より新しいリビジョンのDBの可能性があります)")
        return SQLALCHEMY_REVISIONS[rev]
    return 2 if _table_exists(db, "item_note") else 1  # Alembic の記録が無いDBは、表の有無で判断する


def migrate(db) -> int:
    v = current_version(db)
    if v == 0:
        adopted = _sqlalchemy_version(db)
        if adopted:  # SQLAlchemy 版で作成済みのDBを、その進み具合のまま引き継ぐ
            db.execute(f"PRAGMA user_version={adopted}")
            v = adopted
    for n, sql in enumerate(MIGRATIONS[v:], start=v + 1):
        try:
            db.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={n};\nCOMMIT;")
        except BaseException:
            if db.in_transaction:  # 途中で失敗したら、そのバージョンの変更を全部取り消す
                db.execute("ROLLBACK")
            raise
    return current_version(db)
