import os
import sqlite3
from werkzeug.security import generate_password_hash

DB_PATH = os.environ.get("TRACKER_DB", "tracker.db")

# The first project type, made when the database is new. Administrators can
# rename it, change its tasks, or add other project types.
DEFAULT_TYPE_NAME = "Standard Project"

# Its starting tasks: (task name, high priority?)
DEFAULT_STAGES = [
    ("Intake & Document Collection", 0),
    ("Due Diligence Performed", 0),
    ("Sent to QC", 0),
    ("Sent for Approval", 1),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    initials TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    also_role TEXT NOT NULL DEFAULT 'None'
);

CREATE TABLE IF NOT EXISTS user_permissions (
    user_id INTEGER NOT NULL REFERENCES users(id),
    permission TEXT NOT NULL,
    PRIMARY KEY (user_id, permission)
);

CREATE TABLE IF NOT EXISTS project_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS type_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id INTEGER NOT NULL REFERENCES project_types(id),
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    high_priority INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

-- Older versions kept one master workflow here. It is now only read once,
-- to carry an existing workflow over into the first project type.
CREATE TABLE IF NOT EXISTS template_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    high_priority INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT NOT NULL,
    risk_rating TEXT NOT NULL,
    due_date TEXT NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    manager_id INTEGER NOT NULL REFERENCES users(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    type_id INTEGER REFERENCES project_types(id),
    archived_at TEXT,
    scope_state TEXT NOT NULL DEFAULT 'active',
    scope_reason TEXT,
    scope_actor_id INTEGER REFERENCES users(id),
    scope_at TEXT
);

CREATE TABLE IF NOT EXISTS case_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Not started',
    high_priority INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'template',
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    case_id INTEGER NOT NULL REFERENCES cases(id),
    actor_id INTEGER REFERENCES users(id),
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    archived_by INTEGER REFERENCES users(id)
);

-- Free-form tags. tag_key is the lowercase form, so "Rush" and "rush" are one tag.
CREATE TABLE IF NOT EXISTS case_tags (
    case_id INTEGER NOT NULL REFERENCES cases(id),
    tag TEXT NOT NULL,
    tag_key TEXT NOT NULL,
    added_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (case_id, tag_key)
);

-- People who may see the details of a restricted project, besides its owner,
-- its manager, and the Administrators.
CREATE TABLE IF NOT EXISTS case_viewers (
    case_id INTEGER NOT NULL REFERENCES cases(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    added_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (case_id, user_id)
);

-- Small on/off settings an Administrator can change, such as demo mode.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Only the choices a person has made are stored. No row means the category is on.
CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id INTEGER NOT NULL REFERENCES users(id),
    category TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    PRIMARY KEY (user_id, category)
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)

    # Databases made before the second-role feature need the new column added.
    user_columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "also_role" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN also_role TEXT NOT NULL DEFAULT 'None'"
        )

    case_columns = {r["name"] for r in conn.execute("PRAGMA table_info(cases)")}
    if "type_id" not in case_columns:
        conn.execute("ALTER TABLE cases ADD COLUMN type_id INTEGER REFERENCES project_types(id)")

    if "restricted" not in {r["name"] for r in conn.execute("PRAGMA table_info(cases)")}:
        conn.execute("ALTER TABLE cases ADD COLUMN restricted INTEGER NOT NULL DEFAULT 0")

    # Archive and out-of-scope columns. Projects that were already finished
    # when this feature arrived are moved to the archive once.
    case_columns = {r["name"] for r in conn.execute("PRAGMA table_info(cases)")}
    if "archived_at" not in case_columns:
        conn.execute("ALTER TABLE cases ADD COLUMN archived_at TEXT")
        conn.execute(
            "UPDATE cases SET archived_at = COALESCE("
            "(SELECT MAX(created_at) FROM history WHERE history.case_id = cases.id), "
            "cases.created_at) "
            "WHERE (SELECT COUNT(*) FROM case_stages s "
            "       WHERE s.case_id = cases.id AND s.archived = 0) > 0 "
            "AND (SELECT COUNT(*) FROM case_stages s "
            "     WHERE s.case_id = cases.id AND s.archived = 0 "
            "       AND s.status != 'Complete') = 0"
        )
    if "scope_state" not in case_columns:
        conn.execute("ALTER TABLE cases ADD COLUMN scope_state TEXT NOT NULL DEFAULT 'active'")
    if "scope_reason" not in case_columns:
        conn.execute("ALTER TABLE cases ADD COLUMN scope_reason TEXT")
    if "scope_actor_id" not in case_columns:
        conn.execute("ALTER TABLE cases ADD COLUMN scope_actor_id INTEGER REFERENCES users(id)")
    if "scope_at" not in case_columns:
        conn.execute("ALTER TABLE cases ADD COLUMN scope_at TEXT")

    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 0:
        demo_users = [
            ("Avery Admin", "admin", "AA", "Administrator"),
            ("Morgan Manager", "manager", "MM", "Manager"),
            ("Alex Analyst", "analyst", "AN", "Analyst"),
        ]
        for name, username, initials, role in demo_users:
            conn.execute(
                "INSERT INTO users (name, username, initials, role, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, username, initials, role, generate_password_hash("demo1234")),
            )

    # First start, or first start after upgrading: make the first project type.
    # An existing master workflow becomes that type's tasks.
    type_count = conn.execute("SELECT COUNT(*) FROM project_types").fetchone()[0]
    if type_count == 0:
        type_id = conn.execute(
            "INSERT INTO project_types (name, position) VALUES (?, 1)",
            (DEFAULT_TYPE_NAME,),
        ).lastrowid
        old = conn.execute(
            "SELECT name, high_priority FROM template_stages "
            "WHERE active = 1 ORDER BY position"
        ).fetchall()
        tasks = [(r["name"], r["high_priority"]) for r in old] or DEFAULT_STAGES
        for position, (name, high_priority) in enumerate(tasks, start=1):
            conn.execute(
                "INSERT INTO type_tasks (type_id, name, position, high_priority) "
                "VALUES (?, ?, ?, ?)",
                (type_id, name, position, high_priority),
            )

    # Projects made before project types existed belong to the first type.
    first_type = conn.execute(
        "SELECT id FROM project_types ORDER BY position, id LIMIT 1"
    ).fetchone()
    if first_type is not None:
        conn.execute(
            "UPDATE cases SET type_id = ? WHERE type_id IS NULL", (first_type["id"],)
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database ready.")