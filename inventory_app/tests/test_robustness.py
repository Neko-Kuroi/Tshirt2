"""不正な入力(巨大な整数)・数量の上限・DBスキーマが古い場合の挙動。"""
import os

import pytest

from app import create_app
from app.db import get_db, transaction
from app.schema import MIGRATIONS, migrate
from config import TestConfig

BIG = "9" * 30          # SQLite の整数(64bit)に入らない値
INT64_MAX = 2**63 - 1   # 入るが、足し算でオーバーフローする値


def _quantity(app, variant_id=1):
    with app.app_context():
        return get_db().execute(
            "SELECT quantity, typeof(quantity) AS t FROM variant WHERE id = ?", (variant_id,)).fetchone()


# ---- 巨大な整数で500にならない -------------------------------------------------

@pytest.mark.parametrize("path, want", [
    (f"/receive?item_id={BIG}", 200),
    (f"/history?page={BIG}", 200),
    (f"/history?page={2**62}", 200),          # 64bitには入るが、OFFSET の計算で溢れる値
    (f"/blank?category={BIG}", 200),
    (f"/admin/items?category={BIG}", 200),
    (f"/admin/items/{BIG}", 404),             # URL中の <int:...>
    (f"/admin/categories/{BIG}", 404),
])
def test_get_with_huge_numbers_does_not_crash(client, login, path, want):
    login("admin")
    assert client.get(path).status_code == want


@pytest.mark.parametrize("path, data, want", [
    ("/receive", {"item_id": "1", "color": "赤", "size": "M", "qty": BIG}, 200),
    ("/receive", {"item_id": BIG, "color": "赤", "size": "M", "qty": "1"}, 200),
    ("/convert", {"variant_id": BIG, "used": "1", "output": "1", "design": "x"}, 200),
    ("/move", {"target": f"v:{BIG}", "kind": "ship", "qty": "1"}, 200),
    ("/move", {"target": f"p:{BIG}", "kind": "ship", "qty": "1"}, 200),
    ("/notes", {"item_id": BIG, "body": "x"}, 302),
    (f"/notes/{BIG}/resolve", {}, 404),
    ("/admin/categories/1", {"name": "Tシャツ", "sort_order": BIG}, 200),
    ("/admin/items", {"category_id": BIG, "brand": "x"}, 200),
    (f"/admin/users/{BIG}/toggle", {}, 404),
])
def test_post_with_huge_numbers_does_not_crash(client, login, path, data, want):
    login("admin")
    assert client.post(path, data=data).status_code == want


def test_huge_receive_leaves_no_variant_behind(app, client, login):
    login("admin")
    client.post("/receive", data={"item_id": "1", "color": "赤", "size": "M", "qty": BIG})
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) AS n FROM variant").fetchone()["n"] == 0


# ---- 数量の上限(SQLiteは整数の足し算が溢れると、エラーにせず REAL にしてしまう) ---------

def _receive(client, qty, color="赤"):
    return client.post("/receive", data={"item_id": "1", "color": color, "size": "M", "qty": str(qty)},
                       follow_redirects=True)


def test_quantity_stays_integer_even_when_a_huge_qty_is_sent(app, client, login):
    login("admin")
    _receive(client, 5)
    r = _receive(client, INT64_MAX)
    assert "1,000,000以下" in r.get_data(as_text=True)
    assert dict(_quantity(app)) == {"quantity": 5, "t": "integer"}


def test_per_operation_limit_is_enforced_everywhere(app, client, login):
    from app.utils import MAX_QTY
    login("admin")
    assert _receive(client, MAX_QTY + 1).status_code == 200
    assert _quantity(app) is None                               # 上限超えは登録されない
    _receive(client, MAX_QTY)
    assert dict(_quantity(app)) == {"quantity": MAX_QTY, "t": "integer"}
    for kind, qty in (("ship", MAX_QTY + 1), ("adjust", MAX_QTY + 1), ("adjust", -(MAX_QTY + 1))):
        r = client.post("/move", data={"target": "v:1", "kind": kind, "qty": str(qty), "note": "x"},
                        follow_redirects=True)
        assert "1,000,000以下" in r.get_data(as_text=True), (kind, qty)
    assert dict(_quantity(app)) == {"quantity": MAX_QTY, "t": "integer"}
    r = client.post("/convert", data={"variant_id": "1", "used": str(MAX_QTY + 1), "output": "1", "design": "x"},
                    follow_redirects=True)
    assert "1,000,000以下" in r.get_data(as_text=True)


# ---- DBが未作成/古いままでも、500ではなく分かりやすく案内する ------------------------

def _bare_app(tmp_path):
    class Cfg(TestConfig):
        DATABASE = str(tmp_path / "bare.db")
    return create_app(Cfg), Cfg.DATABASE


def test_missing_db_gives_503_and_does_not_create_the_file(tmp_path):
    app, path = _bare_app(tmp_path)
    c = app.test_client()
    r = c.get("/login")
    assert r.status_code == 503 and "flask init-db" in r.get_data(as_text=True)
    assert not os.path.exists(path)
    assert c.get("/static/app.css").status_code == 200       # 静的ファイルは止めない


def test_outdated_schema_gives_503_then_recovers_after_migration_without_restart(tmp_path):
    app, path = _bare_app(tmp_path)
    with app.app_context():
        db = get_db()
        db.executescript(MIGRATIONS[0])
        db.execute("PRAGMA user_version=1")                  # 備考テーブルが無い古い状態
    c = app.test_client()
    assert c.get("/login").status_code == 503
    with app.app_context():
        migrate(get_db())                                    # 別プロセスで init-db した想定
    assert c.get("/login").status_code == 200


def test_init_db_cli_still_works_on_missing_db(tmp_path):
    app, path = _bare_app(tmp_path)
    r = app.test_cli_runner().invoke(args=["init-db"])
    assert r.exit_code == 0 and os.path.exists(path)
    assert app.test_client().get("/login").status_code == 200


# ---- 改ざん・古いセッションCookie(署名は正しくても、中身が想定外)でも500にならない -----------

@pytest.mark.parametrize("value", [f"{BIG}:abc", "²:abc", "-1:abc", ":", "abc", ""])
def test_unexpected_session_user_id_does_not_crash(client, value):
    with client.session_transaction() as s:
        s["_user_id"] = value
        s["_fresh"] = True
    r = client.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
