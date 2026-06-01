import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover
    psycopg = None
    dict_row = None

IMEI_RE = re.compile(r"^\d{15}$")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_url():
    return os.environ.get("DATABASE_URL", "").strip()


def using_postgres():
    return db_url().startswith(("postgres://", "postgresql://"))


def connect():
    if using_postgres():
        if psycopg is None:
            raise RuntimeError("psycopg nao instalado")
        return psycopg.connect(db_url(), row_factory=dict_row)
    conn = sqlite3.connect(os.environ.get("SQLITE_PATH", "imei_control.sqlite3"))
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def execute(conn, sql, params=None):
    params = params or []
    if using_postgres():
        sql = sql.replace("?", "%s")
    cur = conn.execute(sql, params)
    return cur


def init_db():
    with connect() as conn:
        if using_postgres():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS locations (
                  id SERIAL PRIMARY KEY,
                  code TEXT NOT NULL UNIQUE,
                  name TEXT NOT NULL,
                  folder TEXT,
                  last_seen TIMESTAMPTZ,
                  created_at TIMESTAMPTZ DEFAULT NOW(),
                  updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS imeis (
                  id SERIAL PRIMARY KEY,
                  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                  imei TEXT NOT NULL,
                  file_name TEXT,
                  file_path TEXT,
                  modified_at TIMESTAMPTZ,
                  detected_at TIMESTAMPTZ DEFAULT NOW(),
                  updated_at TIMESTAMPTZ DEFAULT NOW(),
                  UNIQUE(location_id, imei)
                );
                CREATE INDEX IF NOT EXISTS idx_imeis_imei ON imeis(imei);
                CREATE INDEX IF NOT EXISTS idx_imeis_location ON imeis(location_id);
                CREATE INDEX IF NOT EXISTS idx_imeis_updated ON imeis(updated_at);
                """
            )
        else:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS locations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  code TEXT NOT NULL UNIQUE,
                  name TEXT NOT NULL,
                  folder TEXT,
                  last_seen TEXT,
                  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS imeis (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                  imei TEXT NOT NULL,
                  file_name TEXT,
                  file_path TEXT,
                  modified_at TEXT,
                  detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(location_id, imei)
                );
                CREATE INDEX IF NOT EXISTS idx_imeis_imei ON imeis(imei);
                CREATE INDEX IF NOT EXISTS idx_imeis_location ON imeis(location_id);
                CREATE INDEX IF NOT EXISTS idx_imeis_updated ON imeis(updated_at);
                """
            )
        conn.commit()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def api_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"error": "login required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def ingest_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = os.environ.get("INGEST_TOKEN", "")
        if not expected:
            return jsonify({"error": "INGEST_TOKEN not configured"}), 503
        received = request.headers.get("X-Ingest-Token") or request.headers.get("Authorization", "").replace("Bearer ", "")
        if received != expected:
            return jsonify({"error": "invalid ingest token"}), 401
        return fn(*args, **kwargs)
    return wrapper


def upsert_location(conn, code, name, folder):
    current = now_iso()
    if using_postgres():
        row = conn.execute(
            """
            INSERT INTO locations (code, name, folder, last_seen, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (code) DO UPDATE SET
              name=EXCLUDED.name,
              folder=COALESCE(EXCLUDED.folder, locations.folder),
              last_seen=NOW(),
              updated_at=NOW()
            RETURNING *
            """,
            [code, name, folder],
        ).fetchone()
        return dict(row)
    execute(
        conn,
        """
        INSERT INTO locations (code, name, folder, last_seen, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
          name=excluded.name,
          folder=COALESCE(excluded.folder, locations.folder),
          last_seen=excluded.last_seen,
          updated_at=excluded.updated_at
        """,
        [code, name, folder, current, current],
    )
    row = execute(conn, "SELECT * FROM locations WHERE code=?", [code]).fetchone()
    return dict(row)


@app.get("/")
def index():
    if not session.get("admin"):
        return redirect(url_for("login"))
    return render_template("dashboard.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == os.environ.get("ADMIN_PASSWORD", "123456"):
            session["admin"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Senha invalida")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/summary")
@api_login_required
def api_summary():
    with connect() as conn:
        locations = rows_to_dicts(execute(conn, """
            SELECT l.*, COUNT(i.id) AS total_imeis, MAX(i.updated_at) AS last_record
            FROM locations l
            LEFT JOIN imeis i ON i.location_id = l.id
            GROUP BY l.id
            ORDER BY l.name
        """).fetchall())
        total_row = execute(conn, "SELECT COUNT(*) AS total, COUNT(DISTINCT imei) AS unique_total, MAX(updated_at) AS last_record FROM imeis").fetchone()
        return jsonify({
            "locations": locations,
            "total": int(total_row["total"] or 0),
            "unique_total": int(total_row["unique_total"] or 0),
            "last_record": total_row["last_record"],
        })


@app.get("/api/imeis")
@api_login_required
def api_imeis():
    imei = re.sub(r"\D", "", request.args.get("imei", ""))
    location = request.args.get("location", "")
    params = []
    where = []
    if imei:
        where.append("i.imei LIKE ?")
        params.append(f"%{imei}%")
    if location:
        where.append("l.code = ?")
        params.append(location)
    sql = """
        SELECT i.*, l.code AS location_code, l.name AS location_name, l.folder AS location_folder
        FROM imeis i
        JOIN locations l ON l.id = i.location_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY i.updated_at DESC LIMIT 500"
    with connect() as conn:
        rows = rows_to_dicts(execute(conn, sql, params).fetchall())
    return jsonify({"records": rows, "total": len(rows)})


@app.post("/api/ingest")
@ingest_required
def api_ingest():
    data = request.get_json(silent=True) or {}
    code = str(data.get("location_code") or "").strip()
    name = str(data.get("location_name") or code).strip()
    folder = data.get("folder")
    records = data.get("records") or []
    if not code:
        return jsonify({"error": "location_code required"}), 400
    if not isinstance(records, list):
        return jsonify({"error": "records must be a list"}), 400

    saved = 0
    with connect() as conn:
        location = upsert_location(conn, code, name, folder)
        for record in records:
            imei = re.sub(r"\D", "", str(record.get("imei", "")))
            if not IMEI_RE.match(imei):
                continue
            if using_postgres():
                conn.execute(
                    """
                    INSERT INTO imeis (location_id, imei, file_name, file_path, modified_at, detected_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,NOW(),NOW())
                    ON CONFLICT (location_id, imei) DO UPDATE SET
                      file_name=COALESCE(EXCLUDED.file_name, imeis.file_name),
                      file_path=COALESCE(EXCLUDED.file_path, imeis.file_path),
                      modified_at=COALESCE(EXCLUDED.modified_at, imeis.modified_at),
                      updated_at=NOW()
                    """,
                    [location["id"], imei, record.get("file_name"), record.get("file_path"), record.get("modified_at")],
                )
            else:
                current = now_iso()
                execute(
                    conn,
                    """
                    INSERT INTO imeis (location_id, imei, file_name, file_path, modified_at, detected_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(location_id, imei) DO UPDATE SET
                      file_name=COALESCE(excluded.file_name, imeis.file_name),
                      file_path=COALESCE(excluded.file_path, imeis.file_path),
                      modified_at=COALESCE(excluded.modified_at, imeis.modified_at),
                      updated_at=excluded.updated_at
                    """,
                    [location["id"], imei, record.get("file_name"), record.get("file_path"), record.get("modified_at"), current, current],
                )
            saved += 1
        conn.commit()
    return jsonify({"ok": True, "received": len(records), "saved": saved})


@app.get("/health")
def health():
    return jsonify({"status": "online", "database": "postgres" if using_postgres() else "sqlite"})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
