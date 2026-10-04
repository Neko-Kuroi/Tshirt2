import os
import sqlite3
from datetime import datetime, timedelta, timezone

import click
from flask import Flask, Response, render_template, request
from flask_login import LoginManager
from flask_wtf.csrf import CSRFError, CSRFProtect

login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "ログインしてください。"
login_manager.login_message_category = "warning"

JST = timezone(timedelta(hours=9))

_SCHEMA_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>準備中</title></head>
<body style="font-family:sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem">
<h1 style="font-size:1.3rem">データベースの更新が必要です</h1>
<p>管理者に連絡してください。</p>
<p style="color:#666;font-size:.9rem">(管理者向け: サーバーで <code>flask init-db</code> を実行すると、再起動なしで使えるようになります)</p>
</body></html>"""


def create_app(config_object="config.Config"):
    app = Flask(__name__)
    if isinstance(config_object, str):
        from werkzeug.utils import import_string
        config_object = import_string(config_object)
    app.config.from_object(config_object)

    from .utils import BoundedIntegerConverter
    app.url_map.converters["int"] = BoundedIntegerConverter  # blueprint登録より前に差し替える

    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from . import repo
    from .db import close_db, get_db

    app.teardown_appcontext(close_db)
    login_manager.init_app(app)
    login_manager.user_loader(repo.load_session_user)
    csrf.init_app(app)

    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .inventory import bp as inventory_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(404)
    def not_found(_):
        return error_page(404, "ページが見つかりません。")

    @app.errorhandler(403)
    def forbidden(_):
        return error_page(403, "この操作をする権限がありません。")

    @app.errorhandler(CSRFError)
    def csrf_failed(_):
        return error_page(400, "画面の有効期限が切れたか、不正な送信です。ページを開き直して、もう一度お試しください。")

    @app.errorhandler(sqlite3.OperationalError)
    def db_busy(e):
        app.logger.exception("database error")
        if "locked" in str(e) or "busy" in str(e):
            return error_page(503, "ただいま混み合っています。数秒おいて、もう一度お試しください。")
        return error_page(500, "データベースでエラーが起きました。管理者に連絡してください。")

    @app.errorhandler(500)
    def server_error(_):
        return error_page(500, "エラーが起きました。管理者に連絡してください。")

    @app.before_request
    def require_current_schema():
        """DBが未作成/古いままだと、画面を開いて初めて500になる。先に分かりやすく止める。"""
        if app.extensions.get("schema_ok") or request.endpoint == "static":
            return None
        from .schema import is_current
        path = app.config["DATABASE"]
        # 存在しないファイルに get_db() すると、空のDBが勝手に作られてしまうので先に確かめる
        if os.path.exists(path) and is_current(get_db()):
            app.extensions["schema_ok"] = True  # 更新が済めば、それ以降は確認しない
            return None
        if not app.extensions.get("schema_warned"):
            app.extensions["schema_warned"] = True
            app.logger.error("DBが未作成か古いバージョンです。`flask init-db` を実行してください: %s", path)
        return Response(_SCHEMA_PAGE, 503, content_type="text/html; charset=utf-8",
                        headers={"Retry-After": "60"})

    @app.after_request
    def no_cache(resp):
        # 在庫は常に最新を見せたいので、HTMLはキャッシュさせない
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.template_filter("jst")
    def jst(value):
        """DBはUTC保存。表示は日本時間(JST)。"""
        if not value:
            return ""
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%m/%d %H:%M")

    register_cli(app)
    return app


def register_cli(app):
    from werkzeug.security import generate_password_hash

    from .db import get_db, transaction
    from .schema import migrate
    from .utils import is_unique_violation

    @app.cli.command("init-db")
    def init_db():
        """DBを作成/更新する(何度実行しても安全)。"""
        os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
        click.echo(f"スキーマのバージョン: {migrate(get_db())}")

    @app.cli.command("seed")
    def seed():
        """初期カテゴリーを登録する(既にあれば何もしない)。"""
        db = get_db()
        if db.execute("SELECT 1 FROM category").fetchone():
            click.echo("カテゴリーは登録済みです。")
            return
        defaults = [
            # name, color, size, variant_name, printable
            ("Tシャツ", 1, 1, 0, 1),
            ("キャップ", 1, 0, 0, 1),
            ("カバン", 1, 0, 0, 1),
            ("バッジ", 0, 0, 1, 0),
        ]
        with transaction():
            for i, (name, c, s, v, p) in enumerate(defaults):
                db.execute(
                    "INSERT INTO category (name, sort_order, uses_color, uses_size, "
                    "uses_variant_name, is_printable) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, i, c, s, v, p))
        click.echo("初期カテゴリーを登録しました。")

    @app.cli.command("list-users")
    def list_users():
        """登録済みのユーザー名・権限・有効/無効を表示する(パスワードは表示しない)。"""
        path = app.config["DATABASE"]
        # 存在しないファイルに接続すると空のDBが勝手に作られてしまうので、先に確かめる
        if not os.path.exists(path):
            raise click.ClickException(f"DBがありません({path})。先に flask init-db を実行してください。")
        try:
            rows = get_db().execute(
                'SELECT username, role, is_active FROM "user" ORDER BY id').fetchall()
        except sqlite3.OperationalError:
            raise click.ClickException("DBが初期化されていません。先に flask init-db を実行してください。")
        if not rows:
            click.echo("ユーザーはまだ登録されていません。(flask create-user --admin 名前 で作成できます)")
            return
        for r in rows:
            role = "管理者" if r["role"] == "admin" else "スタッフ"
            click.echo(f"{r['username']}\t{role}\t{'有効' if r['is_active'] else '無効'}")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--admin", is_flag=True, help="管理者として作成")
    @click.password_option()
    def create_user(username, admin, password):
        """ユーザーを作成する。"""
        username = username.strip()  # ログイン画面・管理画面と同じ扱い。空白付きだと二度とログインできない
        if not username:
            raise click.ClickException("ユーザー名を入力してください。")
        role = "admin" if admin else "staff"
        try:
            with transaction() as db:
                db.execute('INSERT INTO "user" (username, password_hash, role, is_active, created_at) '
                           "VALUES (?, ?, ?, 1, datetime('now'))",
                           (username, generate_password_hash(password), role))
        except sqlite3.IntegrityError as e:
            if not is_unique_violation(e):
                raise
            raise click.ClickException("そのユーザー名は既に使われています。")
        click.echo(f"作成しました: {username} ({role})")
