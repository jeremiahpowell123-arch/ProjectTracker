"""Restricted projects: only chosen people see the details."""


def restrict(t, case, viewers=(), by="manager", on=True):
    t.login(by)
    data = {"restricted": "on" if on else "off"}
    form = {**data}
    t.client.post(f"/cases/{case}/visibility", data={**form, "viewer": [str(t.uid(v)) for v in viewers]})


def setup_restricted(t, viewers=()):
    t.add_user("outsider", "Analyst", name="Olive Outsider")
    t.add_user("friend", "Analyst", name="Frank Friend")
    case = t.project("Secret Deal")
    restrict(t, case, viewers=[v for v in viewers])
    return case


def test_unrestricted_by_default(t):
    case = t.project("Open Deal")
    assert t.one("SELECT restricted FROM cases WHERE id = ?", case) == 0


def test_restricting_hides_details_from_everyone_else(t):
    case = setup_restricted(t)
    t.login("outsider")
    assert t.status(f"/cases/{case}") == 404
    board = t.page("/")
    assert "Secret Deal" not in board
    assert "Restricted project" in board


def test_hidden_projects_show_no_name_people_or_tags(t):
    case = t.project("Secret Deal")
    t.login("manager")
    t.post(f"/cases/{case}/tags", tag="Sensitive")
    t.add_user("outsider", "Analyst")
    restrict(t, case)
    t.login("outsider")
    for url in ("/", "/?view=list"):
        page = t.page(url)
        assert "Secret Deal" not in page
        assert "Sensitive" not in page
        assert "Alex Analyst" not in page and "Morgan Manager" not in page


def test_hidden_projects_cannot_be_searched_by_name(t):
    setup_restricted(t)
    t.login("outsider")
    assert "Restricted project" not in t.page("/?q=secret")


def test_hidden_tags_do_not_appear_in_the_tag_filter(t):
    case = t.project("Secret Deal")
    t.login("manager")
    t.post(f"/cases/{case}/tags", tag="Sensitive")
    t.add_user("outsider", "Analyst")
    restrict(t, case)
    t.login("outsider")
    assert "Sensitive" not in t.page("/")
    t.login("admin")
    assert "Sensitive" in t.page("/")


def test_owner_manager_and_admin_always_see_a_restricted_project(t):
    case = setup_restricted(t)
    for who in ("analyst", "manager", "admin"):
        t.login(who)
        assert t.status(f"/cases/{case}") == 200
        assert "Secret Deal" in t.page("/")


def test_chosen_people_can_see_details_read_only(t):
    case = setup_restricted(t, viewers=["friend"])
    t.login("friend")
    assert t.status(f"/cases/{case}") == 200
    assert "Secret Deal" in t.page("/")
    sid = t.stage_ids(case)[0]
    t.post(f"/cases/{case}/stages/{sid}/status", status="Complete")
    assert t.one("SELECT status FROM case_stages WHERE id = ?", sid) == "Not started"
    t.login("outsider")
    assert t.status(f"/cases/{case}") == 404


def test_chosen_people_are_notified(t):
    setup_restricted(t, viewers=["friend"])
    assert any("gave you access to Secret Deal" in m for m in t.notes("friend"))


def test_removing_the_restriction_shows_the_project_to_everyone(t):
    case = setup_restricted(t)
    restrict(t, case, on=False)
    t.login("outsider")
    assert t.status(f"/cases/{case}") == 200


def test_unchecking_a_person_removes_their_access(t):
    case = setup_restricted(t, viewers=["friend"])
    restrict(t, case, viewers=[])
    t.login("friend")
    assert t.status(f"/cases/{case}") == 404


def test_only_the_projects_manager_or_an_admin_can_restrict(t):
    t.add_user("manager2", "Manager")
    case = t.project("Open Deal")
    for who in ("analyst", "manager2"):
        t.login(who)
        assert t.post_status(f"/cases/{case}/visibility", restricted="on") == 403
    assert t.one("SELECT restricted FROM cases WHERE id = ?", case) == 0
    restrict(t, case, by="admin")
    assert t.one("SELECT restricted FROM cases WHERE id = ?", case) == 1


def test_owner_and_admins_are_not_valid_choices(t):
    case = t.project("Open Deal")
    t.login("manager")
    t.client.post(f"/cases/{case}/visibility", data={
        "restricted": "on", "viewer": [str(t.uid("analyst")), str(t.uid("admin")), "999", "abc"]})
    assert t.one("SELECT COUNT(*) FROM case_viewers") == 0


def test_visibility_changes_are_logged(t):
    case = t.project("Open Deal")
    t.add_user("friend", "Analyst", name="Frank Friend")
    restrict(t, case, viewers=["friend"])
    row = t.q("SELECT detail FROM history WHERE action = 'visibility'")[0]
    assert "Frank Friend" in row["detail"]


def test_restricted_archived_projects_are_hidden_in_the_archive(t):
    case = setup_restricted(t)
    t.complete_all(case)
    t.login("outsider")
    page = t.page("/archive")
    assert "Secret Deal" not in page and "Restricted project" in page
    t.login("manager")
    assert "Secret Deal" in t.page("/archive")


def test_notifications_still_work_for_restricted_projects(t):
    case = setup_restricted(t)
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{t.stage_ids(case)[0]}/status", status="In progress")
    assert any("Secret Deal" in m for m in t.notes("manager"))


def test_hidden_projects_still_land_in_the_right_board_column(t):
    case = setup_restricted(t)
    t.login("outsider")
    board = t.page("/")
    assert "Other stages" not in board
