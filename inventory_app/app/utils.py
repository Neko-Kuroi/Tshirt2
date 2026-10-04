from urllib.parse import urlparse

SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "2XL", "XXL", "3XL", "XXXL", "4XL", "5XL",
              "FREE", "F"]


def size_key(s):
    u = s.upper()
    if u in SIZE_ORDER:
        return (0, SIZE_ORDER.index(u), s)
    if s.isdigit():
        return (1, int(s), s)
    return (2, 0, s)


def to_int(raw, default=None):
    """フォーム/クエリの文字列を整数にする。変換できなければ default を返す(例外は出さない)。"""
    try:
        return int((raw or "").strip())
    except (ValueError, AttributeError):
        return default


def safe_next(target):
    """同一サイト内の相対パスだけ許可(オープンリダイレクト対策)。それ以外は None。"""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//") \
            or target.startswith("/\\"):
        return None
    return target


def is_unique_violation(exc) -> bool:
    """sqlite3.IntegrityError が UNIQUE 違反か。他の制約違反を『重複』と誤表示しないために使う。"""
    return "UNIQUE constraint failed" in str(exc)
