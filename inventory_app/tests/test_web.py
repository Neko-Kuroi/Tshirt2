import pytest

import app.db as db_module
from app import repo
from app.db import get_db, transaction
from app.services import stock


def _item_id(brand="Printstar"):
    return get_db().execute("SELECT id FROM item WHERE brand = ?", (brand,)).fetchone()["id"]


def test_login_required(client):
    for path in ["/", "/blank", "/printed", "/receive", "/convert", "/move", "/history", "/admin/users"]:
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path


def test_login_logout_and_open_redirect_blocked(client):
    r = client.post("/login?next=https://evil.example/", data={"username": "admin", "password": "password123"})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert client.post("/logout").status_code == 302
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 200 and "違います" in r.get_data(as_text=True)


def test_disabled_user_cannot_login(client, app):
    with transaction() as db:
        db.execute("UPDATE \"user\" SET is_active = 0 WHERE username = 'staff'")
    r = client.post("/login", data={"username": "staff", "password": "password123"})
    assert r.status_code == 200


def test_staff_cannot_access_admin(client, login):
    login("staff")
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/items").status_code == 403


def test_pages_render_and_full_flow(client, login, app):
    login("admin")
    item_id = _item_id()
    for path in ["/", "/blank", "/printed", "/receive", "/convert", "/move", "/history",
                 "/admin/items", "/admin/categories", "/admin/users", "/change-me-not-found"]:
        want = 404 if "not-found" in path else 200
        assert client.get(path).status_code == want, path

    assert client.post("/receive", data={"item_id": item_id, "color": "黒", "size": "M", "qty": "20"}).status_code == 302
    client.post("/receive", data={"item_id": item_id, "color": "黒", "size": "L", "qty": "5"})
    html = client.get("/blank").get_data(as_text=True)
    assert "Printstar" in html and ">20<" in html and ">25<" in html  # 行合計 25

    vid = get_db().execute("SELECT id FROM variant WHERE size = 'M'").fetchone()["id"]
    r = client.post("/convert", data={"variant_id": vid, "design": "", "design_new": "ロゴA",
                                      "used": "10", "output": "9"})
    assert r.status_code == 302
    assert "ロゴA" in client.get("/printed").get_data(as_text=True)
    assert "プリント完成" in client.get("/history").get_data(as_text=True)

    # 在庫超過はエラー表示で、何も変わらない
    r = client.post("/convert", data={"variant_id": vid, "design_new": "ロゴA", "used": "999", "output": "1"},
                    follow_redirects=True)
    assert "在庫が足りません" in r.get_data(as_text=True)
    assert repo.get_variant(vid)["quantity"] == 10
    assert get_db().execute("SELECT COUNT(*) AS n FROM stock_movement").fetchone()["n"] == 4


def test_receive_rejects_garbage_qty(client, login, app):
    login("admin")
    for bad in ["", "abc", "-3", "0"]:
        r = client.post("/receive", data={"item_id": _item_id(), "color": "黒", "size": "M", "qty": bad},
                        follow_redirects=True)
        assert "1以上の整数" in r.get_data(as_text=True), bad
    assert get_db().execute("SELECT COUNT(*) AS n FROM stock_movement").fetchone()["n"] == 0
    assert get_db().execute("SELECT COUNT(*) AS n FROM variant").fetchone()["n"] == 0  # 空のVariantも残さない


def test_move_ship_and_adjust(client, login, app):
    login("admin")
    client.post("/receive", data={"item_id": _item_id(), "color": "黒", "size": "M", "qty": "10"})
    vid = get_db().execute("SELECT id FROM variant").fetchone()["id"]
    client.post("/move", data={"kind": "ship", "target": f"v:{vid}", "qty": "3"})
    assert repo.get_variant(vid)["quantity"] == 7
    client.post("/move", data={"kind": "adjust", "target": f"v:{vid}", "qty": "-2", "note": "数え間違い"})
    assert repo.get_variant(vid)["quantity"] == 5
    r = client.post("/move", data={"kind": "adjust", "target": f"v:{vid}", "qty": "1", "note": ""},
                    follow_redirects=True)
    assert "理由" in r.get_data(as_text=True)
    assert repo.get_variant(vid)["quantity"] == 5


# ---- 管理画面: 数値でない入力で500にならない ----------------------------------

def test_admin_invalid_numbers_do_not_crash(client, login):
    login("admin")
    r = client.post("/admin/categories/1", data={"name": "Tシャツ", "sort_order": "abc"})
    assert r.status_code == 200 and "整数" in r.get_data(as_text=True)
    r = client.post("/admin/items", data={"category_id": "abc", "brand": "X"})
    assert r.status_code == 200 and "カテゴリーを選んでください" in r.get_data(as_text=True)


def test_admin_invalid_sort_order_changes_nothing_and_blank_means_zero(client, login):
    login("admin")
    name = repo.get_category(1)["name"]
    client.post("/admin/categories/1", data={"name": "改名", "sort_order": "abc"})
    assert repo.get_category(1)["name"] == name  # 不正入力では何も保存されない
    r = client.post("/admin/categories/1", data={"name": name, "sort_order": ""})
    assert r.status_code == 302
    assert repo.get_category(1)["sort_order"] == 0


def test_admin_user_management(client, login):
    login("admin")
    client.post("/admin/users", data={"username": "new", "password": "longenough1", "role": "staff"})
    assert repo.get_user_by_name("new") is not None
    assert "既に使われています" in client.post(
        "/admin/users", data={"username": "new", "password": "longenough1"}).get_data(as_text=True)
    uid = repo.get_user_by_name("new").id
    client.post(f"/admin/users/{uid}/toggle")
    assert repo.get_user(uid).is_active is False
    admin_id = repo.get_user_by_name("admin").id
    client.post(f"/admin/users/{admin_id}/toggle")  # 自分自身は無効化できない
    assert repo.get_user(admin_id).is_active is True


# ---- N+1の防止: 表示行が増えてもSQL発行数が増えない -----------------------------

def _seed_printed(n, start):
    admin = repo.get_user_by_name("admin")
    for i in range(start, start + n):
        with transaction() as db:
            item_id = db.execute(
                "INSERT INTO item (category_id, brand, item_no, name) VALUES (1, ?, ?, 'T')",
                (f"Brand{i}", f"No{i}")).lastrowid
            v = stock.get_or_create_variant(repo.get_item(item_id), "黒", "M")
            stock.receive(admin, v, 10)
            stock.convert_to_printed(admin, v, f"Design{i}", 5, 5)
            db.execute("INSERT INTO item_note (item_id, target, body, user_id) VALUES (?, '黒', ?, ?)",
                       (item_id, f"備考{i}", admin.id))


def _count_queries(client, path, monkeypatch):
    statements = []
    real = db_module.connect

    def counting(p):
        conn = real(p)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(db_module, "connect", counting)
    assert client.get(path).status_code == 200
    monkeypatch.setattr(db_module, "connect", real)
    return len(statements)


@pytest.mark.parametrize("path", ["/", "/blank?category=1", "/printed", "/convert", "/move", "/history", "/notes"])
def test_query_count_does_not_grow_with_rows(client, login, monkeypatch, path):
    login("admin")
    _seed_printed(3, 0)
    small = _count_queries(client, path, monkeypatch)
    _seed_printed(12, 3)
    large = _count_queries(client, path, monkeypatch)
    assert large == small, f"{path}: {small} -> {large}"
