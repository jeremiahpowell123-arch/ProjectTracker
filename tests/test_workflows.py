"""Project types and tasks. In-flight projects must never change."""


def task_names(t, case_id):
    return [r["name"] for r in t.q(
        "SELECT name FROM case_stages WHERE case_id = ? AND archived = 0 ORDER BY position", case_id)]


def test_new_project_gets_a_copy_of_the_types_tasks(t):
    case = t.project()
    assert task_names(t, case) == [
        "Intake & Document Collection", "Due Diligence Performed",
        "Sent to QC", "Sent for Approval",
    ]


def test_admin_can_add_a_task_and_only_new_projects_get_it(t):
    old = t.project("Old Project")
    t.login("admin")
    t.post(f"/admin/workflows/types/{t.type_id()}/tasks", name="Compliance Review")
    new = t.project("New Project")
    assert "Compliance Review" not in task_names(t, old)
    assert "Compliance Review" in task_names(t, new)


def test_renaming_a_task_does_not_touch_in_flight_projects(t):
    old = t.project("Old Project")
    task = t.one("SELECT id FROM type_tasks WHERE name = 'Sent to QC'")
    t.login("admin")
    t.post(f"/admin/workflows/tasks/{task}/update", name="Quality Control")
    new = t.project("New Project")
    assert "Sent to QC" in task_names(t, old)
    assert "Quality Control" in task_names(t, new)
    assert "Sent to QC" not in task_names(t, new)


def test_removing_a_task_does_not_touch_in_flight_projects(t):
    old = t.project("Old Project")
    task = t.one("SELECT id FROM type_tasks WHERE name = 'Due Diligence Performed'")
    t.login("admin")
    t.post(f"/admin/workflows/tasks/{task}/remove")
    new = t.project("New Project")
    assert "Due Diligence Performed" in task_names(t, old)
    assert "Due Diligence Performed" not in task_names(t, new)


def test_admin_can_create_a_second_project_type(t):
    t.login("admin")
    t.post("/admin/workflows/types", name="Vendor Review")
    vendor = t.type_id("Vendor Review")
    assert vendor is not None
    t.post(f"/admin/workflows/types/{vendor}/tasks", name="Collect Contract")
    case = t.project("Vendor Co", type_id=vendor)
    assert task_names(t, case) == ["Collect Contract"]


def test_duplicate_type_names_are_refused(t):
    t.login("admin")
    t.post("/admin/workflows/types", name="Standard Project")
    assert t.one("SELECT COUNT(*) FROM project_types WHERE name = 'Standard Project'") == 1


def test_tasks_can_be_flagged_high_priority(t):
    t.login("admin")
    task = t.one("SELECT id FROM type_tasks WHERE name = 'Sent to QC'")
    t.post(f"/admin/workflows/tasks/{task}/update", name="Sent to QC", high_priority="on")
    assert t.one("SELECT high_priority FROM type_tasks WHERE id = ?", task) == 1
