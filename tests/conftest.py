import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """Import the app once against a throwaway database, demo checkout, no mail thread."""
    d = tmp_path_factory.mktemp("shop")
    os.environ.update(ADMIN_PASSWORD="test-password-123", DB_PATH=str(d / "shop.db"), UPLOAD_DIR=str(d / "up"),
                      STRIPE_SECRET_KEY="", MAIL_WORKER="0", DOMAIN="http://localhost:4242", DEMO_MODE="")
    os.chdir(ROOT)
    import server as srv
    return importlib.reload(srv) if hasattr(srv, "_reloaded") else srv


@pytest.fixture()
def client(server):
    return server.app.test_client()


@pytest.fixture()
def admin(server):
    c = server.app.test_client()
    assert c.post("/api/admin/login", json={"password": "test-password-123"}).status_code == 200
    return c


@pytest.fixture()
def buy(server):
    """Create a paid test order: buy(items, customer_index, service) -> order id."""
    import secrets

    def _buy(items, customer=0, service=None, lang="en"):
        oid = "t_" + secrets.token_hex(4)
        with server.db() as c:
            its, sub = server.validate_cart(c, [{"id": i, "qty": q} for i, q in items])
            server.paid_test_order(c, its, sub, oid, customer=server.SAMPLE_CUSTOMERS[customer], service=service, lang=lang)
        return oid
    return _buy
