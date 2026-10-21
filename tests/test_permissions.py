"""Who is allowed to do what."""


def test_demo_accounts_exist_with_expected_roles(t):
    roles = {r["username"]: r["role"] for r in t.q("SELECT username, role FROM users")}
    assert roles == {"admin": "Administrator", "manager": "Manager", "analyst": "Analyst"}


def test_wrong_password_is_rejected(t):
    assert "Incorrect username or password" in t.login("analyst", "not-the-password")


def test_pages_need_a_login(t):
    assert t.status("/") == 302
    assert t.status("/archive") == 302
    assert t.status("/notifications") == 302


def test_contributor_cannot_create_projects(t):
    t.login("analyst")
    assert t.status("/cases/new") == 403
    assert "New Project" not in t.page("/")


def test_manager_and_admin_can_create_projects(t):
    for who in ("manager", "admin"):
        t.login(who)
        assert t.status("/cases/new") == 200


def test_admin_can_grant_create_permission_to_a_contributor(t):
    t.login("admin")
    t.post(f"/admin/users/{t.uid('analyst')}/permissions", perm_create_projects="on", also_role="None")
    t.login("analyst")
    assert t.status("/cases/new") == 200


def test_turning_the_permission_off_takes_it_away(t):
    t.login("admin")
    uid = t.uid("analyst")
    t.post(f"/admin/users/{uid}/permissions", perm_create_projects="on")
    t.post(f"/admin/users/{uid}/permissions", perm_create_projects="off")
    t.login("analyst")
    assert t.status("/cases/new") == 403


def test_manager_list_only_shows_managers(t):
    t.add_user("helper", "Analyst", name="Helen Helper")
    t.login("admin")
    page = t.page("/cases/new")
    manager_select = page.split('name="manager_id"')[1].split("</select>")[0]
    assert "Morgan Manager" in manager_select
    assert "Helen Helper" not in manager_select
    assert "Alex Analyst" not in manager_select


def test_admin_with_manager_role_joins_the_manager_list(t):
    t.login("admin")
    t.post(f"/admin/users/{t.uid('admin')}/permissions", also_role="Manager")
    manager_select = t.page("/cases/new").split('name="manager_id"')[1].split("</select>")[0]
    assert "Avery Admin" in manager_select


def test_admin_can_be_only_an_administrator(t):
    t.login("admin")
    t.post(f"/admin/users/{t.uid('admin')}/permissions", also_role="None")
    assert t.one("SELECT also_role FROM users WHERE username = 'admin'") == "None"
    manager_select = t.page("/cases/new").split('name="manager_id"')[1].split("</select>")[0]
    assert "Avery Admin" not in manager_select


def test_only_admins_reach_admin_screens(t):
    for who in ("analyst", "manager"):
        t.login(who)
        assert t.status("/admin/users") == 403
        assert t.status("/admin/workflows") == 403
        assert t.post_status("/admin/users/new", name="X Y", username="xyz", role="Analyst",
                             password="longenough") == 403


def test_everyone_sees_all_projects(t):
    t.add_user("other", "Analyst")
    mine = t.project("Mine", owner="analyst")
    theirs = t.project("Theirs", owner="other")
    t.login("analyst")
    assert t.status(f"/cases/{mine}") == 200
    assert t.status(f"/cases/{theirs}") == 200
    board = t.page("/")
    assert "Mine" in board and "Theirs" in board


def test_my_projects_is_the_personal_filter(t):
    t.add_user("other", "Analyst")
    t.project("Mine", owner="analyst")
    t.project("Theirs", owner="other")
    t.login("analyst")
    page = t.page("/?mine=1")
    assert "Mine" in page and "Theirs" not in page


def test_other_peoples_projects_are_read_only(t):
    t.add_user("other", "Analyst")
    case = t.project("Theirs", owner="other")
    sid = t.stage_ids(case)[0]
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{sid}/status", status="Complete")
    assert t.one("SELECT status FROM case_stages WHERE id = ?", sid) == "Not started"
    assert t.post_status(f"/cases/{case}/comments", body="Hello") == 403
    assert t.post_status(f"/cases/{case}/tags", tag="Rush") == 403
    assert t.post_status(f"/cases/{case}/override-status", stage_id=str(sid), status="Complete", reason="x") == 403


def test_other_managers_cannot_override_a_project_they_do_not_manage(t):
    t.add_user("manager2", "Manager")
    case = t.project("Theirs", manager="manager")
    sid = t.stage_ids(case)[0]
    t.login("manager2")
    assert t.status(f"/cases/{case}") == 200
    assert t.post_status(f"/cases/{case}/override-status", stage_id=str(sid), status="Complete", reason="x") == 403


def test_admin_sees_every_project(t):
    t.add_user("other", "Analyst")
    a = t.project("One", owner="analyst")
    b = t.project("Two", owner="other")
    t.login("admin")
    assert t.status(f"/cases/{a}") == 200 and t.status(f"/cases/{b}") == 200
