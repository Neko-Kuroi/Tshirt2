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
    try:
        return int((raw or "").strip())
    except (ValueError, AttributeError):
        return default
