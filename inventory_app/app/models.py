from flask_login import UserMixin
from werkzeug.security import check_password_hash

KINDS = {
    "receive": "入荷",
    "print_use": "プリント使用",
    "print_output": "プリント完成",
    "ship": "出荷",
    "adjust": "棚卸し調整",
}


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.password_hash = row["password_hash"]
        self.role = row["role"]
        self._active = bool(row["is_active"])

    @property
    def is_active(self):  # Flask-Login: 無効ユーザーはログイン不可
        return self._active

    @property
    def is_admin(self):
        return self.role == "admin"

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)
