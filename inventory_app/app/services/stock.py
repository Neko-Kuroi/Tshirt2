"""在庫操作ロジック。画面側は quantity を直接触らず、必ずここの関数を使う。

呼び出し側が `with transaction():` で包むこと(1リクエスト=1トランザクション)。
失敗時は StockError を投げ、transaction() が全体をロールバックする。
target は repo.get_variant / get_product / get_target が返す行(kind と id を持つ)。
"""
import uuid

from .. import repo
from ..db import get_db

_TABLE = {"variant": "variant", "product": "printed_product"}


class StockError(Exception):
    """ユーザーに見せてよいエラー。"""


def _norm(s) -> str:
    return (s or "").strip()


def _apply(kind: str, row_id: int, delta: int):
    """在庫を原子的に増減する。マイナスになる場合は更新せず StockError。"""
    cur = get_db().execute(
        f"UPDATE {_TABLE[kind]} SET quantity = quantity + ?, updated_at = datetime('now') "
        "WHERE id = ? AND quantity + ? >= 0", (delta, row_id, delta))
    if cur.rowcount != 1:
        raise StockError("在庫が足りません。")


def _log(batch_id, kind, user, delta, note, target):
    is_variant = target["kind"] == "variant"
    get_db().execute(
        "INSERT INTO stock_movement (batch_id, kind, variant_id, printed_product_id, delta, note, "
        "user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (batch_id, kind, target["id"] if is_variant else None,
         None if is_variant else target["id"], delta, _norm(note), user.id))


def _positive(n, label="数量") -> int:
    if not isinstance(n, int) or n <= 0:
        raise StockError(f"{label}は1以上の整数で入力してください。")
    return n


def get_or_create_variant(item, color="", size="", variant_name=""):
    """item は repo.get_item の行。カテゴリー設定に合わない列は空文字に落として登録する。"""
    if not item["is_active"]:
        raise StockError("この品番は使用停止中のため、入荷できません。")
    color = _norm(color) if item["uses_color"] else ""
    size = _norm(size) if item["uses_size"] else ""
    variant_name = _norm(variant_name) if item["uses_variant_name"] else ""
    if item["uses_color"] and not color:
        raise StockError("色を入力してください。")
    if item["uses_size"] and not size:
        raise StockError("サイズを入力してください。")
    if item["uses_variant_name"] and not variant_name:
        raise StockError("種類名を入力してください。")
    v = repo.find_variant(item["id"], color, size, variant_name)
    if v:
        return v
    cur = get_db().execute(
        "INSERT INTO variant (item_id, color, size, variant_name, quantity, updated_at) "
        "VALUES (?, ?, ?, ?, 0, datetime('now'))",
        (item["id"], color, size, variant_name))
    return repo.get_variant(cur.lastrowid)


def get_or_create_design(name: str) -> int:
    name = _norm(name)
    if not name:
        raise StockError("デザイン名を入力してください。")
    db = get_db()
    row = db.execute("SELECT id FROM design WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else db.execute("INSERT INTO design (name, note) VALUES (?, '')", (name,)).lastrowid


def get_or_create_product(design_id: int, variant_id: int):
    db = get_db()
    row = db.execute("SELECT id FROM printed_product WHERE design_id = ? AND variant_id = ?",
                     (design_id, variant_id)).fetchone()
    pid = row["id"] if row else db.execute(
        "INSERT INTO printed_product (design_id, variant_id, quantity, updated_at) "
        "VALUES (?, ?, 0, datetime('now'))",
        (design_id, variant_id)).lastrowid
    return repo.get_product(pid)


# ---- 操作 -------------------------------------------------------------

def receive(user, variant, qty: int, note="") -> str:
    qty = _positive(qty)
    batch = str(uuid.uuid4())
    _apply("variant", variant["id"], qty)
    _log(batch, "receive", user, qty, note, variant)
    return batch


def ship(user, target, qty: int, note="") -> str:
    qty = _positive(qty)
    batch = str(uuid.uuid4())
    _apply(target["kind"], target["id"], -qty)
    _log(batch, "ship", user, -qty, note, target)
    return batch


def adjust(user, target, delta: int, note: str) -> str:
    """棚卸し調整。増減どちらも可。理由(note)は必須。"""
    if not isinstance(delta, int) or delta == 0:
        raise StockError("増減数は0以外の整数で入力してください。")
    if not _norm(note):
        raise StockError("棚卸し調整には理由を入力してください。")
    batch = str(uuid.uuid4())
    _apply(target["kind"], target["id"], delta)
    _log(batch, "adjust", user, delta, note, target)
    return batch


def convert_to_printed(user, variant, design_name: str, used: int, output: int, note="") -> str:
    """無地在庫 → プリント済み製品。used 枚消費して output 枚完成(ミスプリントで差が出てよい)。"""
    used = _positive(used, "使用数")
    if not isinstance(output, int) or output < 0:
        raise StockError("完成数は0以上の整数で入力してください。")
    if output > used:
        raise StockError("完成数が使用数を超えています。")
    if not variant["is_printable"]:
        raise StockError("このカテゴリーはプリント対象ではありません。")
    if not variant["item_active"]:
        raise StockError("この品番は使用停止中のため、プリント変換できません。")

    design_id = get_or_create_design(design_name)
    batch = str(uuid.uuid4())

    _apply("variant", variant["id"], -used)
    _log(batch, "print_use", user, -used, note, variant)
    if output > 0:
        product = get_or_create_product(design_id, variant["id"])
        _apply("product", product["id"], output)
        _log(batch, "print_output", user, output, note, product)
    return batch
