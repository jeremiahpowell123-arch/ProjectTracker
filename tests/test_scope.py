"""Out of scope: contributors request, managers decide."""


def state(t, case):
    row = t.q("SELECT scope_state, scope_reason FROM cases WHERE id = ?", case)[0]
    return row["scope_state"], row["scope_reason"]


def test_contributor_request_waits_for_the_manager(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer withdrew")
    assert state(t, case) == ("pending", "Customer withdrew")


def test_request_needs_a_reason(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="   ")
    assert state(t, case)[0] == "active"


def test_work_can_continue_while_a_request_is_pending(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Maybe leaving")
    t.post(f"/cases/{case}/stages/{sid}/status", status="In progress")
    assert t.one("SELECT status FROM case_stages WHERE id = ?", sid) == "In progress"


def test_manager_approval_freezes_the_project(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer left")
    t.login("manager")
    t.post(f"/cases/{case}/scope/decide", decision="approve", reason="Confirmed")
    assert state(t, case)[0] == "out"
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{sid}/status", status="Complete")
    assert t.one("SELECT status FROM case_stages WHERE id = ?", sid) == "Not started"
    t.login("manager")
    t.post(f"/cases/{case}/override-status", stage_id=str(sid), status="Complete", reason="x")
    assert t.one("SELECT status FROM case_stages WHERE id = ?", sid) == "Not started"


def test_declining_needs_a_reason_and_keeps_the_project_in_scope(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer left")
    t.login("manager")
    t.post(f"/cases/{case}/scope/decide", decision="decline", reason="")
    assert state(t, case)[0] == "pending"
    t.post(f"/cases/{case}/scope/decide", decision="decline", reason="Still active")
    assert state(t, case)[0] == "active"


def test_contributor_cannot_decide_their_own_request(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer left")
    assert t.post_status(f"/cases/{case}/scope/decide", decision="approve") == 403
    assert state(t, case)[0] == "pending"


def test_requester_can_withdraw(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer left")
    t.post(f"/cases/{case}/scope/withdraw")
    assert state(t, case)[0] == "active"


def test_manager_can_mark_out_of_scope_directly(t):
    case = t.project()
    t.login("manager")
    t.post(f"/cases/{case}/scope/out", reason="Duplicate project")
    assert state(t, case) == ("out", "Duplicate project")


def test_return_to_scope_needs_a_reason_and_unfreezes(t):
    case = t.project()
    sid = t.stage_ids(case)[0]
    t.login("manager")
    t.post(f"/cases/{case}/scope/out", reason="Paused")
    t.post(f"/cases/{case}/scope/return", reason="")
    assert state(t, case)[0] == "out"
    t.post(f"/cases/{case}/scope/return", reason="Customer is back")
    assert state(t, case)[0] == "active"
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{sid}/status", status="In progress")
    assert t.one("SELECT status FROM case_stages WHERE id = ?", sid) == "In progress"


def test_out_of_scope_toggle_hides_and_shows(t):
    case = t.project("Frozen Co")
    t.project("Active Co")
    t.login("manager")
    t.post(f"/cases/{case}/scope/out", reason="Paused")
    shown = t.page("/")
    assert "Frozen Co" in shown and "Active Co" in shown
    hidden = t.page("/?hide_oos=1")
    assert "Frozen Co" not in hidden and "Active Co" in hidden


def test_out_of_scope_projects_are_not_counted_overdue(t):
    case = t.project("Old Co", due="2020-01-01")
    t.login("manager")
    t.post(f"/cases/{case}/scope/out", reason="Paused")
    assert "Old Co" not in t.page("/?status=overdue")


def test_completing_a_project_cancels_a_pending_request(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Maybe")
    t.complete_all(case)
    row = t.q("SELECT scope_state, archived_at FROM cases WHERE id = ?", case)[0]
    assert row["scope_state"] == "active" and row["archived_at"] is not None


def test_archived_projects_cannot_be_marked_out_of_scope(t):
    case = t.project()
    t.complete_all(case)
    t.login("manager")
    t.post(f"/cases/{case}/scope/out", reason="Too late")
    assert state(t, case)[0] == "active"


def test_deciding_when_nothing_is_pending_changes_nothing(t):
    case = t.project()
    t.login("manager")
    t.post(f"/cases/{case}/scope/decide", decision="approve", reason="x")
    assert state(t, case)[0] == "active"
