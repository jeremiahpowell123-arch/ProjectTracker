from datetime import date

from flask import (
    Flask, abort, flash, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash

from db import get_db, init_db

app = Flask(__name__)
app.secret_key = "demo-only-change-before-real-use"

RISK_LEVELS = ["Low", "Medium", "High"]

init_db()


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


CASE_QUERY = """
    SELECT c.*,
           o.name AS owner_name,
           m.name AS manager_name,
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


@app.route("/")
def home():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))

    conn = get_db()
    if user["role"] == "Administrator":
        rows = conn.execute(CASE_QUERY + " ORDER BY c.due_date").fetchall()
    else:
        rows = conn.execute(
            CASE_QUERY + " WHERE c.owner_id = ? OR c.manager_id = ? ORDER BY c.due_date",
            (user["id"], user["id"]),
        ).fetchall()
    conn.close()

    cases = [due_info(row) for row in rows]
    return render_template("home.html", user=user, cases=cases)


@app.route("/cases/new", methods=["GET", "POST"])
def new_case():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    if user["role"] != "Administrator":
        abort(403)

    conn = get_db()
    owners = conn.execute(
        "SELECT id, name, role FROM users ORDER BY name"
    ).fetchall()
    managers = conn.execute(
        "SELECT id, name, role FROM users "
        "WHERE role IN ('Manager', 'Administrator') ORDER BY name"
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
            # Each case gets its own copy of the current master workflow.
            conn.execute(
                "INSERT INTO case_stages (case_id, name, position, high_priority) "
                "SELECT ?, name, position, high_priority FROM template_stages "
                "WHERE active = 1 ORDER BY position",
                (case_id,),
            )
            conn.commit()
            conn.close()
            flash(f"Case created for {customer}.", "success")
            return redirect(url_for("home"))

    conn.close()
    return render_template(
        "new_case.html",
        user=user,
        owners=owners,
        managers=managers,
        risk_levels=RISK_LEVELS,
        form=request.form,
    )


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