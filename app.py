from datetime import date, datetime, timezone

from flask import (
    Flask, abort, flash, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash

from db import get_db, init_db

app = Flask(__name__)
app.secret_key = "demo-only-change-before-real-use"

RISK_LEVELS = ["Low", "Medium", "High"]
STATUSES = ["Not started", "In progress", "Complete"]

STATUS_FILTERS = [
    ("all", "All"),
    ("ontrack", "On Track"),
    ("soon", "Due Soon"),
    ("overdue", "Overdue"),
    ("complete", "Complete"),
]

# The database stores the role "Analyst". The screens show "Contributor".
ROLE_LABELS = {"Analyst": "Contributor"}

# What people can be granted. Administrators and Managers get some by default;
# an Administrator can grant any of them to individual people on the Users screen.
PERMISSIONS = {
    "create_projects": (
        "Create projects",
        "Start new projects and choose the owner and manager.",
    ),
}
ROLE_DEFAULTS = {
    "Administrator": set(PERMISSIONS),
    "Manager": {"create_projects"},
    "Analyst": set(),
}

init_db()


def permissions_of(user, conn=None):
    """Everything this person may do: their role's defaults plus individual grants."""
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    rows = conn.execute(
        "SELECT permission FROM user_permissions WHERE user_id = ?", (user["id"],)
    ).fetchall()
    if own_conn:
        conn.close()
    granted = {r["permission"] for r in rows} & set(PERMISSIONS)
    return ROLE_DEFAULTS.get(user["role"], set()) | granted


@app.template_global("has_permission")
def has_permission(user, permission):
    return permission in permissions_of(user)


@app.template_filter("role_label")
def role_label(role):
    return ROLE_LABELS.get(role, role)


@app.template_filter("role_display")
def role_display(user):
    """A person's role as shown on screen. Administrators also show their second role."""
    if user["role"] == "Administrator":
        return f"Administrator · {role_label(user['also_role'])}"
    return role_label(user["role"])


def is_manager(user):
    """Managers, plus Administrators who also work as a Manager."""
    return user["role"] == "Manager" or (
        user["role"] == "Administrator" and user["also_role"] == "Manager"
    )


@app.template_filter("pretty_date")
def pretty_date(text):
    try:
        d = date.fromisoformat(text)
    except (TypeError, ValueError):
        return text
    return f"{d:%b} {d.day}, {d.year}"


@app.template_filter("avatar_class")
def avatar_class(user_id):
    return f"av-{int(user_id) % 5}"


@app.template_filter("pretty_time")
def pretty_time(text):
    """Show a stored UTC timestamp in this computer's local time."""
    try:
        d = datetime.fromisoformat(text).replace(tzinfo=timezone.utc).astimezone()
    except (TypeError, ValueError):
        return text
    hour = d.hour % 12 or 12
    return f"{d:%b} {d.day}, {hour}:{d:%M} {d:%p}"


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def plural(n):
    return "" if n == 1 else "s"


def valid_date(text):
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def due_info(row):
    """Turn a database row into a dict with a due-date label and color class."""
    case = dict(row)
    days = (date.fromisoformat(case["due_date"]) - date.today()).days
    complete = case["total_stages"] > 0 and case["done_stages"] == case["total_stages"]
    if complete:
        case["due_class"] = "complete"
        case["due_label"] = "Complete"
    elif days < 0:
        case["due_class"] = "overdue"
        case["due_label"] = f"Overdue · {-days} day{plural(-days)}"
    elif days == 0:
        case["due_class"] = "soon"
        case["due_label"] = "Due today"
    elif days <= 3:
        case["due_class"] = "soon"
        case["due_label"] = f"Due in {days} day{plural(days)}"
    else:
        case["due_class"] = "ontrack"
        case["due_label"] = f"Due in {days} days"
    return case


def build_columns(cases, stage_names):
    """Group projects into board columns by their current stage."""
    columns = [{"name": n, "kind": "stage", "cases": []} for n in stage_names]
    by_name = {col["name"]: col for col in columns}
    other = {"name": "Other stages", "kind": "other", "cases": []}
    done = {"name": "Complete", "kind": "done", "cases": []}
    for case in cases:
        stage = case["current_stage"]
        if stage is None and case["total_stages"] > 0:
            done["cases"].append(case)
        elif stage in by_name:
            by_name[stage]["cases"].append(case)
        else:
            other["cases"].append(case)
    if other["cases"]:
        columns.append(other)
    columns.append(done)
    return columns


CASE_QUERY = """
    SELECT c.*,
           o.name AS owner_name,
           o.initials AS owner_initials,
           m.name AS manager_name,
           m.initials AS manager_initials,
           (SELECT COUNT(*) FROM case_stages s
             WHERE s.case_id = c.id AND s.archived = 0) AS total_stages,
           (SELECT COUNT(*) FROM case_stages s
             WHERE s.case_id = c.id AND s.archived = 0
               AND s.status = 'Complete') AS done_stages,
           (SELECT s.name FROM case_stages s
             WHERE s.case_id = c.id AND s.archived = 0
               AND s.status != 'Complete'
             ORDER BY s.position LIMIT 1) AS current_stage
    FROM cases c
    JOIN users o ON o.id = c.owner_id
    JOIN users m ON m.id = c.manager_id
"""


def log_history(conn, case_id, actor_id, action, detail):
    """Write one line to a project's read-only history."""
    conn.execute(
        "INSERT INTO history (case_id, actor_id, action, detail) VALUES (?, ?, ?, ?)",
        (case_id, actor_id, action, detail),
    )


def get_case_for(conn, user, case_id):
    """Return the project if this person may see it, otherwise None."""
    row = conn.execute(CASE_QUERY + " WHERE c.id = ?", (case_id,)).fetchone()
    if row is None:
        return None
    if user["role"] != "Administrator" and user["id"] not in (
        row["owner_id"], row["manager_id"]
    ):
        return None
    return due_info(row)


def can_edit(user, case):
    """Only the project's owner changes stage status day to day."""
    return user["id"] == case["owner_id"]


def can_override(user, case):
    """The project's manager and Administrators can step in when needed."""
    return user["role"] == "Administrator" or user["id"] == case["manager_id"]


MAX_REASON = 500


def clean_reason(text):
    return " ".join(text.split())[:MAX_REASON]


@app.route("/")
def home():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))

    view = request.args.get("view", "board")
    if view not in ("board", "list"):
        view = "board"
    status = request.args.get("status", "all")
    if status not in dict(STATUS_FILTERS):
        status = "all"
    query = request.args.get("q", "").strip()
    mine = request.args.get("mine") == "1"

    conn = get_db()
    if user["role"] == "Administrator":
        rows = conn.execute(CASE_QUERY + " ORDER BY c.due_date").fetchall()
    else:
        rows = conn.execute(
            CASE_QUERY + " WHERE c.owner_id = ? OR c.manager_id = ? ORDER BY c.due_date",
            (user["id"], user["id"]),
        ).fetchall()
    stage_names = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM template_stages WHERE active = 1 ORDER BY position"
        ).fetchall()
    ]
    conn.close()

    cases = [due_info(row) for row in rows]
    if mine:
        cases = [c for c in cases if user["id"] in (c["owner_id"], c["manager_id"])]
    if query:
        cases = [c for c in cases if query.lower() in c["customer_name"].lower()]

    counts = {key: 0 for key, _ in STATUS_FILTERS}
    counts["all"] = len(cases)
    for c in cases:
        counts[c["due_class"]] += 1
    if status != "all":
        cases = [c for c in cases if c["due_class"] == status]

    return render_template(
        "home.html",
        user=user,
        cases=cases,
        columns=build_columns(cases, stage_names),
        view=view,
        status=status,
        query=query,
        mine=mine,
        counts=counts,
        status_filters=STATUS_FILTERS,
        active_nav="mine" if mine else "all",
    )


@app.route("/cases/new", methods=["GET", "POST"])
def new_case():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    if not has_permission(user, "create_projects"):
        abort(403)

    conn = get_db()
    owners = conn.execute(
        "SELECT id, name, role, also_role FROM users ORDER BY name"
    ).fetchall()
    managers = [p for p in owners if is_manager(p)]
    stages = conn.execute(
        "SELECT name, high_priority FROM template_stages "
        "WHERE active = 1 ORDER BY position"
    ).fetchall()

    if request.method == "POST":
        customer = request.form.get("customer_name", "").strip()
        risk = request.form.get("risk_rating", "")
        due = request.form.get("due_date", "")
        owner_id = request.form.get("owner_id", "")
        manager_id = request.form.get("manager_id", "")

        owner_ok = any(str(o["id"]) == owner_id for o in owners)
        manager_ok = any(str(m["id"]) == manager_id for m in managers)

        if not customer or risk not in RISK_LEVELS or not valid_date(due) \
                or not owner_ok or not manager_ok:
            flash("Please complete every field.", "error")
        elif owner_id == manager_id:
            flash("The owner and manager must be different people.", "error")
        else:
            cursor = conn.execute(
                "INSERT INTO cases "
                "(customer_name, risk_rating, due_date, owner_id, manager_id, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (customer, risk, due, owner_id, manager_id, user["id"]),
            )
            case_id = cursor.lastrowid
            # Each project gets its own copy of the current master workflow.
            conn.execute(
                "INSERT INTO case_stages (case_id, name, position, high_priority) "
                "SELECT ?, name, position, high_priority FROM template_stages "
                "WHERE active = 1 ORDER BY position",
                (case_id,),
            )
            log_history(conn, case_id, user["id"], "created", "the project")
            conn.commit()
            conn.close()
            flash(f"Project created: {customer}.", "success")
            return redirect(url_for("home"))

    conn.close()
    return render_template(
        "new_case.html",
        user=user,
        owners=owners,
        managers=managers,
        stages=stages,
        risk_levels=RISK_LEVELS,
        form=request.form,
        active_nav="new",
    )


@app.route("/cases/<int:case_id>")
def case_detail(case_id):
    user = current_user()
    if user is None:
        return redirect(url_for("login"))

    conn = get_db()
    case = get_case_for(conn, user, case_id)
    if case is None:
        conn.close()
        abort(404)

    stages = conn.execute(
        "SELECT * FROM case_stages WHERE case_id = ? AND archived = 0 ORDER BY position",
        (case_id,),
    ).fetchall()
    activity = conn.execute(
        "SELECT h.*, u.name AS actor_name, u.initials AS actor_initials "
        "FROM history h JOIN users u ON u.id = h.actor_id "
        "WHERE h.case_id = ? ORDER BY h.id DESC LIMIT 20",
        (case_id,),
    ).fetchall()
    new_owner_choices = conn.execute(
        "SELECT id, name, role FROM users WHERE id NOT IN (?, ?) ORDER BY name",
        (case["owner_id"], case["manager_id"]),
    ).fetchall()
    conn.close()

    return render_template(
        "case_detail.html",
        user=user,
        case=case,
        stages=stages,
        activity=activity,
        statuses=STATUSES,
        new_owner_choices=new_owner_choices,
        can_edit=can_edit(user, case),
        can_override=can_override(user, case),
        active_nav="all",
    )


@app.route("/cases/<int:case_id>/stages/<int:stage_id>/status", methods=["POST"])
def update_stage_status(case_id, stage_id):
    user = current_user()
    if user is None:
        return redirect(url_for("login"))

    new_status = request.form.get("status", "")
    conn = get_db()
    case = get_case_for(conn, user, case_id)
    if case is None:
        conn.close()
        abort(404)
    if not can_edit(user, case):
        conn.close()
        abort(403)

    stage = conn.execute(
        "SELECT * FROM case_stages WHERE id = ? AND case_id = ? AND archived = 0",
        (stage_id, case_id),
    ).fetchone()
    if stage is None or new_status not in STATUSES:
        conn.close()
        abort(400)

    if new_status != stage["status"]:
        conn.execute(
            "UPDATE case_stages SET status = ? WHERE id = ?", (new_status, stage_id)
        )
        log_history(
            conn, case_id, user["id"], "status_change",
            f'{stage["name"]}: {stage["status"]} → {new_status}',
        )
        conn.commit()
        flash(f'{stage["name"]} is now {new_status}.', "success")
    conn.close()
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/cases/<int:case_id>/override-status", methods=["POST"])
def override_status(case_id):
    user = current_user()
    if user is None:
        return redirect(url_for("login"))

    conn = get_db()
    case = get_case_for(conn, user, case_id)
    if case is None:
        conn.close()
        abort(404)
    if not can_override(user, case):
        conn.close()
        abort(403)

    stage_id = request.form.get("stage_id", "")
    new_status = request.form.get("status", "")
    reason = clean_reason(request.form.get("reason", ""))

    stage = None
    if stage_id.isdigit():
        stage = conn.execute(
            "SELECT * FROM case_stages WHERE id = ? AND case_id = ? AND archived = 0",
            (int(stage_id), case_id),
        ).fetchone()

    if stage is None or new_status not in STATUSES:
        flash("Choose a stage and a status.", "error")
    elif not reason:
        flash("A reason is required for an override.", "error")
    elif new_status == stage["status"]:
        flash(f'{stage["name"]} is already {new_status}.', "error")
    else:
        conn.execute(
            "UPDATE case_stages SET status = ? WHERE id = ?", (new_status, stage["id"])
        )
        log_history(
            conn, case_id, user["id"], "override_status",
            f'{stage["name"]}: {stage["status"]} → {new_status} | Reason: {reason}',
        )
        conn.commit()
        flash(f'Override applied: {stage["name"]} is now {new_status}.', "success")
    conn.close()
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/cases/<int:case_id>/reassign", methods=["POST"])
def reassign_owner(case_id):
    user = current_user()
    if user is None:
        return redirect(url_for("login"))

    conn = get_db()
    case = get_case_for(conn, user, case_id)
    if case is None:
        conn.close()
        abort(404)
    if not can_override(user, case):
        conn.close()
        abort(403)

    new_owner_id = request.form.get("new_owner_id", "")
    reason = clean_reason(request.form.get("reason", ""))

    new_owner = None
    if new_owner_id.isdigit():
        new_owner = conn.execute(
            "SELECT id, name FROM users WHERE id = ?", (int(new_owner_id),)
        ).fetchone()

    if new_owner is None:
        flash("Choose the new owner.", "error")
    elif new_owner["id"] == case["manager_id"]:
        flash("The owner and manager must be different people.", "error")
    elif new_owner["id"] == case["owner_id"]:
        flash(f'{new_owner["name"]} is already the owner.', "error")
    elif not reason:
        flash("A reason is required to reassign a project.", "error")
    else:
        conn.execute(
            "UPDATE cases SET owner_id = ? WHERE id = ?", (new_owner["id"], case_id)
        )
        log_history(
            conn, case_id, user["id"], "reassign",
            f'the owner from {case["owner_name"]} to {new_owner["name"]} | Reason: {reason}',
        )
        conn.commit()
        flash(f'Project reassigned to {new_owner["name"]}.', "success")
    conn.close()
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/admin/users")
def admin_users():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    if user["role"] != "Administrator":
        abort(403)

    conn = get_db()
    people = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    rows = []
    for person in people:
        inherited = ROLE_DEFAULTS.get(person["role"], set())
        rows.append({
            "user": person,
            "inherited": inherited,
            "effective": permissions_of(person, conn),
            "editable": person["role"] == "Administrator"
            or bool(set(PERMISSIONS) - inherited),
        })
    conn.close()
    return render_template(
        "admin_users.html",
        user=user,
        rows=rows,
        permissions=PERMISSIONS,
        active_nav="users",
    )


@app.route("/admin/users/<int:person_id>/permissions", methods=["POST"])
def update_permissions(person_id):
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    if user["role"] != "Administrator":
        abort(403)

    conn = get_db()
    person = conn.execute("SELECT * FROM users WHERE id = ?", (person_id,)).fetchone()
    if person is None:
        conn.close()
        abort(404)

    # Only individual grants are stored. Anything a role already includes is skipped.
    inherited = ROLE_DEFAULTS.get(person["role"], set())
    wanted = {
        p for p in PERMISSIONS
        if request.form.get(f"perm_{p}") == "on" and p not in inherited
    }
    conn.execute("DELETE FROM user_permissions WHERE user_id = ?", (person_id,))
    for p in sorted(wanted):
        conn.execute(
            "INSERT INTO user_permissions (user_id, permission) VALUES (?, ?)",
            (person_id, p),
        )
    # Administrators can also work as a Contributor or as a Manager.
    also_role = request.form.get("also_role", "")
    if person["role"] == "Administrator" and also_role in ("Analyst", "Manager"):
        conn.execute("UPDATE users SET also_role = ? WHERE id = ?", (also_role, person_id))
    conn.commit()
    conn.close()
    flash(f'Updated {person["name"]}.', "success")
    return redirect(url_for("admin_users"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        if user is not None and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
        flash("Incorrect username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)