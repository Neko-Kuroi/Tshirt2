import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.db import get_db, transaction
from app.schema import migrate
from config import TestConfig


@pytest.fixture
def app(tmp_path):
    class Cfg(TestConfig):
        DATABASE = str(tmp_path / "test.db")

    app = create_app(Cfg)
    with app.app_context():
        migrate(get_db())
        pw = generate_password_hash("password123")
        with transaction() as db:
            db.execute(
                "INSERT INTO category (name, sort_order, uses_color, uses_size, uses_variant_name, is_printable) "
                "VALUES ('Tシャツ', 0, 1, 1, 0, 1), ('バッジ', 1, 0, 0, 1, 0)")
            db.execute(
                "INSERT INTO item (category_id, brand, item_no, name) VALUES "
                "(1, 'Printstar', '085-CVT', 'Tシャツ'), (1, 'United Athle', '5001', 'Tシャツ'), "
                "(2, '', '44mm', '缶バッジ')")
            db.execute('INSERT INTO "user" (username, password_hash, role) VALUES '
                       "('admin', ?, 'admin'), ('staff', ?, 'staff')", (pw, pw))
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    def _login(username="admin"):
        r = client.post("/login", data={"username": username, "password": "password123"})
        assert r.status_code == 302
        return client
    return _login
