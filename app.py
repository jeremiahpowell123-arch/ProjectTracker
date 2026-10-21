from datetime import date, datetime, timezone
from functools import wraps

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
    """A person's role as shown on screen. Administrators also show any additional role."""
    if user["role"] == "Administrator" and user["also_role"] in ("Analyst", "Manager"):
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
    if case["scope_state"] == "out":
        case["due_class"] = "outofscope"
        case["due_label"] = "Out of scope"
    elif complete:
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
    out = {"name": "Out of scope", "kind": "out", "cases": []}
    for case in cases:
        stage = case["current_stage"]
        if case["scope_state"] == "out":
            out["cases"].append(case)
        elif stage in by_name:
            by_name[stage]["cases"].append(case)
        else:
            other["cases"].append(case)
    if other["cases"]:
        columns.append(other)
    if out["cases"]:
        columns.append(out)
    return columns


CASE_QUERY = """
    SELECT c.*,
           o.name AS owner_name,
           o.initials AS owner_initials,
           m.name AS manager_name,
           m.initials AS manager_initials,
           t.name AS type_name,
           sa.name AS scope_actor_name,
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
    LEFT JOIN project_types t ON t.id = c.type_id
    LEFT JOIN users sa ON sa.id = c.scope_actor_id
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


def case_locked(case):
    """Archived and out-of-scope projects can't change until a manager steps in."""
    return case["archived_at"] is not None or case["scope_state"] == "out"


def locked_message(case):
    if case["archived_at"] is not None:
        return "This project is archived. A manager can reopen it."
    return "This project is out of scope. A manager can return it to scope."


def local_date(utc_text):
    """A stored UTC timestamp as a date on this computer's calendar."""
    return datetime.fromisoformat(utc_text).replace(tzinfo=timezone.utc).astimezone().date()


def finish_info(case):
    """Add finished-date wording for the Archive: on time, or how late."""
    finished = local_date(case["archived_at"])
    late = (finished - date.fromisoformat(case["due_date"])).days
    case["finished_on"] = f"{finished:%b} {finished.day}, {finished.year}"
    if late <= 0:
        case["finish_label"], case["finish_class"] = "On time", "complete"
    else:
        case["finish_label"], case["finish_class"] = f"{late} day{plural(late)} late", "overdue"
    return case


def maybe_archive(conn, case_id, actor_id):
    """Move a project to the Archive when its last stage is complete."""
    counts = conn.execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(status = 'Complete'), 0) AS done "
        "FROM case_stages WHERE case_id = ? AND archived = 0",
        (case_id,),
    ).fetchone()
    if counts["total"] == 0 or counts["done"] != counts["total"]:
        return False
    case = conn.execute(
        "SELECT scope_state, archived_at FROM cases WHERE id = ?", (case_id,)
    ).fetchone()
    if case["archived_at"] is not None:
        return False
    if case["scope_state"] == "pending":
        conn.execute(
            "UPDATE cases SET scope_state = 'active', scope_reason = NULL, "
            "scope_actor_id = NULL, scope_at = NULL WHERE id = ?",
            (case_id,),
        )
        log_history(
            conn, case_id, actor_id, "scope_withdraw",
            "the out-of-scope request because the project was completed",
        )
    conn.execute("UPDATE cases SET archived_at = CURRENT_TIMESTAMP WHERE id = ?", (case_id,))
    log_history(
        conn, case_id, actor_id, "archived",
        "the project after its last stage was completed",
    )
    return True


MAX_REASON = 500


def clean_reason(text):
    return " ".join(text.split())[:MAX_REASON]


MAX_NAME = 80


def clean_name(text):
    return " ".join(text.split())


def name_problem(name):
    if not name:
        return "Please enter a name."
    if len(name) > MAX_NAME:
        return f"Names can be up to {MAX_NAME} characters."
    return None


def active_types_with_tasks(conn):
    """Active project types, each with its active tasks in order."""
    types = []
    for t in conn.execute(
        "SELECT * FROM project_types WHERE active = 1 ORDER BY position, id"
    ).fetchall():
        tasks = conn.execute(
            "SELECT * FROM type_tasks WHERE type_id = ? AND active = 1 "
            "ORDER BY position, id",
            (t["id"],),
        ).fetchall()
        types.append({"id": t["id"], "name": t["name"], "tasks": tasks})
    return types


def admin_only(view):
    """Wrap a route so only signed-in Administrators reach it."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        if user["role"] != "Administrator":
            abort(403)
        return view(user, *args, **kwargs)
    return wrapper


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
    type_arg = request.args.get("type", "")
    hide_oos = request.args.get("hide_oos") == "1"

    conn = get_db()
    if user["role"] == "Administrator":
        rows = conn.execute(
            CASE_QUERY + " WHERE c.archived_at IS NULL ORDER BY c.due_date"
        ).fetchall()
    else:
        rows = conn.execute(
            CASE_QUERY + " WHERE c.archived_at IS NULL "
            "AND (c.owner_id = ? OR c.manager_id = ?) ORDER BY c.due_date",
            (user["id"], user["id"]),
        ).fetchall()
    active_types = active_types_with_tasks(conn)

    # The type filter offers active types, plus any removed type a visible project still uses.
    type_options = [{"id": t["id"], "name": t["name"]} for t in active_types]
    listed = {t["id"] for t in type_options}
    used = {r["type_id"] for r in rows if r["type_id"] is not None} - listed
    for r in conn.execute("SELECT id, name FROM project_types ORDER BY name").fetchall():
        if r["id"] in used:
            type_options.append({"id": r["id"], "name": f'{r["name"]} (removed)'})
    type_id = int(type_arg) if type_arg.isdigit() else None
    if type_id not in {t["id"] for t in type_options}:
        type_id = None

    # Board columns: the tasks of the chosen type, or of every active type.
    if type_id is not None:
        stage_names = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM type_tasks WHERE type_id = ? AND active = 1 "
                "ORDER BY position, id",
                (type_id,),
            ).fetchall()
        ]
    else:
        stage_names = []
        for t in active_types:
            for task in t["tasks"]:
                if task["name"] not in stage_names:
                    stage_names.append(task["name"])
    conn.close()

    cases = [due_info(row) for row in rows]

    # Out-of-scope requests this person can approve. Always shown, whatever the filters.
    needs_approval = [
        c for c in cases
        if c["scope_state"] == "pending"
        and (user["role"] == "Administrator" or c["manager_id"] == user["id"])
    ]

    if type_id is not None:
        cases = [c for c in cases if c["type_id"] == type_id]
    if mine:
        cases = [c for c in cases if user["id"] in (c["owner_id"], c["manager_id"])]
    if query:
        cases = [c for c in cases if query.lower() in c["customer_name"].lower()]

    oos_count = sum(1 for c in cases if c["scope_state"] == "out")
    if hide_oos:
        cases = [c for c in cases if c["scope_state"] != "out"]

    counts = {key: 0 for key, _ in STATUS_FILTERS}
    counts["all"] = len(cases)
    for c in cases:
        if c["due_class"] in counts:
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
        type_id=type_id,
        type_options=type_options,
        hide_oos=hide_oos,
        oos_count=oos_count,
        needs_approval=needs_approval,
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
    # Only project types that have at least one task can be used.
    types = [t for t in active_types_with_tasks(conn) if t["tasks"]]

    if request.method == "POST":
        customer = request.form.get("customer_name", "").strip()
        risk = request.form.get("risk_rating", "")
        due = request.form.get("due_date", "")
        owner_id = request.form.get("owner_id", "")
        manager_id = request.form.get("manager_id", "")
        chosen_type = next(
            (t for t in types if str(t["id"]) == request.form.get("type_id", "")), None
        )

        owner_ok = any(str(o["id"]) == owner_id for o in owners)
        manager_ok = any(str(m["id"]) == manager_id for m in managers)

        if not customer or risk not in RISK_LEVELS or not valid_date(due) \
                or not owner_ok or not manager_ok or chosen_type is None:
            flash("Please complete every field.", "error")
        elif owner_id == manager_id:
            flash("The owner and manager must be different people.", "error")
        else:
            cursor = conn.execute(
                "INSERT INTO cases "
                "(customer_name, risk_rating, due_date, owner_id, manager_id, created_by, type_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (customer, risk, due, owner_id, manager_id, user["id"], chosen_type["id"]),
            )
            case_id = cursor.lastrowid
            # Each project gets its own frozen copy of the type's tasks as they are now.
            # Later changes to the project type never reach projects already made.
            conn.execute(
                "INSERT INTO case_stages (case_id, name, position, high_priority) "
                "SELECT ?, name, position, high_priority FROM type_tasks "
                "WHERE type_id = ? AND active = 1 ORDER BY position, id",
                (case_id, chosen_type["id"]),
            )
            log_history(
                conn, case_id, user["id"], "created",
                f'the project ({chosen_type["name"]})',
            )
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
        types=types,
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
        locked=case_locked(case),
        is_requester=case["scope_actor_id"] == user["id"],
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
    if case_locked(case):
        conn.close()
        flash(locked_message(case), "error")
        return redirect(url_for("case_detail", case_id=case_id))

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
        archived = maybe_archive(conn, case_id, user["id"])
        conn.commit()
        flash(f'{stage["name"]} is now {new_status}.', "success")
        if archived:
            flash("All stages are complete, so the project moved to the Archive.", "success")
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
    if case_locked(case):
        conn.close()
        flash(locked_message(case), "error")
        return redirect(url_for("case_detail", case_id=case_id))

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
        archived = maybe_archive(conn, case_id, user["id"])
        conn.commit()
        flash(f'Override applied: {stage["name"]} is now {new_status}.', "success")
        if archived:
            flash("All stages are complete, so the project moved to the Archive.", "success")
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
    if case_locked(case):
        conn.close()
        flash(locked_message(case), "error")
        return redirect(url_for("case_detail", case_id=case_id))

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


def load_case_or_404(conn, user, case_id):
    case = get_case_for(conn, user, case_id)
    if case is None:
        conn.close()
        abort(404)
    return case


def back_to_case(case_id):
    return redirect(url_for("case_detail", case_id=case_id))


def clear_scope(conn, case_id):
    conn.execute(
        "UPDATE cases SET scope_state = 'active', scope_reason = NULL, "
        "scope_actor_id = NULL, scope_at = NULL WHERE id = ?",
        (case_id,),
    )


@app.route("/cases/<int:case_id>/scope/out", methods=["POST"])
def scope_out(case_id):
    """A manager or Administrator marks a project out of scope. The owner requests it."""
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    conn = get_db()
    case = load_case_or_404(conn, user, case_id)
    direct = can_override(user, case)
    if not direct and not can_edit(user, case):
        conn.close()
        abort(403)

    reason = clean_reason(request.form.get("reason", ""))
    if case["archived_at"] is not None:
        flash("An archived project can't be marked out of scope. Reopen it first.", "error")
    elif case["scope_state"] == "pending":
        flash("An out-of-scope request is already waiting for approval.", "error")
    elif case["scope_state"] == "out":
        flash("This project is already out of scope.", "error")
    elif not reason:
        flash("A reason is required.", "error")
    elif direct:
        conn.execute(
            "UPDATE cases SET scope_state = 'out', scope_reason = ?, "
            "scope_actor_id = ?, scope_at = CURRENT_TIMESTAMP WHERE id = ?",
            (reason, user["id"], case_id),
        )
        log_history(
            conn, case_id, user["id"], "scope_out",
            f"the project was marked out of scope | Reason: {reason}",
        )
        conn.commit()
        flash("Project marked out of scope. Its stages are now frozen.", "success")
    else:
        conn.execute(
            "UPDATE cases SET scope_state = 'pending', scope_reason = ?, "
            "scope_actor_id = ?, scope_at = CURRENT_TIMESTAMP WHERE id = ?",
            (reason, user["id"], case_id),
        )
        log_history(
            conn, case_id, user["id"], "scope_request",
            f"to mark the project out of scope | Reason: {reason}",
        )
        conn.commit()
        flash(
            f'Request sent. {case["manager_name"]} needs to approve it. '
            "You can keep working until then.", "success",
        )
    conn.close()
    return back_to_case(case_id)


@app.route("/cases/<int:case_id>/scope/decide", methods=["POST"])
def scope_decide(case_id):
    """The manager approves or declines an out-of-scope request."""
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    conn = get_db()
    case = load_case_or_404(conn, user, case_id)
    if not can_override(user, case):
        conn.close()
        abort(403)

    decision = request.form.get("decision", "")
    reason = clean_reason(request.form.get("reason", ""))
    if case["scope_state"] != "pending":
        flash("There is no out-of-scope request waiting for approval.", "error")
    elif decision == "approve":
        conn.execute(
            "UPDATE cases SET scope_state = 'out', scope_actor_id = ?, "
            "scope_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user["id"], case_id),
        )
        detail = "the out-of-scope request"
        if reason:
            detail += f" | Reason: {reason}"
        log_history(conn, case_id, user["id"], "scope_approve", detail)
        conn.commit()
        flash("Approved. The project is out of scope and its stages are frozen.", "success")
    elif decision == "decline":
        if not reason:
            flash("A reason is required to decline.", "error")
        else:
            clear_scope(conn, case_id)
            log_history(
                conn, case_id, user["id"], "scope_decline",
                f"the out-of-scope request | Reason: {reason}",
            )
            conn.commit()
            flash("Request declined. The project stays in scope.", "success")
    else:
        flash("Choose Approve or Decline.", "error")
    conn.close()
    return back_to_case(case_id)


@app.route("/cases/<int:case_id>/scope/withdraw", methods=["POST"])
def scope_withdraw(case_id):
    """Whoever sent an out-of-scope request can take it back."""
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    conn = get_db()
    case = load_case_or_404(conn, user, case_id)
    if case["scope_state"] != "pending" or case["scope_actor_id"] != user["id"]:
        conn.close()
        abort(403)
    clear_scope(conn, case_id)
    log_history(
        conn, case_id, user["id"], "scope_withdraw", "the out-of-scope request",
    )
    conn.commit()
    conn.close()
    flash("Request withdrawn.", "success")
    return back_to_case(case_id)


@app.route("/cases/<int:case_id>/scope/return", methods=["POST"])
def scope_return(case_id):
    """A manager or Administrator puts an out-of-scope project back in scope."""
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    conn = get_db()
    case = load_case_or_404(conn, user, case_id)
    if not can_override(user, case):
        conn.close()
        abort(403)

    reason = clean_reason(request.form.get("reason", ""))
    if case["scope_state"] != "out":
        flash("This project is not out of scope.", "error")
    elif not reason:
        flash("A reason is required.", "error")
    else:
        clear_scope(conn, case_id)
        log_history(
            conn, case_id, user["id"], "scope_return",
            f"the project was returned to scope | Reason: {reason}",
        )
        conn.commit()
        flash("Project returned to scope. Its stages can be changed again.", "success")
    conn.close()
    return back_to_case(case_id)


@app.route("/cases/<int:case_id>/reopen", methods=["POST"])
def reopen_case(case_id):
    """A manager or Administrator takes a finished project out of the Archive."""
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    conn = get_db()
    case = load_case_or_404(conn, user, case_id)
    if not can_override(user, case):
        conn.close()
        abort(403)

    stage_id = request.form.get("stage_id", "")
    reason = clean_reason(request.form.get("reason", ""))
    stage = None
    if stage_id.isdigit():
        stage = conn.execute(
            "SELECT * FROM case_stages WHERE id = ? AND case_id = ? AND archived = 0",
            (int(stage_id), case_id),
        ).fetchone()

    if case["archived_at"] is None:
        flash("This project is not archived.", "error")
    elif stage is None:
        flash("Choose the stage to move back to In progress.", "error")
    elif not reason:
        flash("A reason is required to reopen a project.", "error")
    else:
        conn.execute(
            "UPDATE case_stages SET status = 'In progress' WHERE id = ?", (stage["id"],)
        )
        conn.execute("UPDATE cases SET archived_at = NULL WHERE id = ?", (case_id,))
        log_history(
            conn, case_id, user["id"], "reopen",
            f'the project. {stage["name"]} is back to In progress | Reason: {reason}',
        )
        conn.commit()
        flash("Project reopened. It is back on the board.", "success")
    conn.close()
    return back_to_case(case_id)


@app.route("/archive")
def archive():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    query = request.args.get("q", "").strip()

    conn = get_db()
    if user["role"] == "Administrator":
        rows = conn.execute(
            CASE_QUERY + " WHERE c.archived_at IS NOT NULL ORDER BY c.archived_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            CASE_QUERY + " WHERE c.archived_at IS NOT NULL "
            "AND (c.owner_id = ? OR c.manager_id = ?) ORDER BY c.archived_at DESC",
            (user["id"], user["id"]),
        ).fetchall()
    conn.close()

    cases = [finish_info(due_info(row)) for row in rows]
    total = len(cases)
    if query:
        cases = [c for c in cases if query.lower() in c["customer_name"].lower()]
    return render_template(
        "archive.html",
        user=user,
        cases=cases,
        total=total,
        query=query,
        active_nav="archive",
    )


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
    # Administrators can have no additional role, or also work as a Contributor or Manager.
    also_role = request.form.get("also_role", "")
    if person["role"] == "Administrator" and also_role in ("None", "Analyst", "Manager"):
        conn.execute("UPDATE users SET also_role = ? WHERE id = ?", (also_role, person_id))
    conn.commit()
    conn.close()
    flash(f'Updated {person["name"]}.', "success")
    return redirect(url_for("admin_users"))


def back_to_workflows(type_id=None):
    anchor = f"type-{type_id}" if type_id else None
    return redirect(url_for("admin_workflows", _anchor=anchor))


def type_name_taken(conn, name, except_id=0):
    return conn.execute(
        "SELECT 1 FROM project_types WHERE active = 1 AND lower(name) = lower(?) AND id != ?",
        (name, except_id),
    ).fetchone() is not None


def task_name_taken(conn, type_id, name, except_id=0):
    return conn.execute(
        "SELECT 1 FROM type_tasks WHERE type_id = ? AND active = 1 "
        "AND lower(name) = lower(?) AND id != ?",
        (type_id, name, except_id),
    ).fetchone() is not None


def one_or_404(conn, sql, args):
    row = conn.execute(sql, args).fetchone()
    if row is None:
        conn.close()
        abort(404)
    return row


def next_task_position(conn, type_id):
    return conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM type_tasks "
        "WHERE type_id = ? AND active = 1",
        (type_id,),
    ).fetchone()[0]


@app.route("/admin/workflows")
@admin_only
def admin_workflows(user):
    conn = get_db()
    types = []
    for t in conn.execute(
        "SELECT * FROM project_types WHERE active = 1 ORDER BY position, id"
    ).fetchall():
        item = dict(t)
        item["tasks"] = conn.execute(
            "SELECT * FROM type_tasks WHERE type_id = ? AND active = 1 "
            "ORDER BY position, id", (t["id"],),
        ).fetchall()
        item["removed_tasks"] = conn.execute(
            "SELECT * FROM type_tasks WHERE type_id = ? AND active = 0 ORDER BY id DESC",
            (t["id"],),
        ).fetchall()
        item["case_count"] = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE type_id = ?", (t["id"],)
        ).fetchone()[0]
        types.append(item)
    removed_types = conn.execute(
        "SELECT * FROM project_types WHERE active = 0 ORDER BY name"
    ).fetchall()
    conn.close()
    return render_template(
        "admin_workflows.html",
        user=user,
        types=types,
        removed_types=removed_types,
        active_nav="workflows",
    )


@app.route("/admin/workflows/types", methods=["POST"])
@admin_only
def create_type(user):
    conn = get_db()
    name = clean_name(request.form.get("name", ""))
    problem = name_problem(name)
    if problem is None and type_name_taken(conn, name):
        problem = f'A project type named "{name}" already exists.'
    if problem:
        conn.close()
        flash(problem, "error")
        return back_to_workflows()
    position = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM project_types"
    ).fetchone()[0]
    type_id = conn.execute(
        "INSERT INTO project_types (name, position) VALUES (?, ?)", (name, position)
    ).lastrowid
    conn.commit()
    conn.close()
    flash(f'Project type "{name}" added. Add its tasks below.', "success")
    return back_to_workflows(type_id)


@app.route("/admin/workflows/types/<int:type_id>/rename", methods=["POST"])
@admin_only
def rename_type(user, type_id):
    conn = get_db()
    one_or_404(conn, "SELECT id FROM project_types WHERE id = ? AND active = 1", (type_id,))
    name = clean_name(request.form.get("name", ""))
    problem = name_problem(name)
    if problem is None and type_name_taken(conn, name, type_id):
        problem = f'A project type named "{name}" already exists.'
    if problem:
        flash(problem, "error")
    else:
        conn.execute("UPDATE project_types SET name = ? WHERE id = ?", (name, type_id))
        conn.commit()
        flash(f'Project type renamed to "{name}".', "success")
    conn.close()
    return back_to_workflows(type_id)


@app.route("/admin/workflows/types/<int:type_id>/remove", methods=["POST"])
@admin_only
def remove_type(user, type_id):
    conn = get_db()
    t = one_or_404(conn, "SELECT * FROM project_types WHERE id = ? AND active = 1", (type_id,))
    others = conn.execute(
        "SELECT COUNT(*) FROM project_types WHERE active = 1 AND id != ?", (type_id,)
    ).fetchone()[0]
    if others == 0:
        flash("At least one project type must stay active.", "error")
    else:
        conn.execute("UPDATE project_types SET active = 0 WHERE id = ?", (type_id,))
        conn.commit()
        flash(
            f'"{t["name"]}" removed. Existing projects keep their tasks, '
            "and you can restore it below.", "success",
        )
    conn.close()
    return back_to_workflows()


@app.route("/admin/workflows/types/<int:type_id>/restore", methods=["POST"])
@admin_only
def restore_type(user, type_id):
    conn = get_db()
    t = one_or_404(conn, "SELECT * FROM project_types WHERE id = ? AND active = 0", (type_id,))
    if type_name_taken(conn, t["name"], type_id):
        flash(f'Rename the active "{t["name"]}" type first, then restore this one.', "error")
        conn.close()
        return back_to_workflows()
    position = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM project_types"
    ).fetchone()[0]
    conn.execute(
        "UPDATE project_types SET active = 1, position = ? WHERE id = ?", (position, type_id)
    )
    conn.commit()
    conn.close()
    flash(f'"{t["name"]}" restored.', "success")
    return back_to_workflows(type_id)


@app.route("/admin/workflows/types/<int:type_id>/tasks", methods=["POST"])
@admin_only
def add_task(user, type_id):
    conn = get_db()
    one_or_404(conn, "SELECT id FROM project_types WHERE id = ? AND active = 1", (type_id,))
    name = clean_name(request.form.get("name", ""))
    high = 1 if request.form.get("high_priority") == "on" else 0
    problem = name_problem(name)
    if problem is None and task_name_taken(conn, type_id, name):
        problem = f'This project type already has a task named "{name}".'
    if problem:
        flash(problem, "error")
    else:
        conn.execute(
            "INSERT INTO type_tasks (type_id, name, position, high_priority) "
            "VALUES (?, ?, ?, ?)",
            (type_id, name, next_task_position(conn, type_id), high),
        )
        conn.commit()
        flash(f'Task "{name}" added. New projects of this type will include it.', "success")
    conn.close()
    return back_to_workflows(type_id)


@app.route("/admin/workflows/tasks/<int:task_id>/update", methods=["POST"])
@admin_only
def update_task(user, task_id):
    conn = get_db()
    task = one_or_404(conn, "SELECT * FROM type_tasks WHERE id = ? AND active = 1", (task_id,))
    name = clean_name(request.form.get("name", ""))
    high = 1 if request.form.get("high_priority") == "on" else 0
    problem = name_problem(name)
    if problem is None and task_name_taken(conn, task["type_id"], name, task_id):
        problem = f'This project type already has a task named "{name}".'
    if problem:
        flash(problem, "error")
    else:
        conn.execute(
            "UPDATE type_tasks SET name = ?, high_priority = ? WHERE id = ?",
            (name, high, task_id),
        )
        conn.commit()
        flash(f'Task "{name}" saved. Projects already in progress are not changed.', "success")
    conn.close()
    return back_to_workflows(task["type_id"])


@app.route("/admin/workflows/tasks/<int:task_id>/move", methods=["POST"])
@admin_only
def move_task(user, task_id):
    direction = request.form.get("direction", "")
    if direction not in ("up", "down"):
        abort(400)
    conn = get_db()
    task = one_or_404(conn, "SELECT * FROM type_tasks WHERE id = ? AND active = 1", (task_id,))
    order = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM type_tasks WHERE type_id = ? AND active = 1 "
            "ORDER BY position, id", (task["type_id"],),
        ).fetchall()
    ]
    i = order.index(task_id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]
        for position, tid in enumerate(order, start=1):
            conn.execute("UPDATE type_tasks SET position = ? WHERE id = ?", (position, tid))
        conn.commit()
    conn.close()
    return back_to_workflows(task["type_id"])


@app.route("/admin/workflows/tasks/<int:task_id>/remove", methods=["POST"])
@admin_only
def remove_task(user, task_id):
    conn = get_db()
    task = one_or_404(conn, "SELECT * FROM type_tasks WHERE id = ? AND active = 1", (task_id,))
    conn.execute("UPDATE type_tasks SET active = 0 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    flash(
        f'Task "{task["name"]}" removed from new projects. '
        "Projects already in progress keep it.", "success",
    )
    return back_to_workflows(task["type_id"])


@app.route("/admin/workflows/tasks/<int:task_id>/restore", methods=["POST"])
@admin_only
def restore_task(user, task_id):
    conn = get_db()
    task = one_or_404(conn, "SELECT * FROM type_tasks WHERE id = ? AND active = 0", (task_id,))
    if task_name_taken(conn, task["type_id"], task["name"]):
        flash(f'This project type already has a task named "{task["name"]}".', "error")
    else:
        conn.execute(
            "UPDATE type_tasks SET active = 1, position = ? WHERE id = ?",
            (next_task_position(conn, task["type_id"]), task_id),
        )
        conn.commit()
        flash(f'Task "{task["name"]}" restored at the end of the list.', "success")
    conn.close()
    return back_to_workflows(task["type_id"])


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