import sqlite3

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.security import generate_password_hash

from .. import repo
from ..db import get_db, transaction
from ..utils import to_int
from . import admin_required, bp


def _flag(name):
    return 1 if request.form.get(name) == "on" else 0


# ---- カテゴリー ------------------------------------------------------

@bp.route("/categories", methods=["GET", "POST"])
@admin_required
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("カテゴリー名を入力してください。", "danger")
        else:
            try:
                with transaction() as db:
                    count = db.execute("SELECT COUNT(*) AS n FROM category").fetchone()["n"]
                    db.execute(
                        "INSERT INTO category (name, sort_order, uses_color, uses_size, "
                        "uses_variant_name, is_printable) VALUES (?, ?, ?, ?, ?, ?)",
                        (name, count, _flag("uses_color"), _flag("uses_size"),
                         _flag("uses_variant_name"), _flag("is_printable")))
                flash(f"カテゴリーを追加しました: {name}", "success")
                return redirect(url_for("admin.categories"))
            except sqlite3.IntegrityError:
                flash("同名のカテゴリーが既にあります。", "danger")
    return render_template("admin/categories.html", cats=repo.categories())


@bp.route("/categories/<int:cid>", methods=["GET", "POST"])
@admin_required
def category_edit(cid):
    cat = repo.get_category(cid)
    if not cat:
        return render_template("error.html", code=404, message="カテゴリーが見つかりません。"), 404
    if request.method == "POST":
        raw_order = request.form.get("sort_order", "").strip()
        sort_order = to_int(raw_order) if raw_order else 0  # 空欄は0
        if sort_order is None:
            flash("表示順は整数で入力してください。", "danger")
        else:
            try:
                with transaction() as db:
                    # 在庫が既にあるカテゴリーでも、入力欄の出し分け設定は変更できる(既存データは消えない)
                    db.execute(
                        "UPDATE category SET name=?, sort_order=?, uses_color=?, uses_size=?, "
                        "uses_variant_name=?, is_printable=? WHERE id=?",
                        (request.form.get("name", "").strip() or cat["name"], sort_order,
                         _flag("uses_color"), _flag("uses_size"), _flag("uses_variant_name"),
                         _flag("is_printable"), cid))
                flash("カテゴリーを更新しました。", "success")
                return redirect(url_for("admin.categories"))
            except sqlite3.IntegrityError:
                flash("同名のカテゴリーが既にあります。", "danger")
    return render_template("admin/category_edit.html", cat=cat)


# ---- 品番(Item) ------------------------------------------------------

@bp.route("/items", methods=["GET", "POST"])
@admin_required
def items():
    if request.method == "POST":
        cat = repo.get_category(to_int(request.form.get("category_id"), 0))
        brand = request.form.get("brand", "").strip()
        item_no = request.form.get("item_no", "").strip()
        name = request.form.get("name", "").strip()
        if not cat:
            flash("カテゴリーを選んでください。", "danger")
        elif not (brand or item_no or name):
            flash("ブランド・品番・商品名のいずれかを入力してください。", "danger")
        else:
            try:
                with transaction() as db:
                    db.execute(
                        "INSERT INTO item (category_id, brand, item_no, name, note) VALUES (?, ?, ?, ?, ?)",
                        (cat["id"], brand, item_no, name, request.form.get("note", "").strip()))
                flash("品番を追加しました。", "success")
                return redirect(url_for("admin.items", category=cat["id"]))
            except sqlite3.IntegrityError:
                flash("同じカテゴリーに同じブランド・品番が既にあります。", "danger")
    cat_id = to_int(request.args.get("category"))
    return render_template("admin/items.html", cats=repo.categories(),
                           items=repo.items_admin(cat_id), cat_id=cat_id)


@bp.route("/items/<int:iid>", methods=["GET", "POST"])
@admin_required
def item_edit(iid):
    item = repo.get_item(iid)
    if not item:
        return render_template("error.html", code=404, message="品番が見つかりません。"), 404
    if request.method == "POST":
        try:
            with transaction() as db:
                db.execute(
                    "UPDATE item SET brand=?, item_no=?, name=?, note=?, is_active=? WHERE id=?",
                    (request.form.get("brand", "").strip(), request.form.get("item_no", "").strip(),
                     request.form.get("name", "").strip(), request.form.get("note", "").strip(),
                     _flag("is_active"), iid))
            flash("品番を更新しました。", "success")
            return redirect(url_for("admin.items", category=item["category_id"]))
        except sqlite3.IntegrityError:
            flash("同じカテゴリーに同じブランド・品番が既にあります。", "danger")
    return render_template("admin/item_edit.html", item=item)


# ---- ユーザー --------------------------------------------------------

@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < 8:
            flash("ユーザー名と、8文字以上のパスワードを入力してください。", "danger")
        else:
            role = "admin" if request.form.get("role") == "admin" else "staff"
            try:
                with transaction() as db:
                    db.execute('INSERT INTO "user" (username, password_hash, role) VALUES (?, ?, ?)',
                               (username, generate_password_hash(password), role))
                flash(f"ユーザーを追加しました: {username}", "success")
                return redirect(url_for("admin.users"))
            except sqlite3.IntegrityError:
                flash("そのユーザー名は既に使われています。", "danger")
    return render_template("admin/users.html", users=repo.users())


@bp.post("/users/<int:uid>/toggle")
@admin_required
def user_toggle(uid):
    u = repo.get_user(uid)
    if not u:
        return render_template("error.html", code=404, message="ユーザーが見つかりません。"), 404
    if u.id == current_user.id:
        flash("自分自身は無効化できません。", "danger")
    else:
        new_state = 0 if u.is_active else 1
        with transaction() as db:
            db.execute('UPDATE "user" SET is_active = ? WHERE id = ?', (new_state, uid))
        flash(f"{u.username} を{'有効' if new_state else '無効'}にしました。", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:uid>/password")
@admin_required
def user_password(uid):
    u = repo.get_user(uid)
    if not u:
        return render_template("error.html", code=404, message="ユーザーが見つかりません。"), 404
    pw = request.form.get("password", "")
    if len(pw) < 8:
        flash("パスワードは8文字以上にしてください。", "danger")
    else:
        with transaction() as db:
            db.execute('UPDATE "user" SET password_hash = ? WHERE id = ?',
                       (generate_password_hash(pw), uid))
        flash(f"{u.username} のパスワードを再設定しました。", "success")
    return redirect(url_for("admin.users"))
