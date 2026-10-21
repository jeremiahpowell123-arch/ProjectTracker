"""Shared test setup.

Every test gets a brand-new SQLite database in a temporary folder, so tests never
touch your real tracker.db and never depend on each other.
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# app.py sets up its database when it is imported, so point it at a throwaway file first.
os.environ["TRACKER_DB"] = os.path.join(tempfile.mkdtemp(), "import.db")

import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402
import db  # noqa: E402

DEMO_PASSWORD = "demo1234"


class Tracker:
    """A small helper that drives the app the way a person would."""

    def __init__(self, flask_app):
        self.app = flask_app
        self.client = flask_app.test_client()

    # -- talking to the app ------------------------------------------------
    def login(self, username, password=DEMO_PASSWORD):
        self.client.get("/logout")
        return self.client.post(
            "/login", data={"username": username, "password": password},
            follow_redirects=True,
        ).get_data(as_text=True)

    def logout(self):
        self.client.get("/logout")

    def page(self, url):
        return self.client.get(url).get_data(as_text=True)

    def status(self, url):
        return self.client.get(url).status_code

    def post(self, url, **data):
        """POST a form and follow the redirect. Returns the page text."""
        return self.client.post(url, data=data, follow_redirects=True).get_data(as_text=True)

    def post_status(self, url, **data):
        """POST a form without following redirects. Returns the status code."""
        return self.client.post(url, data=data).status_code

    # -- looking at the database -------------------------------------------
    def q(self, sql, *args):
        conn = db.get_db()
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return rows

    def one(self, sql, *args):
        rows = self.q(sql, *args)
        return rows[0][0] if rows else None

    def uid(self, username):
        return self.one("SELECT id FROM users WHERE username = ?", username)

    def type_id(self, name="Standard Project"):
        return self.one("SELECT id FROM project_types WHERE name = ?", name)

    # -- setting things up -------------------------------------------------
    def add_user(self, username, role="Analyst", name=None):
        conn = db.get_db()
        conn.execute(
            "INSERT INTO users (name, username, initials, role, password_hash) VALUES (?, ?, ?, ?, ?)",
            (name or username.title(), username, username[:2].upper(), role,
             generate_password_hash(DEMO_PASSWORD)),
        )
        conn.commit()
        conn.close()
        return self.uid(username)

    def project(self, name="Acme Co", owner="analyst", manager="manager",
                due="2030-01-01", risk="Low", type_id=None):
        """Create a project as the admin and return its id."""
        self.login("admin")
        self.post(
            "/cases/new", customer_name=name, risk_rating=risk, due_date=due,
            owner_id=str(self.uid(owner)), manager_id=str(self.uid(manager)),
            type_id=str(type_id or self.type_id()),
        )
        return self.one("SELECT id FROM cases WHERE customer_name = ?", name)

    def stage_ids(self, case_id):
        return [r["id"] for r in self.q(
            "SELECT id FROM case_stages WHERE case_id = ? AND archived = 0 ORDER BY position",
            case_id)]

    def complete_all(self, case_id):
        """The owner completes every stage, which archives the project."""
        owner = self.one("SELECT username FROM users WHERE id = "
                         "(SELECT owner_id FROM cases WHERE id = ?)", case_id)
        self.login(owner)
        for sid in self.stage_ids(case_id):
            self.post(f"/cases/{case_id}/stages/{sid}/status", status="Complete")

    def notes(self, username):
        return [r["message"] for r in self.q(
            "SELECT message FROM notifications WHERE user_id = ? ORDER BY id", self.uid(username))]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    app_module.app.config.update(TESTING=True, CSRF_ENABLED=False)
    return app_module.app


@pytest.fixture
def t(app):
    """The helper most tests use."""
    return Tracker(app)


@pytest.fixture
def csrf_t(app):
    """Same helper, with CSRF protection switched on."""
    app.config["CSRF_ENABLED"] = True
    yield Tracker(app)
    app.config["CSRF_ENABLED"] = False


def csrf_token_in(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else None
