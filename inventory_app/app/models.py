import hmac

from flask import current_app
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

    def session_token(self) -> str:
        """パスワードを変えると変わる値(ハッシュそのものは Cookie に出さず、HMAC にする)。"""
        key = current_app.config["SECRET_KEY"]
        key = key.encode() if isinstance(key, str) else key
        return hmac.new(key, self.password_hash.encode(), "sha256").hexdigest()[:20]

    def get_id(self):  # Flask-Login がセッション/remember Cookie に保存する値
        return f"{self.id}:{self.session_token()}"
