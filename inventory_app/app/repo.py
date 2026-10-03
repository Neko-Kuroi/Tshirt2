"""読み取り用クエリ集。テンプレートで使う表示用の項目(label, spec など)もここで付ける。"""
from .db import get_db
from .models import KINDS, User


def _all(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def _one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def item_label(r):
    head = " ".join(p for p in (r["brand"], r["item_no"]) if p)
    return f"{head} {r['item_name']}".strip() or f"Item#{r.get('item_id', r.get('id'))}"


def spec_of(r):
    return " / ".join(p for p in (r["variant_name"], r["color"], r["size"]) if p) or "-"


# ---- ユーザー --------------------------------------------------------

def get_user(user_id):
    row = _one('SELECT * FROM "user" WHERE id = ?', (user_id,))
    return User(row) if row else None


def get_user_by_name(username):
    row = _one('SELECT * FROM "user" WHERE username = ?', (username,))
    return User(row) if row else None


def users():
    return [User(r) for r in _all('SELECT * FROM "user" ORDER BY id')]


# ---- カテゴリー ------------------------------------------------------

def categories():
    return _all("SELECT * FROM category ORDER BY sort_order, id")


def get_category(cid):
    return _one("SELECT * FROM category WHERE id = ?", (cid,))


# ---- 品番 ------------------------------------------------------------

_ITEM = """
SELECT i.id, i.category_id, i.brand, i.item_no, i.name AS item_name, i.note, i.is_active,
       c.name AS category_name, c.uses_color, c.uses_size, c.uses_variant_name, c.is_printable
FROM item i JOIN category c ON c.id = i.category_id
"""


def _item(r):
    if r is not None:
        r["name"] = r["item_name"]
        r["label"] = item_label(r)
    return r


def get_item(item_id):
    return _item(_one(_ITEM + " WHERE i.id = ?", (item_id,)))


def items_for_select():
    return [_item(r) for r in _all(
        _ITEM + " WHERE i.is_active = 1 ORDER BY c.sort_order, i.brand, i.item_no")]


def items_in_category(category_id, q=""):
    sql, args = _ITEM + " WHERE i.is_active = 1 AND i.category_id = ?", [category_id]
    if q:
        like = f"%{q}%"
        sql += " AND (i.brand LIKE ? OR i.item_no LIKE ? OR i.name LIKE ?)"
        args += [like, like, like]
    return [_item(r) for r in _all(sql + " ORDER BY i.brand, i.item_no", args)]


def items_admin(category_id=None):
    sql, args = _ITEM, []
    if category_id:
        sql += " WHERE i.category_id = ?"
        args.append(category_id)
    return [_item(r) for r in _all(sql + " ORDER BY c.sort_order, i.brand, i.item_no", args)]


# ---- 無地在庫(Variant) -----------------------------------------------

_VARIANT = """
SELECT v.id, v.item_id, v.color, v.size, v.variant_name, v.quantity,
       i.brand, i.item_no, i.name AS item_name, i.is_active AS item_active,
       i.category_id, c.name AS category_name, c.is_printable
FROM variant v
JOIN item i ON i.id = v.item_id
JOIN category c ON c.id = i.category_id
"""


def _variant(r):
    if r is not None:
        r["kind"] = "variant"
        r["item_label"] = item_label(r)
        r["spec"] = spec_of(r)
        r["label"] = f"{r['item_label']} | {r['spec']}"
    return r


def get_variant(variant_id):
    return _variant(_one(_VARIANT + " WHERE v.id = ?", (variant_id,)))


def find_variant(item_id, color, size, variant_name):
    return _variant(_one(
        _VARIANT + " WHERE v.item_id=? AND v.color=? AND v.size=? AND v.variant_name=?",
        (item_id, color, size, variant_name)))


def variants_in_category(category_id):
    return [_variant(r) for r in _all(
        _VARIANT + " WHERE i.category_id = ? ORDER BY v.variant_name, v.color", (category_id,))]


def _variant_order():
    return " ORDER BY c.sort_order, i.brand, i.item_no, v.color, v.size"


def printable_variants_in_stock():
    return [_variant(r) for r in _all(
        _VARIANT + " WHERE c.is_printable = 1 AND v.quantity > 0 AND i.is_active = 1"
        + _variant_order())]


def all_variants():
    return [_variant(r) for r in _all(_VARIANT + _variant_order())]


def used_colors():
    return [r["color"] for r in _all(
        "SELECT DISTINCT color FROM variant WHERE color <> '' ORDER BY color")]


# ---- プリント済み在庫 ------------------------------------------------

_PRODUCT = """
SELECT p.id, p.design_id, p.variant_id, p.quantity, d.name AS design_name,
       v.color, v.size, v.variant_name,
       i.brand, i.item_no, i.name AS item_name, c.name AS category_name
FROM printed_product p
JOIN design d ON d.id = p.design_id
JOIN variant v ON v.id = p.variant_id
JOIN item i ON i.id = v.item_id
JOIN category c ON c.id = i.category_id
"""


def _product(r):
    if r is not None:
        r["kind"] = "product"
        r["item_label"] = item_label(r)
        r["spec"] = spec_of(r)
        r["label"] = f"{r['design_name']} | {r['item_label']} | {r['spec']}"
    return r


def get_product(product_id):
    return _product(_one(_PRODUCT + " WHERE p.id = ?", (product_id,)))


def products(q=""):
    sql, args = _PRODUCT, []
    if q:
        like = f"%{q}%"
        sql += (" WHERE d.name LIKE ? OR i.brand LIKE ? OR i.item_no LIKE ?"
                " OR i.name LIKE ? OR v.color LIKE ?")
        args = [like] * 5
    return [_product(r) for r in _all(sql + " ORDER BY d.name, i.brand, i.item_no", args)]


def designs():
    return _all("SELECT * FROM design ORDER BY name")


def get_target(raw):
    """フォームの 'v:12' / 'p:34' から在庫の行を引く。"""
    kind, _, rid = (raw or "").partition(":")
    try:
        rid = int(rid)
    except ValueError:
        return None
    if kind == "v":
        return get_variant(rid)
    if kind == "p":
        return get_product(rid)
    return None


# ---- ダッシュボード --------------------------------------------------

def blank_totals():
    return {r["category_id"]: r["total"] for r in _all(
        "SELECT i.category_id, COALESCE(SUM(v.quantity), 0) AS total "
        "FROM variant v JOIN item i ON i.id = v.item_id GROUP BY i.category_id")}


def printed_totals():
    return {r["category_id"]: r["total"] for r in _all(
        "SELECT i.category_id, COALESCE(SUM(p.quantity), 0) AS total "
        "FROM printed_product p JOIN variant v ON v.id = p.variant_id "
        "JOIN item i ON i.id = v.item_id GROUP BY i.category_id")}


# ---- 履歴 ------------------------------------------------------------

_MOVEMENT = """
SELECT m.id, m.batch_id, m.kind, m.delta, m.note, m.created_at, u.username,
       d.name AS design_name,
       i.id AS item_id, i.brand, i.item_no, i.name AS item_name,
       COALESCE(v.color, pv.color) AS color,
       COALESCE(v.size, pv.size) AS size,
       COALESCE(v.variant_name, pv.variant_name) AS variant_name
FROM stock_movement m
JOIN "user" u ON u.id = m.user_id
LEFT JOIN variant v ON v.id = m.variant_id
LEFT JOIN printed_product p ON p.id = m.printed_product_id
LEFT JOIN design d ON d.id = p.design_id
LEFT JOIN variant pv ON pv.id = p.variant_id
JOIN item i ON i.id = COALESCE(v.item_id, pv.item_id)
"""


def _movement(r):
    base = f"{item_label(r)} | {spec_of(r)}"
    r["kind_label"] = KINDS.get(r["kind"], r["kind"])
    r["target_label"] = f"{r['design_name']} | {base}" if r["design_name"] else base
    return r


def recent_movements(limit=8):
    return [_movement(r) for r in _all(
        _MOVEMENT + " ORDER BY m.created_at DESC, m.id DESC LIMIT ?", (limit,))]


class Page:
    def __init__(self, items, page, has_next):
        self.items, self.page, self.has_next = items, page, has_next
        self.has_prev = page > 1
        self.prev_num, self.next_num = page - 1, page + 1


def movements_page(kind, page, per_page):
    sql, args = _MOVEMENT, []
    if kind in KINDS:
        sql += " WHERE m.kind = ?"
        args.append(kind)
    sql += " ORDER BY m.created_at DESC, m.id DESC LIMIT ? OFFSET ?"
    rows = _all(sql, args + [per_page + 1, (page - 1) * per_page])
    return Page([_movement(r) for r in rows[:per_page]], page, len(rows) > per_page)
