"""Forms without the secret token are refused."""
import re

from conftest import csrf_token_in


def test_login_form_carries_a_token(csrf_t):
    assert csrf_token_in(csrf_t.page("/login"))


def test_login_without_a_token_is_refused(csrf_t):
    csrf_t.client.post("/login", data={"username": "admin", "password": "demo1234"})
    assert csrf_t.status("/") == 302  # still signed out


def test_login_with_the_token_works(csrf_t):
    token = csrf_token_in(csrf_t.page("/login"))
    csrf_t.client.post("/login", data={"username": "admin", "password": "demo1234", "csrf_token": token})
    assert csrf_t.status("/") == 200


def sign_in(csrf_t, username="admin"):
    token = csrf_token_in(csrf_t.page("/login"))
    csrf_t.client.post("/login", data={"username": username, "password": "demo1234", "csrf_token": token})
    return csrf_token_in(csrf_t.page("/account/password"))


def test_changes_without_a_token_are_refused(csrf_t):
    sign_in(csrf_t)
    status = csrf_t.post_status("/admin/users/new", name="Evil Eve", username="eve",
                                role="Administrator", password="longenough1")
    assert status == 302
    assert csrf_t.one("SELECT COUNT(*) FROM users WHERE username = 'eve'") == 0


def test_a_wrong_token_is_refused(csrf_t):
    sign_in(csrf_t)
    csrf_t.post_status("/admin/users/new", name="Evil Eve", username="eve", role="Analyst",
                       password="longenough1", csrf_token="not-the-token")
    assert csrf_t.one("SELECT COUNT(*) FROM users WHERE username = 'eve'") == 0


def test_a_valid_token_is_accepted(csrf_t):
    token = sign_in(csrf_t)
    csrf_t.post_status("/admin/users/new", name="Good Gus", username="gus", role="Analyst",
                       password="longenough1", csrf_token=token)
    assert csrf_t.one("SELECT COUNT(*) FROM users WHERE username = 'gus'") == 1


def test_a_refused_form_shows_a_friendly_message(csrf_t):
    sign_in(csrf_t)
    page = csrf_t.post("/admin/settings/demo-mode", demo_mode="off")
    assert "session expired or the form was out of date" in page
    assert "demo1234" in csrf_t.page("/login")  # the setting did not change


def test_every_post_form_in_the_app_has_a_token(csrf_t):
    # Make a project first (with the check briefly off), so its page can be inspected too.
    csrf_t.app.config["CSRF_ENABLED"] = False
    case = csrf_t.project()
    csrf_t.app.config["CSRF_ENABLED"] = True
    csrf_t.logout()
    sign_in(csrf_t)
    for url in ("/", "/cases/new", "/admin/users", "/admin/workflows", f"/cases/{case}",
                "/notifications", "/notifications/settings", "/account/password"):
        html = csrf_t.page(url)
        forms = len(re.findall(r'<form\b[^>]*method="post"', html))
        tokens = len(re.findall(r'name="csrf_token"', html))
        assert forms == tokens, url


def test_tokens_are_not_shared_between_sessions(csrf_t):
    mine = sign_in(csrf_t)
    other = csrf_t.app.test_client()
    theirs = csrf_token_in(other.get("/login").get_data(as_text=True))
    assert mine and theirs and mine != theirs
    other.post("/login", data={"username": "admin", "password": "demo1234", "csrf_token": mine})
    assert other.get("/").status_code == 302  # another session's token does not work
