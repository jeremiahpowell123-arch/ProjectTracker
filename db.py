import sqlite3
from werkzeug.security import generate_password_hash

DB_PATH = "tracker.db"

# The default master workflow: (stage name, high priority?)
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
    password_hash TEXT NOT NULL
);

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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)

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

    stage_count = conn.execute("SELECT COUNT(*) FROM template_stages").fetchone()[0]
    if stage_count == 0:
        for position, (name, high_priority) in enumerate(DEFAULT_STAGES, start=1):
            conn.execute(
                "INSERT INTO template_stages (name, position, high_priority) "
                "VALUES (?, ?, ?)",
                (name, position, high_priority),
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database ready.")