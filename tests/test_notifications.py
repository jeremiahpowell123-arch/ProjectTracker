"""In-app notifications, including opting out."""


def settings(t, **overrides):
    values = dict(cat_assigned="on", cat_progress="on", cat_scope="on",
                  cat_lifecycle="on", cat_comments="on")
    values.update(overrides)
    return t.post("/notifications/settings", **values)


def test_owner_and_manager_hear_about_a_new_project(t):
    t.project("Nova")
    assert any("made you the owner of Nova" in m for m in t.notes("analyst"))
    assert any("made you the manager of Nova" in m for m in t.notes("manager"))


def test_the_person_who_acts_is_not_notified_of_their_own_action(t):
    t.project("Nova")
    assert t.notes("admin") == []


def test_manager_hears_about_status_changes(t):
    case = t.project("Nova")
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{t.stage_ids(case)[0]}/status", status="In progress")
    assert any("changed" in m and "Nova" in m for m in t.notes("manager"))
    assert not any("changed" in m for m in t.notes("analyst"))


def test_muting_a_category_stops_new_notifications(t):
    case = t.project("Nova")
    t.login("manager")
    settings(t, cat_progress="off")
    before = len(t.notes("manager"))
    t.login("analyst")
    t.post(f"/cases/{case}/stages/{t.stage_ids(case)[0]}/status", status="In progress")
    assert len(t.notes("manager")) == before


def test_settings_page_remembers_choices(t):
    t.login("manager")
    settings(t, cat_progress="off")
    assert 'name="cat_progress" value="off" checked' in t.page("/notifications/settings")
    assert 'name="cat_assigned" value="on" checked' in t.page("/notifications/settings")


def test_reassigning_notifies_new_and_old_owner(t):
    helper = t.add_user("helper", "Analyst")
    case = t.project("Nova")
    t.login("manager")
    t.post(f"/cases/{case}/reassign", new_owner_id=str(helper), reason="Workload")
    assert any("made you the owner of Nova" in m for m in t.notes("helper"))
    assert any("moved Nova" in m for m in t.notes("analyst"))


def test_override_notifies_the_owner(t):
    case = t.project("Nova")
    t.login("manager")
    t.post(f"/cases/{case}/override-status", stage_id=str(t.stage_ids(case)[0]),
           status="Complete", reason="Owner on PTO")
    assert any("overrode" in m for m in t.notes("analyst"))


def test_archive_notifies_the_manager(t):
    case = t.project("Nova")
    t.complete_all(case)
    assert any("Nova was archived" in m for m in t.notes("manager"))


def test_scope_decisions_notify_the_owner(t):
    case = t.project("Nova")
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer left")
    t.login("manager")
    t.post(f"/cases/{case}/scope/decide", decision="approve", reason="Confirmed")
    assert any("approved" in m for m in t.notes("analyst"))


def test_pending_approval_always_shows_even_when_everything_is_muted(t):
    case = t.project("Nova")
    t.login("manager")
    settings(t, cat_assigned="off", cat_progress="off", cat_scope="off",
             cat_lifecycle="off", cat_comments="off")
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer left")
    t.login("manager")
    assert 'class="bell-badge"' in t.page("/")
    inbox = t.page("/notifications")
    assert "Needs your approval" in inbox and "Out-of-scope request on Nova" in inbox


def test_pending_approval_clears_after_a_decision(t):
    case = t.project("Nova")
    t.login("analyst")
    t.post(f"/cases/{case}/scope/out", reason="Customer left")
    t.login("manager")
    t.post(f"/cases/{case}/scope/decide", decision="decline", reason="Still active")
    assert "Needs your approval" not in t.page("/notifications")


def test_opening_a_notification_marks_it_read_and_goes_to_the_project(t):
    case = t.project("Nova")
    t.login("analyst")
    note = t.q("SELECT id FROM notifications WHERE user_id = ?", t.uid("analyst"))[0]["id"]
    response = t.client.get(f"/notifications/{note}/open")
    assert response.status_code == 302 and f"/cases/{case}" in response.headers["Location"]
    assert t.one("SELECT read_at FROM notifications WHERE id = ?", note) is not None


def test_mark_all_read(t):
    t.project("Nova")
    t.login("analyst")
    t.post("/notifications/read-all")
    assert t.one("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
                 t.uid("analyst")) == 0


def test_nobody_can_open_someone_elses_notification(t):
    t.project("Nova")
    note = t.q("SELECT id FROM notifications WHERE user_id = ?", t.uid("analyst"))[0]["id"]
    t.login("manager")
    assert t.status(f"/notifications/{note}/open") == 404
