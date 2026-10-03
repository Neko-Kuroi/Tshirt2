import threading

import pytest

from app import repo
from app.db import connect, get_db, transaction
from app.schema import MIGRATIONS, current_version, migrate
from app.services import stock
from app.services.stock import StockError


def _user():
    return repo.get_user_by_name("admin")


def _tee(brand="Printstar"):
    row = get_db().execute("SELECT id FROM item WHERE brand = ?", (brand,)).fetchone()
    return repo.get_item(row["id"])


def _badge():
    row = get_db().execute("SELECT id FROM item WHERE item_no = '44mm'").fetchone()
    return repo.get_item(row["id"])


def _rows(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def _qty(variant_id):
    return repo.get_variant(variant_id)["quantity"]


def test_receive_creates_variant_and_movement(app):
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(_user(), v, 30, "納品書A")
    assert _qty(v["id"]) == 30
    (m,) = _rows("SELECT * FROM stock_movement")
    assert (m["kind"], m["delta"], m["variant_id"], m["note"]) == ("receive", 30, v["id"], "納品書A")


def test_same_spec_reuses_variant_and_brands_are_separate(app):
    with transaction():
        a1 = stock.get_or_create_variant(_tee("Printstar"), "黒", "M")
        a2 = stock.get_or_create_variant(_tee("Printstar"), " 黒 ", "M")
        b = stock.get_or_create_variant(_tee("United Athle"), "黒", "M")
    assert a1["id"] == a2["id"]
    assert a1["id"] != b["id"]


def test_unused_columns_are_blanked_and_required_ones_enforced(app):
    with transaction():
        v = stock.get_or_create_variant(_badge(), color="赤", size="M", variant_name="デザインA")
    assert (v["color"], v["size"], v["variant_name"]) == ("", "", "デザインA")
    with pytest.raises(StockError):
        stock.get_or_create_variant(_badge(), variant_name="")
    with pytest.raises(StockError):
        stock.get_or_create_variant(_tee(), "黒", "")


def test_cannot_go_negative_and_failure_rolls_everything_back(app):
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(_user(), v, 5)
    with pytest.raises(StockError):
        with transaction():
            stock.ship(_user(), v, 6)
    assert _qty(v["id"]) == 5
    assert len(_rows("SELECT * FROM stock_movement")) == 1


def test_convert_is_atomic_and_logs_both_sides(app):
    u = _user()
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(u, v, 20)
    with transaction():
        batch = stock.convert_to_printed(u, v, "ロゴA", used=10, output=9, note="1枚ミスプリント")
    assert _qty(v["id"]) == 10
    (p,) = repo.products()
    assert p["quantity"] == 9 and p["design_name"] == "ロゴA"
    moves = _rows("SELECT kind, delta FROM stock_movement WHERE batch_id = ? ORDER BY id", (batch,))
    assert [(m["kind"], m["delta"]) for m in moves] == [("print_use", -10), ("print_output", 9)]


def test_convert_failure_leaves_nothing_behind(app):
    u = _user()
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(u, v, 3)
    with pytest.raises(StockError):
        with transaction():
            stock.convert_to_printed(u, v, "ロゴA", used=5, output=5)
    assert _qty(v["id"]) == 3
    assert repo.products() == []
    assert _rows("SELECT * FROM design") == []  # デザインの新規登録も巻き戻る
    assert len(_rows("SELECT * FROM stock_movement")) == 1


def test_convert_validations(app):
    u = _user()
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(u, v, 10)
        bv = stock.get_or_create_variant(_badge(), variant_name="デザインA")
        stock.receive(u, bv, 10)
    for args in [(v, "A", 5, 6), (v, "", 5, 5), (bv, "A", 1, 1)]:  # 完成>使用 / デザイン名なし / プリント対象外
        with pytest.raises(StockError):
            with transaction():
                stock.convert_to_printed(u, *args)


def test_adjust_requires_reason_and_nonzero(app):
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(_user(), v, 10)
    for delta, note in [(-1, ""), (0, "理由")]:
        with pytest.raises(StockError):
            with transaction():
                stock.adjust(_user(), v, delta, note)
    with transaction():
        stock.adjust(_user(), v, -2, "棚卸し")
    assert _qty(v["id"]) == 8


def test_ship_printed_product(app):
    u = _user()
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(u, v, 10)
        stock.convert_to_printed(u, v, "ロゴA", 10, 10)
    (p,) = repo.products()
    with transaction():
        stock.ship(u, p, 4)
    assert repo.get_product(p["id"])["quantity"] == 6


# ---- 同時リクエスト(別接続・別スレッド)でも壊れない ------------------------------

def test_concurrent_receives_on_same_new_variant(app):
    """初回登録が重なっても、UNIQUE衝突で落ちず、数量の合計も狂わない。"""
    item_id = _tee()["id"]
    barrier, errors = threading.Barrier(8), []

    def worker():
        try:
            with app.app_context():
                user, item = _user(), repo.get_item(item_id)
                barrier.wait()
                for _ in range(5):
                    with transaction():
                        v = stock.get_or_create_variant(item, "黒", "M")
                        stock.receive(user, v, 1)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    (v,) = _rows("SELECT * FROM variant")
    assert v["quantity"] == 40
    assert _rows("SELECT COUNT(*) AS n FROM stock_movement")[0]["n"] == 40


def test_concurrent_ship_never_goes_negative(app):
    with transaction():
        v = stock.get_or_create_variant(_tee(), "黒", "M")
        stock.receive(_user(), v, 10)
    barrier, ok, failed = threading.Barrier(8), [], []

    def worker():
        with app.app_context():
            user, target = _user(), repo.get_variant(v["id"])
            barrier.wait()
            for _ in range(3):  # 8×3=24回 ×1枚 を出荷しようとする(在庫は10)
                try:
                    with transaction():
                        stock.ship(user, target, 1)
                    ok.append(1)
                except StockError:
                    failed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 10 and len(failed) == 14
    assert _qty(v["id"]) == 0


# ---- マイグレーション ------------------------------------------------------------

def test_migrate_is_idempotent_and_adopts_legacy_db(app):
    db = get_db()
    assert current_version(db) == len(MIGRATIONS)
    assert migrate(db) == len(MIGRATIONS)  # 2回目は何もしない
    db.execute("PRAGMA user_version=0")  # SQLAlchemy+Alembic時代のDB(user_version未設定)を再現
    assert migrate(db) == len(MIGRATIONS)
    assert _rows("SELECT COUNT(*) AS n FROM item")[0]["n"] == 3  # データは無傷


def test_foreign_keys_and_checks_are_enforced(app):
    import sqlite3
    db = connect(app.config["DATABASE"])
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO item (category_id, brand) VALUES (999, 'x')")  # 存在しないカテゴリー
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO variant (item_id, quantity) VALUES (1, -1)")  # マイナス在庫
    db.close()
