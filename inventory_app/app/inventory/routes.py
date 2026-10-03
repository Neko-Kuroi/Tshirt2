from collections import OrderedDict

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import repo
from ..db import transaction
from ..models import KINDS
from ..services import stock
from ..services.stock import StockError
from ..utils import size_key, to_int
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
        printed=repo.printed_totals(), recent=repo.recent_movements(8))


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
        items = [build_matrix(it, cat, by_item.get(it["id"], []))
                 for it in repo.items_in_category(cat["id"], q)]
    return render_template("inventory/blank_list.html", cats=cats, cat=cat, items=items, q=q)


def build_matrix(item, cat, variants):
    """行=色/種類名、列=サイズ(サイズ無しカテゴリーは1列)。"""
    sizes = sorted({v["size"] for v in variants}, key=size_key) if cat["uses_size"] else [""]
    rows = OrderedDict()
    for v in variants:  # repo 側で variant_name, color 順に並んでいる
        key = (v["variant_name"], v["color"])
        row = rows.setdefault(key, {
            "label": " / ".join(p for p in key if p) or "-", "cells": {}, "total": 0})
        row["cells"][v["size"] if cat["uses_size"] else ""] = v
        row["total"] += v["quantity"]
    return {"item": item, "sizes": sizes, "rows": list(rows.values()),
            "total": sum(r["total"] for r in rows.values())}


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
    return render_template("inventory/receive.html", items=repo.items_for_select(),
                           selected=selected, colors=repo.used_colors(), form=request.form)


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
