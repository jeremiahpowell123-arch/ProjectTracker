"""Working a project: status changes, manager overrides, reassigning, archive, reopen."""


def stage_status(t, stage_id):
    return t.one("SELECT status FROM case_stages WHERE id = ?", stage_id)


def test_owner_can_change_a_stage(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{sid}/status", status="In progress")
    assert stage_status(t, sid) == "In progress"


def test_only_the_owner_changes_stages(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    for who in ("manager", "admin"):
        t.login(who)
        t.post(f"/cases/{case}/stages/{sid}/status", status="Complete")
        assert stage_status(t, sid) == "Not started"


def test_invalid_status_is_ignored(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{sid}/status", status="Banana")
    assert stage_status(t, sid) == "Not started"


def test_status_changes_are_logged(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{sid}/status", status="In progress")
    actions = [r["action"] for r in t.q("SELECT action FROM history WHERE case_id = ?", case)]
    assert actions == ["created", "status_change"]


def test_manager_override_needs_a_reason(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("manager")
    t.post(f"/cases/{case}/override-status", stage_id=str(sid), status="Complete", reason="  ")
    assert stage_status(t, sid) == "Not started"
    t.post(f"/cases/{case}/override-status", stage_id=str(sid), status="Complete", reason="Owner on PTO")
    assert stage_status(t, sid) == "Complete"
    row = t.q("SELECT action, detail FROM history WHERE action = 'override_status'")[0]
    assert "Owner on PTO" in row["detail"]


def test_owner_cannot_use_manager_controls(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("analyst")
    assert t.post_status(f"/cases/{case}/override-status", stage_id=str(sid),
                         status="Complete", reason="x") == 403
    assert t.post_status(f"/cases/{case}/reassign", new_owner_id="1", reason="x") == 403


def test_reassign_changes_the_owner_and_needs_a_reason(t):
    helper = t.add_user("helper", "Analyst")
    case = t.project()
    t.login("manager")
    t.post(f"/cases/{case}/reassign", new_owner_id=str(helper), reason="")
    assert t.one("SELECT owner_id FROM cases WHERE id = ?", case) == t.uid("analyst")
    t.post(f"/cases/{case}/reassign", new_owner_id=str(helper), reason="Balancing workload")
    assert t.one("SELECT owner_id FROM cases WHERE id = ?", case) == helper


def test_owner_and_manager_must_be_different_people(t):
    t.login("admin")
    t.post("/cases/new", customer_name="Same Person", risk_rating="Low", due_date="2030-01-01",
           owner_id=str(t.uid("manager")), manager_id=str(t.uid("manager")),
           type_id=str(t.type_id()))
    assert t.one("SELECT COUNT(*) FROM cases WHERE customer_name = 'Same Person'") == 0


def test_project_form_rejects_missing_fields_and_bad_dates(t):
    t.login("admin")
    base = dict(customer_name="Bad Co", risk_rating="Low", due_date="2030-01-01",
                owner_id=str(t.uid("analyst")), manager_id=str(t.uid("manager")),
                type_id=str(t.type_id()))
    for change in ({"customer_name": ""}, {"due_date": "not-a-date"}, {"risk_rating": "Extreme"}):
        t.post("/cases/new", **{**base, **change})
    assert t.one("SELECT COUNT(*) FROM cases") == 0


def test_completing_the_last_stage_archives_the_project(t):
    case = t.project()
    t.complete_all(case)
    assert t.one("SELECT archived_at FROM cases WHERE id = ?", case) is not None
    assert "Archived" in [r["action"].title() for r in t.q("SELECT action FROM history WHERE case_id = ?", case)]
    assert "Acme Co" not in t.page("/")


def test_archived_projects_appear_in_the_archive(t):
    case = t.project()
    t.complete_all(case)
    page = t.page("/archive")
    assert "Acme Co" in page and ("On time" in page or "late" in page)


def test_archived_projects_are_read_only(t):
    case = t.project()
    t.complete_all(case)
    sid = t.stage_ids(case)[0]
    t.post(f"/cases/{case}/stages/{sid}/status", status="In progress")
    assert stage_status(t, sid) == "Complete"
    t.login("manager")
    t.post(f"/cases/{case}/override-status", stage_id=str(sid), status="In progress", reason="x")
    assert stage_status(t, sid) == "Complete"


def test_manager_can_reopen_with_a_stage_and_reason(t):
    case = t.project()
    t.complete_all(case)
    sid = t.stage_ids(case)[-1]
    t.login("manager")
    t.post(f"/cases/{case}/reopen", stage_id=str(sid), reason="")
    assert t.one("SELECT archived_at FROM cases WHERE id = ?", case) is not None
    t.post(f"/cases/{case}/reopen", stage_id=str(sid), reason="Missing document found")
    assert t.one("SELECT archived_at FROM cases WHERE id = ?", case) is None
    assert stage_status(t, sid) == "In progress"
    assert "Acme Co" in t.page("/")


def test_owner_cannot_reopen(t):
    case = t.project()
    t.complete_all(case)
    t.login("analyst")
    assert t.post_status(f"/cases/{case}/reopen", stage_id=str(t.stage_ids(case)[0]), reason="x") == 403


def test_status_filters_split_overdue_from_on_track(t):
    t.project("Late Co", due="2020-01-01")
    t.project("Fine Co", due="2099-01-01")
    t.login("admin")
    overdue = t.page("/?status=overdue")
    assert "Late Co" in overdue and "Fine Co" not in overdue
    ontrack = t.page("/?status=ontrack")
    assert "Fine Co" in ontrack and "Late Co" not in ontrack


def test_search_matches_project_names(t):
    t.project("Harbor Foods")
    t.project("Summit Review")
    t.login("admin")
    page = t.page("/?q=harbor")
    assert "Harbor Foods" in page and "Summit Review" not in page
