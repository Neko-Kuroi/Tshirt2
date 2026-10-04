import os

from app import create_app
from app.db import get_db, transaction
from app.schema import migrate
from config import TestConfig


def _runner(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def test_list_users_shows_name_role_and_status_but_not_the_hash(app):
    with app.app_context(), transaction() as db:
        db.execute('UPDATE "user" SET is_active = 0 WHERE username = ?', ("staff",))
        pw_hash = db.execute('SELECT password_hash FROM "user" LIMIT 1').fetchone()["password_hash"]
    r = _runner(app, "list-users")
    assert r.exit_code == 0
    lines = r.output.strip().splitlines()
    assert lines == ["admin\t管理者\t有効", "staff\tスタッフ\t無効"]
    assert pw_hash not in r.output


def test_list_users_with_no_users(tmp_path):
    class Cfg(TestConfig):
        DATABASE = str(tmp_path / "empty.db")
    app = create_app(Cfg)
    with app.app_context():
        migrate(get_db())
    r = _runner(app, "list-users")
    assert r.exit_code == 0 and "まだ登録されていません" in r.output


def test_list_users_on_missing_db_does_not_create_it(tmp_path):
    class Cfg(TestConfig):
        DATABASE = str(tmp_path / "missing.db")
    app = create_app(Cfg)
    r = _runner(app, "list-users")
    assert r.exit_code != 0 and "flask init-db" in r.output
    assert not os.path.exists(Cfg.DATABASE)


def test_list_users_on_uninitialized_db(tmp_path):
    class Cfg(TestConfig):
        DATABASE = str(tmp_path / "blank.db")
    open(Cfg.DATABASE, "w").close()                    # 空ファイル(テーブルなし)
    app = create_app(Cfg)
    r = _runner(app, "list-users")
    assert r.exit_code != 0 and "flask init-db" in r.output
