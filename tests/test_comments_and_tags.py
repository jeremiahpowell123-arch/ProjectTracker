"""Comments (with auto-stamped initials) and free-form tags."""


# ---- comments ---------------------------------------------------------------

def test_comment_is_saved_with_the_authors_initials(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/comments", body="Documents received")
    page = t.page(f"/cases/{case}")
    assert "Documents received" in page
    assert 'comment-stamp">AN<' in page


def test_empty_comment_is_rejected(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/comments", body="   ")
    assert t.one("SELECT COUNT(*) FROM comments") == 0


def test_comment_html_is_escaped(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/comments", body="<script>alert(1)</script>")
    page = t.page(f"/cases/{case}")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_comment_notifies_the_other_people_on_the_project(t):
    case = t.project("Nova")
    t.login("analyst")
    t.post(f"/cases/{case}/comments", body="Update")
    assert any("commented on Nova" in m for m in t.notes("manager"))
    assert not any("commented" in m for m in t.notes("analyst"))


def test_people_outside_the_project_can_read_but_not_comment(t):
    t.add_user("other", "Analyst")
    case = t.project()
    t.login("other")
    assert t.status(f"/cases/{case}") == 200
    assert t.post_status(f"/cases/{case}/comments", body="Hello") == 403
    assert t.one("SELECT COUNT(*) FROM comments") == 0


def test_author_can_archive_and_restore_their_comment(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/comments", body="First note")
    comment = t.one("SELECT id FROM comments")
    t.post(f"/cases/{case}/comments/{comment}/archive", action="archive")
    assert "First note" not in t.page(f"/cases/{case}")
    assert "First note" in t.page(f"/cases/{case}?comments=all")
    t.post(f"/cases/{case}/comments/{comment}/archive", action="restore", show="all")
    assert "First note" in t.page(f"/cases/{case}")


def test_manager_can_archive_anyones_comment_but_contributor_cannot_archive_a_managers(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/comments", body="Analyst note")
    t.login("manager")
    t.post(f"/cases/{case}/comments", body="Manager note")
    manager_comment = t.one("SELECT id FROM comments WHERE author_id = ?", t.uid("manager"))
    analyst_comment = t.one("SELECT id FROM comments WHERE author_id = ?", t.uid("analyst"))
    assert t.post_status(f"/cases/{case}/comments/{analyst_comment}/archive", action="archive") == 302
    t.login("analyst")
    assert t.post_status(f"/cases/{case}/comments/{manager_comment}/archive", action="archive") == 403


def test_comments_are_allowed_on_archived_projects(t):
    case = t.project()
    t.complete_all(case)
    t.post(f"/cases/{case}/comments", body="After the fact")
    assert "After the fact" in t.page(f"/cases/{case}")


# ---- tags -------------------------------------------------------------------

def tags(t, case):
    return [r["tag"] for r in t.q("SELECT tag FROM case_tags WHERE case_id = ? ORDER BY created_at, tag_key", case)]


def test_tags_are_tidied_and_saved(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/tags", tag="  #Rush  ")
    assert tags(t, case) == ["Rush"]


def test_tags_ignore_case_when_checking_for_duplicates(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/tags", tag="Rush")
    t.post(f"/cases/{case}/tags", tag="rush")
    assert tags(t, case) == ["Rush"]


def test_an_existing_tags_spelling_is_reused(t):
    a, b = t.project("One"), t.project("Two")
    t.login("manager")
    t.post(f"/cases/{a}/tags", tag="Legal Hold")
    t.post(f"/cases/{b}/tags", tag="legal hold")
    assert tags(t, b) == ["Legal Hold"]


def test_bad_tags_are_refused(t):
    case = t.project()
    t.login("analyst")
    for bad in ("", "   ", "<script>", "x" * 25):
        t.post(f"/cases/{case}/tags", tag=bad)
    assert tags(t, case) == []


def test_a_project_holds_at_most_eight_tags(t):
    case = t.project()
    t.login("analyst")
    for i in range(10):
        t.post(f"/cases/{case}/tags", tag=f"tag{i}")
    assert len(tags(t, case)) == 8


def test_tags_can_be_removed_and_changes_are_logged(t):
    case = t.project()
    t.login("analyst")
    t.post(f"/cases/{case}/tags", tag="Rush")
    t.post(f"/cases/{case}/tags/remove", tag_key="rush")
    assert tags(t, case) == []
    actions = [r["action"] for r in t.q("SELECT action FROM history WHERE case_id = ?", case)]
    assert "tag_add" in actions and "tag_remove" in actions


def test_tag_filter_narrows_the_board(t):
    a, _ = t.project("Tagged Co"), t.project("Plain Co")
    t.login("admin")
    t.post(f"/cases/{a}/tags", tag="Rush")
    page = t.page("/?tag=rush")
    assert "Tagged Co" in page and "Plain Co" not in page


def test_unknown_tag_filter_is_ignored(t):
    t.project("Plain Co")
    t.login("admin")
    assert "Plain Co" in t.page("/?tag=does-not-exist")
