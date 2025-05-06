# app.py
import datetime
import os

import duckdb
import pytz
from database import get_db_connection, init_db, now_utc
from dotenv import load_dotenv
from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from scheduler import schedule_api_fetch, start_scheduler, stop_scheduler

load_dotenv()  # For MOCK_API_URL

APP_SECRET_KEY = os.urandom(24)
MOCK_API_URL = os.getenv(
    "MOCK_API_URL", "http://localhost:5001/api/jobs"
)  # Default if not in .env

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY
app.config["MOCK_API_URL"] = MOCK_API_URL

# Global variable to store last refresh time for display
last_refresh_time_g = None


def get_current_time_str():
    return datetime.datetime.now(pytz.timezone("UTC")).strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )


@app.before_request
def before_request():
    g.db = get_db_connection()


@app.teardown_request
def teardown_request(exception):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


# --- Helper Functions ---
def get_engineers():
    results = g.db.execute(
        "SELECT id, name, on_call_level FROM engineers ORDER BY on_call_level, name"
    ).fetchall()
    return [
        {"id": row[0], "name": row[1], "on_call_level": row[2]}
        for row in results
    ]


def get_active_incidents():
    # Active incidents are those not yet resolved
    # Sorted by status (Critical > Error > Warning), then by first_detected_at (oldest first)
    conn = g.db
    query = """
    SELECT 
        i.id,
        i.job_api_id,
        i.job_name,
        i.status,
        i.priority,
        i.log_url,
        i.first_detected_at,
        i.responded_at,
        i.resolved_at,
        i.responding_engineer_id,
        e.name as responding_engineer_name,
        i.inc_number,
        i.inc_link,
        i.last_api_update
    FROM incidents i
    LEFT JOIN engineers e ON i.responding_engineer_id = e.id
    WHERE i.resolved_at IS NULL
    ORDER BY 
        CASE i.status 
            WHEN 'CRITICAL' THEN 1
            WHEN 'ERROR' THEN 2
            WHEN 'WARNING' THEN 3
            ELSE 4
        END,
        i.first_detected_at ASC;
    """
    results = conn.execute(query).fetchall()
    # Convert tuples to dictionaries
    return [
        {
            "id": row[0],
            "job_api_id": row[1],
            "job_name": row[2],
            "status": row[3],
            "priority": row[4],
            "log_url": row[5],
            "first_detected_at": row[6],
            "responded_at": row[7],
            "resolved_at": row[8],
            "responding_engineer_id": row[9],
            "responding_engineer_name": row[10],
            "inc_number": row[11],
            "inc_link": row[12],
            "last_api_update": row[13],
        }
        for row in results
    ]


def get_stats_for_week():
    conn = g.db
    # Monday of the current week
    today = now_utc().date()
    start_of_week = today - datetime.timedelta(days=today.weekday())
    end_of_week = start_of_week + datetime.timedelta(days=6)  # Sunday end

    # Incidents per day (Mon-Fri for display, but data can be whole week)
    # For display, we care about Mon-Fri, but let's query the whole week for completeness
    incidents_by_day_q = f"""
    SELECT strftime(first_detected_at, '%Y-%m-%d') as day, COUNT(*) as count
    FROM incidents
    WHERE date_trunc('day', first_detected_at) >= '{start_of_week}' AND date_trunc('day', first_detected_at) <= '{end_of_week}'
    GROUP BY day ORDER BY day;
    """
    incidents_by_day_raw = conn.execute(incidents_by_day_q).fetchall()

    # Initialize counts for Mon-Sun
    days_of_week = [
        (start_of_week + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(7)
    ]
    daily_counts = {day: 0 for day in days_of_week}
    for day_str, count in incidents_by_day_raw:
        daily_counts[day_str] = count

    # Filter for Mon-Fri for the chart specifically
    # chart_labels = [(start_of_week + datetime.timedelta(days=i)).strftime('%a') for i in range(5)] # Mon-Fri
    # chart_data = [daily_counts.get((start_of_week + datetime.timedelta(days=i)).strftime('%Y-%m-%d'), 0) for i in range(5)]

    # Let's do it more robustly to handle missing days from query
    chart_labels = []
    chart_data = []
    for i in range(5):  # Mon to Fri
        day_date = start_of_week + datetime.timedelta(days=i)
        chart_labels.append(day_date.strftime("%a"))  # Mon, Tue, etc.
        chart_data.append(daily_counts.get(day_date.strftime("%Y-%m-%d"), 0))

    total_this_week = conn.execute(
        f"SELECT COUNT(*) FROM incidents WHERE first_detected_at >= '{start_of_week}' AND first_detected_at <= '{end_of_week}'"
    ).fetchone()[0]

    resolved_this_week = conn.execute(
        f"SELECT COUNT(*) FROM incidents WHERE resolved_at >= '{start_of_week}' AND resolved_at <= '{end_of_week}'"
    ).fetchone()[0]

    # Average times (only for incidents that HAVE been responded/resolved)
    avg_respond_time_seconds_q = f"""
    SELECT AVG(EXTRACT(EPOCH FROM (responded_at - first_detected_at)))
    FROM incidents
    WHERE responded_at IS NOT NULL AND first_detected_at IS NOT NULL
      AND first_detected_at >= '{start_of_week}' AND first_detected_at <= '{end_of_week}';
    """
    avg_respond_time_seconds = conn.execute(
        avg_respond_time_seconds_q
    ).fetchone()[0]

    avg_resolve_time_seconds_q = f"""
    SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - first_detected_at)))
    FROM incidents
    WHERE resolved_at IS NOT NULL AND first_detected_at IS NOT NULL
      AND first_detected_at >= '{start_of_week}' AND first_detected_at <= '{end_of_week}';
    """
    avg_resolve_time_seconds = conn.execute(
        avg_resolve_time_seconds_q
    ).fetchone()[0]

    return {
        "daily_counts_labels": chart_labels,
        "daily_counts_data": chart_data,
        "total_this_week": total_this_week or 0,
        "resolved_this_week": resolved_this_week or 0,
        "avg_respond_time_seconds": avg_respond_time_seconds
        if avg_respond_time_seconds is not None
        else 0,
        "avg_resolve_time_seconds": avg_resolve_time_seconds
        if avg_resolve_time_seconds is not None
        else 0,
    }


def get_recent_resolved_incidents():
    conn = g.db
    today = now_utc().date()
    start_of_week = today - datetime.timedelta(days=today.weekday())
    query = f"""
    SELECT i.job_name, i.status, i.priority, i.first_detected_at, i.responded_at, i.resolved_at, e.name as responding_engineer_name, i.inc_number
    FROM incidents i
    LEFT JOIN engineers e ON i.responding_engineer_id = e.id
    WHERE i.resolved_at IS NOT NULL AND i.resolved_at >= '{start_of_week}'
    ORDER BY i.resolved_at DESC
    LIMIT 20;
    """  # Limit to recent 20 for brevity
    results = conn.execute(query).fetchall()
    # Convert tuples to dictionaries
    return [
        {
            "job_name": row[0],
            "status": row[1],
            "priority": row[2],
            "first_detected_at": row[3],
            "responded_at": row[4],
            "resolved_at": row[5],
            "responding_engineer_name": row[6],
            "inc_number": row[7],
        }
        for row in results
    ]


# --- Routes ---
@app.route("/")
def index():
    global last_refresh_time_g
    if "last_refresh_time" not in session or last_refresh_time_g is None:
        session["last_refresh_time"] = "Never"  # Initial state
        last_refresh_time_g = "Never"

    all_active_incidents = get_active_incidents()
    pending_incidents = [
        inc
        for inc in all_active_incidents
        if inc["responding_engineer_name"] is None
    ]
    wip_incidents = [
        inc
        for inc in all_active_incidents
        if inc["responding_engineer_name"] is not None
    ]

    issue_count = len(all_active_incidents)
    responded_count = len(wip_incidents)
    pending_count = len(pending_incidents)

    engineers = get_engineers()
    stats = get_stats_for_week()
    recent_resolved = get_recent_resolved_incidents()

    return render_template(
        "index.html",
        current_time_utc=get_current_time_str(),
        last_refresh_time=last_refresh_time_g,
        issue_count=issue_count,
        responded_count=responded_count,
        pending_count=pending_count,
        engineers=engineers,
        pending_incidents=pending_incidents,
        wip_incidents=wip_incidents,
        stats=stats,
        recent_resolved=recent_resolved,
        priorities=["P1", "P2", "P3", "P4"],
        now_utc_timestamp=now_utc().timestamp(),  # For JS timer calculations
    )


@app.route("/refresh-data", methods=["POST"])
def refresh_data():
    global last_refresh_time_g
    # This endpoint could potentially trigger the scheduler to run immediately
    # For now, it just updates the 'last_refresh_time' for display
    # The actual data refresh happens via the background scheduler
    print("Manual refresh triggered by user.")
    # You could force an immediate fetch here if desired:
    # from scheduler import fetch_and_update_job_statuses # careful with imports
    # fetch_and_update_job_statuses(app)
    # But for simplicity, we assume scheduler is running.
    last_refresh_time_g = get_current_time_str()
    session["last_refresh_time"] = last_refresh_time_g
    return redirect(url_for("index"))


@app.route("/add-engineer", methods=["POST"])
def add_engineer():
    name = request.form.get("name")
    level = request.form.get("level")
    if name and level:
        try:
            g.db.execute(
                "INSERT INTO engineers (name, on_call_level) VALUES (?, ?)",
                (name, level),
            )
            g.db.commit()
        except duckdb.IntegrityError:  # Handles unique constraint for name
            print(
                f"Engineer {name} already exists."
            )  # Add flash message for user
        except Exception as e:
            print(f"Error adding engineer: {e}")
    return redirect(url_for("index"))


@app.route("/delete-engineer/<int:engineer_id>", methods=["POST"])
def delete_engineer(engineer_id):
    # Consider what happens if engineer is assigned to an incident.
    # For now, we'll allow deletion. Could add a check or set assigned incidents to NULL.
    g.db.execute(
        "UPDATE incidents SET responding_engineer_id = NULL WHERE responding_engineer_id = ?",
        (engineer_id,),
    )
    g.db.execute("DELETE FROM engineers WHERE id = ?", (engineer_id,))
    g.db.commit()
    return redirect(url_for("index"))


@app.route("/respond-incident/<int:incident_id>", methods=["POST"])
def respond_incident(incident_id):
    engineer_id = request.form.get("engineer_id")
    priority = request.form.get("priority")
    inc_number = request.form.get("inc_number", None)  # Optional
    inc_link = request.form.get("inc_link", None)  # Optional

    if engineer_id and priority:
        g.db.execute(
            """
            UPDATE incidents
            SET responding_engineer_id = ?, priority = ?, responded_at = ?, inc_number = ?, inc_link = ?
            WHERE id = ? AND responded_at IS NULL
        """,
            (
                engineer_id,
                priority,
                now_utc(),
                inc_number,
                inc_link,
                incident_id,
            ),
        )
        g.db.commit()
    return redirect(url_for("index"))


@app.route("/update-incident-priority/<int:incident_id>", methods=["POST"])
def update_incident_priority(incident_id):
    priority = request.form.get("priority")
    if priority:
        g.db.execute(
            "UPDATE incidents SET priority = ? WHERE id = ?",
            (priority, incident_id),
        )
        g.db.commit()
    return redirect(url_for("index"))


@app.route("/update-inc-link/<int:incident_id>", methods=["POST"])
def update_inc_link(incident_id):
    inc_number = request.form.get("inc_number")
    inc_link = request.form.get("inc_link")
    if inc_number or inc_link:  # Allow updating one or both
        updates = []
        params = []
        if inc_number:
            updates.append("inc_number = ?")
            params.append(inc_number)
        if inc_link:
            updates.append("inc_link = ?")
            params.append(inc_link)
        params.append(incident_id)

        if updates:
            query = f"UPDATE incidents SET {', '.join(updates)} WHERE id = ?"
            g.db.execute(query, tuple(params))
            g.db.commit()
    return redirect(url_for("index"))


@app.route("/resolve-incident/<int:incident_id>", methods=["POST"])
def resolve_incident(incident_id):
    # This is a manual resolve button.
    # If an incident is not picked up by the API as "OK" or gone,
    # SREs can manually resolve it.
    g.db.execute(
        "UPDATE incidents SET resolved_at = ? WHERE id = ?",
        (now_utc(), incident_id),
    )
    g.db.commit()
    return redirect(url_for("index"))


# --- Scheduler Setup ---
if __name__ == "__main__":
    init_db()  # Ensure DB is initialized
    # Start scheduler in a separate thread
    # Pass the app context or necessary components if scheduler needs them directly
    # For simplicity, scheduler.py will import app and use its context
    start_scheduler(app)
    try:
        app.run(
            debug=True, use_reloader=False
        )  # use_reloader=False is important when using APScheduler
    finally:
        stop_scheduler()
