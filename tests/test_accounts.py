"""Adding people, passwords, and demo mode."""
from werkzeug.security import check_password_hash


def add(t, **overrides):
    data = dict(name="Taylor Quinn", username="tquinn", role="Analyst",
                initials="", password="temp-pass-1")
    data.update(overrides)
    return t.post("/admin/users/new", **data)


def test_admin_adds_a_person_who_can_sign_in(t):
    t.login("admin")
    assert "Taylor Quinn was added as Contributor" in add(t)
    assert "All Projects" in t.login("tquinn", "temp-pass-1")


def test_initials_are_generated_unless_given(t):
    t.login("admin")
    add(t)
    add(t, name="Pat Lee", username="plee", initials="pl")
    assert t.one("SELECT initials FROM users WHERE username = 'tquinn'") == "TQ"
    assert t.one("SELECT initials FROM users WHERE username = 'plee'") == "PL"


def test_passwords_are_stored_as_hashes(t):
    t.login("admin")
    add(t)
    stored = t.one("SELECT password_hash FROM users WHERE username = 'tquinn'")
    assert stored != "temp-pass-1"
    assert check_password_hash(stored, "temp-pass-1")


def test_bad_new_user_details_are_refused(t):
    t.login("admin")
    for bad in (
        dict(username="ab"), dict(username="has space"), dict(username="admin"),
        dict(role="Root"), dict(password="short"), dict(initials="1!"), dict(name=""),
    ):
        add(t, **bad)
    assert t.one("SELECT COUNT(*) FROM users") == 3


def test_usernames_are_unique_ignoring_case(t):
    t.login("admin")
    add(t)
    add(t, username="TQUINN", name="Someone Else")
    assert t.one("SELECT COUNT(*) FROM users WHERE username = 'tquinn'") == 1


def test_new_people_can_be_given_projects(t):
    t.login("admin")
    add(t)
    case = t.project("Fresh Co", owner="tquinn")
    assert case is not None


def test_a_person_can_change_their_own_password(t):
    t.login("analyst")
    t.post("/account/password", current="demo1234", new="brand-new-pass", again="brand-new-pass")
    assert "Incorrect username" in t.login("analyst", "demo1234")
    assert "All Projects" in t.login("analyst", "brand-new-pass")


def test_password_change_checks_current_password_and_confirmation(t):
    t.login("analyst")
    t.post("/account/password", current="wrong", new="brand-new-pass", again="brand-new-pass")
    t.post("/account/password", current="demo1234", new="brand-new-pass", again="different-pass")
    t.post("/account/password", current="demo1234", new="short", again="short")
    assert "All Projects" in t.login("analyst", "demo1234")


def test_admin_can_reset_a_password(t):
    t.login("admin")
    t.post("/admin/users/reset-password", person=str(t.uid("analyst")), password="reset-pass-9")
    assert "All Projects" in t.login("analyst", "reset-pass-9")


def test_contributors_cannot_reset_passwords(t):
    t.login("analyst")
    assert t.post_status("/admin/users/reset-password", person=str(t.uid("admin")),
                         password="hacked-pass-1") == 403
    assert "All Projects" in t.login("admin")


def test_demo_details_show_on_the_sign_in_page_by_default(t):
    page = t.page("/login")
    assert "demo1234" in page and "Demo accounts" in page


def test_admin_can_turn_demo_mode_off_and_on(t):
    t.login("admin")
    t.post("/admin/settings/demo-mode", demo_mode="off")
    t.logout()
    page = t.page("/login")
    assert "demo1234" not in page and "Demo accounts" not in page
    t.login("admin")
    t.post("/admin/settings/demo-mode", demo_mode="on")
    t.logout()
    assert "demo1234" in t.page("/login")


def test_only_admins_can_change_demo_mode(t):
    t.login("analyst")
    assert t.post_status("/admin/settings/demo-mode", demo_mode="off") == 403
    assert "demo1234" in t.page("/login")


def test_users_page_warns_about_accounts_using_the_demo_password(t):
    t.login("admin")
    assert "still use the demo password" in t.page("/admin/users")
    for username in ("admin", "manager", "analyst"):
        t.post("/admin/users/reset-password", person=str(t.uid(username)), password="a-long-new-pass")
    assert "still use the demo password" not in t.page("/admin/users")
