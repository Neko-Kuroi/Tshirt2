"""旧版(SQLAlchemy + Alembic)DBの引き継ぎと、レビュー指摘の回帰テスト。"""
import re
import sqlite3
from pathlib import Path

import pytest
from flask import g
from werkzeug.security import generate_password_hash

from app import create_app, repo
from app.db import get_db, transaction
from app.schema import MIGRATIONS, migrate
from app.services import stock
from config import TestConfig

LEGACY_DDL = (Path(__file__).parent / "legacy_schema.sql").read_text(encoding="utf-8")
LEGACY_TS = "2026-10-04 00:20:10.887356"  # SQLAlchemy が書いていた(マイクロ秒付き)形式


# ---- 旧DBの再現(旧版が実際に作ったDDL + 旧版が書いた形式のデータ) -------------------

@pytest.fixture
def legacy_app(tmp_path):
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_DDL)
    raw.executescript(f"""
    INSERT INTO category VALUES (1,'Tシャツ',0,1,1,0,1), (2,'バッジ',1,0,0,1,0);
    INSERT INTO item VALUES (1,1,'Printstar','085-CVT','Tシャツ','旧メモ: 色違いを探す',1),
                            (2,2,'','44mm','缶バッジ','',1);
    INSERT INTO variant VALUES (1,1,'黒','M','',10,'{LEGACY_TS}');
    INSERT INTO design VALUES (1,'ロゴA','');
    INSERT INTO printed_product VALUES (1,1,1,9,'{LEGACY_TS}');
    """)
    raw.execute('INSERT INTO "user" VALUES (1,?,?,?,1,?)',
                ("neko", generate_password_hash("pass12345"), "admin", LEGACY_TS))
    raw.execute("INSERT INTO stock_movement VALUES (1,'b1','receive',1,NULL,20,'旧版で入荷',1,?)", (LEGACY_TS,))
    raw.commit()
    raw.close()

    class Cfg(TestConfig):
        DATABASE = path

    app = create_app(Cfg)
    with app.app_context():
        assert get_db().execute("PRAGMA user_version").fetchone()["user_version"] == 0
        migrate(get_db())
        yield app


def _login(app, username="neko", password="pass12345"):
    g.pop("_login_user", None)  # 直前に別の人でログインしていても、その人として扱わない
    c = app.test_client()
    r = c.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302 and r.headers["Location"].endswith("/"), "ログインできていない"
    g.pop("_login_user", None)
    return c


def test_legacy_db_is_adopted_and_old_note_is_kept(legacy_app):
    db = get_db()
    assert db.execute("PRAGMA user_version").fetchone()["user_version"] == len(MIGRATIONS)
    (n,) = repo.notes(include_resolved=True)
    assert "色違いを探す" in n["body"] and n["username"] == "neko"
    assert repo.get_variant(1)["quantity"] == 10  # 既存データは無傷


def test_legacy_db_all_pages_render(legacy_app):
    c = _login(legacy_app)
    for path in ["/", "/blank", "/printed", "/receive?item_id=1", "/convert", "/move", "/history",
                 "/notes", "/admin/items", "/admin/categories", "/admin/users"]:
        assert c.get(path).status_code == 200, path
    assert "10/04 09:20" in c.get("/history").get_data(as_text=True)  # UTC 00:20 → JST 09:20


def test_legacy_db_every_write_path_works(legacy_app):
    """旧DBには DEFAULT が無い。新コードの INSERT がそれに頼っていないことの確認。"""
    c = _login(legacy_app)
    assert c.post("/receive", data={"item_id": 1, "color": "黒", "size": "M", "qty": "5"}).status_code == 302
    assert c.post("/receive", data={"item_id": 1, "color": "白", "size": "L", "qty": "7"}).status_code == 302  # 新Variant
    assert c.post("/convert", data={"variant_id": 1, "design_new": "新デザイン", "used": "4", "output": "4"}).status_code == 302
    assert c.post("/move", data={"kind": "ship", "target": "v:1", "qty": "1"}).status_code == 302
    assert c.post("/move", data={"kind": "adjust", "target": "p:1", "qty": "-1", "note": "数え直し"}).status_code == 302
    assert c.post("/admin/users", data={"username": "staff1", "password": "longenough1"}).status_code == 302
    assert c.post("/admin/items", data={"category_id": 1, "brand": "United Athle", "item_no": "5001"}).status_code == 302
    assert c.post("/admin/categories", data={"name": "キャップ", "uses_color": "on"}).status_code == 302
    assert c.post("/notes", data={"item_id": 1, "target": "黒", "body": "入荷は最後"}).status_code == 302
    assert repo.get_variant(1)["quantity"] == 10 + 5 - 4 - 1
    assert repo.find_variant(1, "白", "L", "")["quantity"] == 7
    assert repo.get_user_by_name("staff1") is not None
    assert get_db().execute("SELECT COUNT(*) AS n FROM stock_movement").fetchone()["n"] == 1 + 1 + 1 + 2 + 1 + 1


# ---- 1: ブランドか品番のどちらか必須 ----------------------------------------------

def test_item_requires_brand_or_item_no(client, login):
    login("admin")
    before = get_db().execute("SELECT COUNT(*) AS n FROM item").fetchone()["n"]
    for data in [{"name": "名前だけ"}, {"brand": " ", "item_no": " ", "name": "空白だけ"}]:
        r = client.post("/admin/items", data={"category_id": 1, **data})
        assert r.status_code == 200 and "どちらかは入力" in r.get_data(as_text=True)
    assert get_db().execute("SELECT COUNT(*) AS n FROM item").fetchone()["n"] == before
    # 品番だけ・ブランドだけは登録できる
    assert client.post("/admin/items", data={"category_id": 2, "item_no": "55mm"}).status_code == 302
    assert client.post("/admin/items", data={"category_id": 2, "brand": "ブランドのみ"}).status_code == 302
    # 同じブランド・品番は重複エラー(原因を取り違えない)
    r = client.post("/admin/items", data={"category_id": 2, "item_no": "55mm"})
    assert "既にあります" in r.get_data(as_text=True)


def test_item_edit_cannot_blank_out_identity(client, login):
    login("admin")
    r = client.post("/admin/items/1", data={"brand": "", "item_no": "", "name": "x", "is_active": "on"})
    assert r.status_code == 200 and "どちらかは入力" in r.get_data(as_text=True)
    assert repo.get_item(1)["brand"] == "Printstar"
    assert client.post("/admin/items/1", data={"brand": "Printstar", "item_no": "085-CVT",
                                              "name": "改名", "is_active": "on"}).status_code == 302
    assert repo.get_item(1)["item_name"] == "改名"


def test_item_label_fallback_uses_item_id_not_row_id(app):
    with transaction() as db:  # 旧データなどで、全項目が空の品番
        iid = db.execute("INSERT INTO item (category_id, brand, item_no, name, note, is_active) "
                         "VALUES (1, '', '', '', '', 1)").lastrowid
        admin = repo.get_user_by_name("admin")
        v = stock.get_or_create_variant(repo.get_item(iid), "黒", "M")
        stock.receive(admin, v, 5)
        stock.convert_to_printed(admin, v, "ロゴA", 5, 5)
    (p,) = repo.products()
    assert p["id"] != iid
    assert f"Item#{iid}" in p["label"]
    assert f"Item#{iid}" in repo.recent_movements(1)[0]["target_label"]


# ---- 2: 使用中のカテゴリー設定はOFFにできない ---------------------------------------

def _tee_form(**flags):
    base = {"name": "Tシャツ", "sort_order": "0", "uses_color": "on", "uses_size": "on", "is_printable": "on"}
    base.update(flags)
    return {k: v for k, v in base.items() if v is not None}


def test_category_flag_in_use_cannot_be_turned_off(client, login, app):
    login("admin")
    with transaction():
        stock.receive(repo.get_user_by_name("admin"),
                      stock.get_or_create_variant(repo.get_item(1), "黒", "M"), 5)
    r = client.post("/admin/categories/1", data=_tee_form(uses_size=None))
    assert r.status_code == 200 and "サイズ" in r.get_data(as_text=True) and "OFFにできません" in r.get_data(as_text=True)
    assert repo.get_category(1)["uses_size"] == 1
    # 在庫が無い設定(種類名)・まだ使われていない設定はOFF/ONできる
    assert client.post("/admin/categories/1", data=_tee_form(uses_variant_name="on")).status_code == 302
    assert repo.get_category(1)["uses_variant_name"] == 1
    assert client.post("/admin/categories/1", data=_tee_form()).status_code == 302
    assert repo.get_category(1)["uses_variant_name"] == 0


def test_category_flag_without_stock_can_be_turned_off(client, login):
    login("admin")  # バッジ(id=2)は在庫が無い
    assert client.post("/admin/categories/2", data={"name": "バッジ", "sort_order": "1",
                                                   "uses_color": "on"}).status_code == 302
    assert repo.get_category(2)["uses_variant_name"] == 0


# ---- 4: パスワード変更で既存セッションが切れる --------------------------------------

def _get(client, path):
    """fixture が app context を握ったままだと、Flask-Login がリクエスト間で『ログイン中のユーザー』を
    使い回す(テスト特有。本番ではリクエストごとに別 context)。毎回捨ててから叩く。"""
    g.pop("_login_user", None)
    return client.get(path)


def _post(client, path, data):
    g.pop("_login_user", None)
    return client.post(path, data=data)


def test_admin_password_reset_logs_out_that_user(app):
    admin, staff = _login(app, "admin", "password123"), _login(app, "staff", "password123")
    assert _get(staff, "/").status_code == 200
    sid = repo.get_user_by_name("staff").id
    assert _post(admin, f"/admin/users/{sid}/password", {"password": "brandnew-pass"}).status_code == 302
    r = _get(staff, "/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert _get(_login(app, "staff", "brandnew-pass"), "/").status_code == 200  # 新パスワードでは入れる
    assert _get(admin, "/").status_code == 200  # 管理者自身は影響なし


def test_changing_own_password_keeps_own_session_only(app):
    me, other = _login(app, "staff", "password123"), _login(app, "staff", "password123")  # 同じ人の2端末
    r = _post(me, "/account/password", {"old": "password123", "new": "another-pass1", "confirm": "another-pass1"})
    assert r.status_code == 302
    assert _get(me, "/").status_code == 200                      # 変更した端末は入ったまま
    assert _get(other, "/").status_code == 302                   # もう1台は入り直し


# ---- 5: エラーページ ---------------------------------------------------------------

def test_japanese_error_pages(client, login, monkeypatch):
    login("staff")
    r = client.get("/admin/users")
    assert r.status_code == 403 and "権限" in r.get_data(as_text=True)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(repo, "recent_movements", locked)
    r = client.get("/")
    assert r.status_code == 503 and "混み合って" in r.get_data(as_text=True)


def test_csrf_is_enforced_when_enabled(tmp_path):
    class Cfg(TestConfig):
        DATABASE = str(tmp_path / "c.db")
        WTF_CSRF_ENABLED = True

    app = create_app(Cfg)
    with app.app_context():
        migrate(get_db())
        with transaction() as db:
            db.execute('INSERT INTO "user" (username, password_hash, role, is_active, created_at) '
                       "VALUES ('u', ?, 'staff', 1, datetime('now'))", (generate_password_hash("password123"),))
    c = app.test_client()
    r = c.post("/login", data={"username": "u", "password": "password123"})
    assert r.status_code == 400 and "有効期限" in r.get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
    r = c.post("/login", data={"csrf_token": token, "username": "u", "password": "password123"})
    assert r.status_code == 302


def test_open_redirect_is_blocked(client):
    for evil in ["https://evil.example/", "//evil.example.com", "/\\evil.example.com", "javascript:alert(1)"]:
        r = client.post(f"/login?next={evil}", data={"username": "admin", "password": "password123"})
        assert r.status_code == 302 and r.headers["Location"] == "/", evil
        client.post("/logout")


# ---- 6: 停止中の品番 ---------------------------------------------------------------

def test_inactive_item_cannot_receive_or_convert_but_can_ship(client, login):
    login("admin")
    client.post("/receive", data={"item_id": 1, "color": "黒", "size": "M", "qty": "10"})
    vid = get_db().execute("SELECT id FROM variant").fetchone()["id"]
    with transaction() as db:
        db.execute("UPDATE item SET is_active = 0 WHERE id = 1")
    r = client.post("/receive", data={"item_id": 1, "color": "黒", "size": "M", "qty": "1"}, follow_redirects=True)
    assert "使用停止中" in r.get_data(as_text=True)
    r = client.post("/convert", data={"variant_id": vid, "design_new": "A", "used": "1", "output": "1"},
                    follow_redirects=True)
    assert "使用停止中" in r.get_data(as_text=True)
    assert repo.get_variant(vid)["quantity"] == 10
    assert client.post("/move", data={"kind": "ship", "target": f"v:{vid}", "qty": "10"}).status_code == 302
    assert repo.get_variant(vid)["quantity"] == 0  # 残り在庫は出荷できる


# ---- 備考 --------------------------------------------------------------------------

def test_notes_flow(client, login):
    login("staff")
    msg = "これで入荷は最後。この色は別ブランドの近い色を、仕入れ先を変えて探す。"
    client.post("/receive", data={"item_id": 1, "color": "黒", "size": "M", "qty": "3"})
    r = client.post("/notes", data={"item_id": 1, "target": "黒", "body": msg, "back": "/blank?category=1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/blank?category=1")

    blank = client.get("/blank?category=1").get_data(as_text=True)
    assert msg in blank and "備考" in blank                        # 品番カードに出る
    assert re.search(r"黒\s*<span class=\"badge text-bg-warning\">備考</span>", blank)  # 該当の色の行に印
    assert msg in client.get("/receive?item_id=1").get_data(as_text=True)    # 入荷画面でも見える
    dash = client.get("/").get_data(as_text=True)
    assert msg in dash and "未対応の備考" in dash                    # ダッシュボードにも出る
    assert msg in client.get("/notes").get_data(as_text=True)

    nid = repo.notes()[0]["id"]
    client.post(f"/notes/{nid}/resolve", data={})                   # 対応済みにする
    assert msg not in client.get("/notes").get_data(as_text=True)
    assert msg in client.get("/notes?all=1").get_data(as_text=True)
    assert repo.open_note_count() == 0
    client.post(f"/notes/{nid}/resolve", data={})                   # 戻す
    assert repo.open_note_count() == 1


def test_notes_validation_permissions_and_escaping(client, login):
    login("staff")
    for data in [{"item_id": 1, "body": "   "}, {"item_id": 999, "body": "x"}, {"item_id": 1, "body": "x" * 1001},
                 {"item_id": 1, "target": "t" * 65, "body": "x"}]:
        client.post("/notes", data=data)
    assert repo.open_note_count() == 0

    client.post("/notes", data={"item_id": 1, "body": "<script>alert(1)</script>", "back": "https://evil.example"})
    r = client.post("/notes", data={"item_id": 1, "body": "確認", "back": "https://evil.example"})
    assert "evil.example" not in r.headers["Location"]              # 戻り先は同一サイトのみ
    page = client.get("/notes").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page   # エスケープされる

    nid = repo.notes()[0]["id"]
    assert client.post(f"/notes/{nid}/delete", data={}).status_code == 403  # スタッフは削除できない
    client.post("/logout")
    login("admin")
    assert client.post(f"/notes/{nid}/delete", data={}).status_code == 302
    assert nid not in [n["id"] for n in repo.notes(include_resolved=True)]
    assert client.post("/notes/99999/resolve", data={}).status_code == 404
