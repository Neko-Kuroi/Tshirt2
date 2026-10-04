from collections import OrderedDict

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import repo
from ..db import transaction
from ..models import KINDS
from ..services import stock
from ..services.stock import StockError
from ..utils import safe_next, size_key, to_int
from . import bp


@bp.before_request
@login_required
def _require_login():
    pass


# ---- ダッシュボード --------------------------------------------------

@bp.route("/")
def dashboard():
    return render_template(
        "inventory/dashboard.html", cats=repo.categories(), blank=repo.blank_totals(),
        printed=repo.printed_totals(), recent=repo.recent_movements(8),
        notes=repo.notes(limit=5), note_count=repo.open_note_count())


# ---- 無地在庫(色×サイズのマトリクス) ---------------------------------

@bp.route("/blank")
def blank_list():
    cats = repo.categories()
    cat_id = to_int(request.args.get("category"))
    cat = next((c for c in cats if c["id"] == cat_id), cats[0] if cats else None)
    q = request.args.get("q", "").strip()
    items = []
    if cat:
        by_item = {}
        for v in repo.variants_in_category(cat["id"]):
            by_item.setdefault(v["item_id"], []).append(v)
        notes_by_item = {}
        for n in repo.notes(category_id=cat["id"]):
            notes_by_item.setdefault(n["item_id"], []).append(n)
        items = [build_matrix(it, cat, by_item.get(it["id"], []), notes_by_item.get(it["id"], []))
                 for it in repo.items_in_category(cat["id"], q)]
    return render_template("inventory/blank_list.html", cats=cats, cat=cat, items=items, q=q)


def note_targets(variants):
    """備考の対象に選べる値(その品番にある色・種類名)。"""
    return sorted({t for v in variants for t in (v["color"], v["variant_name"]) if t})


def build_matrix(item, cat, variants, notes=()):
    """行=色/種類名、列=サイズ(サイズ無しカテゴリーは1列)。備考のある行には印を付ける。"""
    noted = {n["target"] for n in notes if n["target"]}
    sizes = sorted({v["size"] for v in variants}, key=size_key) if cat["uses_size"] else [""]
    rows = OrderedDict()
    for v in variants:  # repo 側で variant_name, color 順に並んでいる
        key = (v["variant_name"], v["color"])
        row = rows.setdefault(key, {
            "label": " / ".join(p for p in key if p) or "-", "cells": {}, "total": 0,
            "has_note": bool(noted & {k for k in key if k})})
        row["cells"][v["size"] if cat["uses_size"] else ""] = v
        row["total"] += v["quantity"]
    return {"item": item, "sizes": sizes, "rows": list(rows.values()), "notes": list(notes),
            "targets": note_targets(variants), "total": sum(r["total"] for r in rows.values())}


# ---- プリント済み在庫 ------------------------------------------------

@bp.route("/printed")
def printed_list():
    q = request.args.get("q", "").strip()
    groups = OrderedDict()
    for p in repo.products(q):
        groups.setdefault(p["design_name"], []).append(p)
    return render_template("inventory/printed_list.html", groups=groups, q=q)


# ---- 入荷 ------------------------------------------------------------

@bp.route("/receive", methods=["GET", "POST"])
def receive():
    selected = to_int(request.values.get("item_id"))
    if request.method == "POST":
        try:
            item = repo.get_item(selected) if selected else None
            if not item:
                raise StockError("品番を選んでください。")
            qty = to_int(request.form.get("qty"))
            with transaction():
                v = stock.get_or_create_variant(
                    item, request.form.get("color"), request.form.get("size"),
                    request.form.get("variant_name"))
                stock.receive(current_user, v, qty, request.form.get("note"))
            flash(f"入荷を登録しました: {v['label']} +{qty}", "success")
            return redirect(url_for("inventory.receive", item_id=item["id"]))
        except StockError as e:
            flash(str(e), "danger")
    item_notes = repo.notes(item_id=selected) if selected else []
    targets = note_targets(repo.variants_of_item(selected)) if selected else []
    return render_template("inventory/receive.html", items=repo.items_for_select(),
                           selected=selected, colors=repo.used_colors(), form=request.form,
                           item_notes=item_notes, targets=targets)


# ---- プリント変換 ----------------------------------------------------

@bp.route("/convert", methods=["GET", "POST"])
def convert():
    if request.method == "POST":
        try:
            v = repo.get_variant(to_int(request.form.get("variant_id"), 0))
            if not v:
                raise StockError("無地在庫を選んでください。")
            used = to_int(request.form.get("used"))
            output = to_int(request.form.get("output"))
            design_name = request.form.get("design_new", "").strip() or request.form.get("design", "")
            with transaction():
                stock.convert_to_printed(current_user, v, design_name, used, output,
                                         request.form.get("note"))
            flash(f"プリント変換しました: {design_name} / {v['label']} "
                  f"(使用 {used} → 完成 {output})", "success")
            return redirect(url_for("inventory.convert"))
        except StockError as e:
            flash(str(e), "danger")
    return render_template("inventory/convert.html", variants=repo.printable_variants_in_stock(),
                           designs=repo.designs(), form=request.form)


# ---- 出荷・棚卸し調整 ------------------------------------------------

@bp.route("/move", methods=["GET", "POST"])
def move():
    if request.method == "POST":
        try:
            target = repo.get_target(request.form.get("target"))
            if not target:
                raise StockError("対象の在庫を選んでください。")
            kind = request.form.get("kind")
            qty = to_int(request.form.get("qty"))
            note = request.form.get("note")
            with transaction():
                if kind == "ship":
                    stock.ship(current_user, target, qty, note)
                elif kind == "adjust":
                    stock.adjust(current_user, target, qty, note)
                else:
                    raise StockError("操作の種類を選んでください。")
            shown = f"{qty:+d}" if kind == "adjust" else f"-{qty}"
            flash(f"{KINDS[kind]}を登録しました: {target['label']} ({shown})", "success")
            return redirect(url_for("inventory.move"))
        except StockError as e:
            flash(str(e), "danger")
    return render_template("inventory/move.html", variants=repo.all_variants(),
                           products=repo.products(), form=request.form)


# ---- 履歴 ------------------------------------------------------------

@bp.route("/history")
def history():
    page = max(to_int(request.args.get("page"), 1), 1)
    kind = request.args.get("kind", "")
    pagination = repo.movements_page(kind, page, current_app.config["HISTORY_PER_PAGE"])
    return render_template("inventory/history.html", pagination=pagination, kind=kind, kinds=KINDS)


# ---- 備考 ------------------------------------------------------------

@bp.route("/notes")
def notes():
    show_all = request.args.get("all") == "1"
    return render_template("inventory/notes.html", notes=repo.notes(include_resolved=show_all),
                           show_all=show_all)


@bp.post("/notes")
def note_add():
    back = safe_next(request.form.get("back")) or url_for("inventory.notes")
    item_id = to_int(request.form.get("item_id"), 0)
    body = request.form.get("body", "").strip()
    target = request.form.get("target", "").strip()
    if not repo.get_item(item_id):
        flash("品番が見つかりません。", "danger")
    elif not body:
        flash("備考の内容を入力してください。", "danger")
    elif len(body) > 1000 or len(target) > 64:
        flash("備考は1000文字まで、対象は64文字までです。", "danger")
    else:
        with transaction() as db:
            db.execute("INSERT INTO item_note (item_id, target, body, is_resolved, user_id, created_at) "
                       "VALUES (?, ?, ?, 0, ?, datetime('now'))",
                       (item_id, target, body, current_user.id))
        flash("備考を追加しました。", "success")
    return redirect(back)


@bp.post("/notes/<int:note_id>/resolve")
def note_resolve(note_id):
    if not repo.get_note(note_id):
        abort(404)
    with transaction() as db:  # 対応済み ⇔ 未対応 を切り替える
        db.execute("UPDATE item_note SET is_resolved = 1 - is_resolved WHERE id = ?", (note_id,))
    return redirect(safe_next(request.form.get("back")) or url_for("inventory.notes"))


@bp.post("/notes/<int:note_id>/delete")
def note_delete(note_id):
    if not current_user.is_admin:
        abort(403)
    if not repo.get_note(note_id):
        abort(404)
    with transaction() as db:
        db.execute("DELETE FROM item_note WHERE id = ?", (note_id,))
    flash("備考を削除しました。", "success")
    return redirect(safe_next(request.form.get("back")) or url_for("inventory.notes"))
