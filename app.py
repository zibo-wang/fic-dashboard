"""Flask application for the FIC Dashboard.

This module implements a Flask web application that provides a dashboard for monitoring
and managing incidents. It includes features for:
- Displaying active and resolved incidents
- Managing incident responses
- Tracking incident statistics
- Auto-refreshing data from external APIs

The application uses DuckDB for data storage and APScheduler for background tasks.
"""

import datetime
import os
from functools import wraps

import duckdb
import pytz
from database import (
    authenticate_user,
    get_db_connection,
    get_user_by_id,
    init_db,
    now_utc,
)
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from scheduler import (
    api_error_state,
    fetch_and_update_job_statuses,
    start_scheduler,
    stop_scheduler,
)

load_dotenv()  # For MOCK_API_URL

APP_SECRET_KEY = os.urandom(24)
MOCK_API_URL = os.getenv(
    "MOCK_API_URL", "http://localhost:5001/api/jobs"
)  # Default if not in .env

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY
app.config["MOCK_API_URL"] = MOCK_API_URL
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(
    hours=8
)  # 8-hour session timeout


def login_required(f):
    """Decorator to require login for protected routes."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        # Check if session is still valid
        user = get_user_by_id(session["user_id"])
        if not user:
            session.clear()
            flash("Session expired. Please log in again.", "warning")
            return redirect(url_for("login"))

        # Update user info in g for use in templates
        g.current_user = user
        return f(*args, **kwargs)

    return decorated_function


def check_session_timeout():
    """Check if session has timed out."""
    if "user_id" in session:
        # Check if session is permanent and if it has expired
        if not session.permanent:
            session.permanent = True

        # Verify user still exists and is active
        user = get_user_by_id(session["user_id"])
        if not user:
            session.clear()
            return False

        g.current_user = user
        return True
    return False


def get_current_time_str():
    """Gets the current time in Sydney timezone as a formatted string.

    Returns:
        str: Current time in format 'HH:MM:SS'
    """
    sydney_tz = pytz.timezone("Australia/Sydney")
    return datetime.datetime.now(sydney_tz).strftime("%H:%M:%S")


@app.before_request
def before_request():
    """Sets up database connection and checks session before each request."""
    g.db = get_db_connection()

    # Skip session check for login/logout routes and static files
    if request.endpoint in ["login", "logout", "static"]:
        return

    # Check session timeout for all other routes
    check_session_timeout()


@app.teardown_request
def teardown_request(exception):
    """Closes database connection after each request."""
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


# --- Helper Functions ---
def get_engineers():
    """Retrieves all engineers from the database.

    Returns:
        list: List of dictionaries containing engineer information (id, name, on_call_level)
    """
    results = g.db.execute(
        "SELECT id, name, on_call_level FROM engineers ORDER BY on_call_level, name"
    ).fetchall()
    return [
        {"id": row[0], "name": row[1], "on_call_level": row[2]}
        for row in results
    ]


def get_active_incidents():
    """Retrieves all active (unresolved) incidents from the database.

    Returns:
        list: List of dictionaries containing incident information, sorted by status
        (Critical > Error > Warning) and then by detection time (oldest first)
    """
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
    """Calculates incident statistics for the current week.

    Returns:
        dict: Dictionary containing:
            - daily_counts_labels: List of day labels (Mon-Fri)
            - daily_counts_data: List of incident counts per day
            - total_this_week: Total incidents this week
            - resolved_this_week: Total resolved incidents this week
            - avg_respond_time_seconds: Average response time
            - avg_resolve_time_seconds: Average resolution time
    """
    conn = g.db
    # Monday of the current week
    today = now_utc().date()
    start_of_week = today - datetime.timedelta(days=today.weekday())
    end_of_week = start_of_week + datetime.timedelta(days=6)  # Sunday end

    # Incidents per day (Mon-Fri for display, but data can be whole week)
    incidents_by_day_q = f"""
    SELECT strftime(first_detected_at, '%Y-%m-%d') as day, COUNT(*) as count
    FROM incidents
    WHERE DATE(first_detected_at) >= '{start_of_week}' AND DATE(first_detected_at) <= '{end_of_week}'
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

    # Filter for Mon-Fri for the chart
    chart_labels = []
    chart_data = []
    for i in range(5):  # Mon to Fri
        day_date = start_of_week + datetime.timedelta(days=i)
        chart_labels.append(day_date.strftime("%a"))  # Mon, Tue, etc.
        chart_data.append(daily_counts.get(day_date.strftime("%Y-%m-%d"), 0))

    total_this_week = conn.execute(
        f"SELECT COUNT(*) FROM incidents WHERE DATE(first_detected_at) >= '{start_of_week}' AND DATE(first_detected_at) <= '{end_of_week}'"
    ).fetchone()[0]

    resolved_this_week = conn.execute(
        f"SELECT COUNT(*) FROM incidents WHERE DATE(resolved_at) >= '{start_of_week}' AND DATE(resolved_at) <= '{end_of_week}'"
    ).fetchone()[0]

    # Average times (only for incidents that HAVE been responded/resolved)
    avg_respond_time_seconds_q = f"""
    SELECT AVG(EXTRACT(EPOCH FROM (responded_at - first_detected_at)))
    FROM incidents
    WHERE responded_at IS NOT NULL AND first_detected_at IS NOT NULL
      AND DATE(first_detected_at) >= '{start_of_week}' AND DATE(first_detected_at) <= '{end_of_week}';
    """
    avg_respond_time_seconds = conn.execute(
        avg_respond_time_seconds_q
    ).fetchone()[0]

    avg_resolve_time_seconds_q = f"""
    SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - responded_at)))
    FROM incidents
    WHERE resolved_at IS NOT NULL AND responded_at IS NOT NULL
      AND DATE(first_detected_at) >= '{start_of_week}' AND DATE(first_detected_at) <= '{end_of_week}';
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
    """Retrieves recently resolved incidents from the database.

    Returns:
        list: List of dictionaries containing information about recently resolved incidents,
        limited to the 20 most recent ones.
    """
    conn = g.db
    today = now_utc().date()
    start_of_week = today - datetime.timedelta(days=today.weekday())
    query = f"""
    SELECT i.job_name, i.status, i.priority, i.first_detected_at, i.responded_at, i.resolved_at, e.name as responding_engineer_name, i.inc_number, i.resolve_notes
    FROM incidents i
    LEFT JOIN engineers e ON i.responding_engineer_id = e.id
    WHERE i.resolved_at IS NOT NULL AND i.resolved_at >= '{start_of_week}'
    ORDER BY i.resolved_at DESC
    LIMIT 20;
    """
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
            "resolve_notes": row[8],
        }
        for row in results
    ]


def get_last_refresh_time():
    """Gets the last refresh time from the database.

    Returns:
        str: Formatted last refresh time in Sydney timezone or "Never"
    """
    conn = get_db_connection()
    try:
        # Ensure the table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key VARCHAR PRIMARY KEY,
                value TIMESTAMPTZ
            );
        """)

        result = conn.execute(
            "SELECT value FROM app_state WHERE key = 'last_refresh_time'"
        ).fetchone()

        if result and result[0]:
            # Convert to Sydney timezone for display
            sydney_tz = pytz.timezone("Australia/Sydney")
            last_refresh_utc = result[0]
            last_refresh_sydney = last_refresh_utc.astimezone(sydney_tz)
            return last_refresh_sydney.strftime("%H:%M:%S")
        return "Never"
    except Exception as e:
        print(f"Error getting last refresh time: {e}")
        return "Error"
    finally:
        conn.close()


def update_last_refresh_time():
    """Updates the last refresh time in the database to current time."""
    current_time = now_utc()
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key VARCHAR PRIMARY KEY,
                value TIMESTAMPTZ
            );
            """
        )
        conn.execute(
            """
            INSERT INTO app_state (key, value) 
            VALUES ('last_refresh_time', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            (current_time,),
        )
        conn.commit()
    except Exception as e:
        print(f"Error updating last refresh time: {e}")
    finally:
        conn.close()


# --- Routes ---
@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page and authentication."""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username and password:
            user = authenticate_user(username, password)
            if user:
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session.permanent = True
                flash(f"Welcome, {user['full_name']}!", "success")
                return redirect(url_for("index"))
            else:
                flash("Invalid username or password.", "error")
        else:
            flash("Please enter both username and password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Logout and clear session."""
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    """Main dashboard page.

    Returns:
        str: Rendered HTML template with dashboard data
    """
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
    last_refresh_time = get_last_refresh_time()

    return render_template(
        "index.html",
        current_time_utc=get_current_time_str(),
        last_refresh_time=last_refresh_time,
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
        current_user=g.current_user,
    )


@app.route("/get-last-refresh-time")
@login_required
def get_last_refresh_time_endpoint():
    """API endpoint to get the last refresh time from the database."""
    last_refresh = get_last_refresh_time()
    return jsonify({"last_refresh_time": last_refresh})


@app.route("/get-incident-count")
@login_required
def get_incident_count():
    """API endpoint to get the current count of active incidents."""
    conn = get_db_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE resolved_at IS NULL"
        ).fetchone()[0]
        return jsonify({"count": count})
    except Exception as e:
        return jsonify({"count": 0, "error": str(e)})
    finally:
        conn.close()


@app.route("/get-stats")
@login_required
def get_stats():
    """API endpoint to get updated dashboard statistics."""
    try:
        # Get current incident counts
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

        # Get weekly stats
        stats = get_stats_for_week()

        return jsonify(
            {
                "issue_count": issue_count,
                "responded_count": responded_count,
                "pending_count": pending_count,
                "stats": stats,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/refresh-data", methods=["POST"])
@login_required
def refresh_data():
    """Manually triggers a data refresh.

    Returns:
        Response: Redirect to the index page
    """
    # Trigger immediate refresh
    fetch_and_update_job_statuses(app)
    # Update the refresh time
    update_last_refresh_time()

    return redirect(url_for("index"))


@app.route("/add-incident", methods=["POST"])
@login_required
def add_incident():
    """Adds a new incident to the database.

    Returns:
        Response: Redirect to the index page
    """
    job_name = request.form.get("job_name")
    status = request.form.get("status")
    priority = request.form.get("priority")
    log_url = request.form.get("log_url", None)  # Optional
    notes = request.form.get("notes", None)  # Optional

    if job_name and status and priority:
        try:
            current_time = now_utc()
            g.db.execute(
                """
                INSERT INTO incidents (
                    job_name, status, priority, log_url, notes,
                    first_detected_at, last_api_update
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_name,
                    status,
                    priority,
                    log_url,
                    notes,
                    current_time,
                    current_time,
                ),
            )
            g.db.commit()
            flash(f"Incident '{job_name}' added successfully.", "success")
        except Exception as e:
            flash(f"Error adding incident: {e}", "error")
    else:
        flash("Job name, status, and priority are required.", "error")
    return redirect(url_for("index"))


@app.route("/add-engineer", methods=["POST"])
@login_required
def add_engineer():
    """Adds a new engineer to the database.

    Returns:
        Response: Redirect to the index page
    """
    name = request.form.get("name")
    level = request.form.get("level")
    if name and level:
        try:
            g.db.execute(
                "INSERT INTO engineers (name, on_call_level) VALUES (?, ?)",
                (name, level),
            )
            g.db.commit()
            flash(f"Engineer {name} added successfully.", "success")
        except duckdb.IntegrityError:  # Handles unique constraint for name
            flash(f"Engineer {name} already exists.", "error")
        except Exception as e:
            flash(f"Error adding engineer: {e}", "error")
    return redirect(url_for("index"))


@app.route("/delete-engineer/<int:engineer_id>", methods=["POST"])
@login_required
def delete_engineer(engineer_id):
    """Deletes an engineer from the database.

    Args:
        engineer_id (int): ID of the engineer to delete

    Returns:
        Response: Redirect to the index page
    """
    # Consider what happens if engineer is assigned to an incident.
    # For now, we'll allow deletion. Could add a check or set assigned incidents to NULL.
    g.db.execute(
        "UPDATE incidents SET responding_engineer_id = NULL WHERE responding_engineer_id = ?",
        (engineer_id,),
    )
    g.db.execute("DELETE FROM engineers WHERE id = ?", (engineer_id,))
    g.db.commit()
    flash("Engineer deleted successfully.", "success")
    return redirect(url_for("index"))


@app.route("/respond-incident/<int:incident_id>", methods=["POST"])
@login_required
def respond_incident(incident_id):
    """Updates an incident with response information.

    Args:
        incident_id (int): ID of the incident to update

    Returns:
        Response: Redirect to the index page
    """
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
        flash("Incident response recorded successfully.", "success")
    return redirect(url_for("index"))


@app.route("/update-incident-priority/<int:incident_id>", methods=["POST"])
@login_required
def update_incident_priority(incident_id):
    """Updates the priority of an incident.

    Args:
        incident_id (int): ID of the incident to update

    Returns:
        Response: Redirect to the index page
    """
    priority = request.form.get("priority")
    if priority:
        g.db.execute(
            "UPDATE incidents SET priority = ? WHERE id = ?",
            (priority, incident_id),
        )
        g.db.commit()
        flash("Incident priority updated successfully.", "success")
    return redirect(url_for("index"))


@app.route("/update-inc-link/<int:incident_id>", methods=["POST"])
@login_required
def update_inc_link(incident_id):
    """Updates the incident number and/or link for an incident.

    Args:
        incident_id (int): ID of the incident to update

    Returns:
        Response: Redirect to the index page
    """
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
            flash("Incident link updated successfully.", "success")
    return redirect(url_for("index"))


@app.route("/resolve-incident/<int:incident_id>", methods=["POST"])
@login_required
def resolve_incident(incident_id):
    """Marks an incident as resolved with structured resolution notes.

    Args:
        incident_id (int): ID of the incident to resolve

    Returns:
        Response: Redirect to the index page
    """
    reason = request.form.get("reason")
    action_taken = request.form.get("action_taken")
    pending_actions = request.form.get("pending_actions", "")

    # Validate required fields
    if not reason or not reason.strip():
        flash("Root cause/reason is required to resolve an incident.", "error")
        return redirect(url_for("index"))

    if not action_taken or not action_taken.strip():
        flash("Action taken is required to resolve an incident.", "error")
        return redirect(url_for("index"))

    # Format structured resolution notes
    resolve_notes = f"**Root Cause/Reason:**\n{reason.strip()}\n\n"
    resolve_notes += f"**Action Taken:**\n{action_taken.strip()}"

    if pending_actions and pending_actions.strip():
        resolve_notes += f"\n\n**Pending Actions:**\n{pending_actions.strip()}"

    # This is a manual resolve button.
    # If an incident is not picked up by the API as "OK" or gone,
    # SREs can manually resolve it.
    g.db.execute(
        "UPDATE incidents SET resolved_at = ?, resolve_notes = ? WHERE id = ?",
        (now_utc(), resolve_notes, incident_id),
    )
    g.db.commit()
    flash("Incident resolved successfully.", "success")
    return redirect(url_for("index"))


@app.route("/get-api-error-state")
@login_required
def get_api_error_state():
    """Returns the current API error state."""
    return jsonify(
        {
            "has_error": api_error_state["has_error"],
            "last_error": api_error_state["last_error"],
            "last_error_time": api_error_state["last_error_time"].isoformat()
            if api_error_state["last_error_time"]
            else None,
        }
    )


@app.route("/debug-status")
@login_required
def debug_status():
    """Debug endpoint to check system status."""
    try:
        # Get last refresh time
        last_refresh = get_last_refresh_time()

        # Get incident counts
        total_incidents = g.db.execute(
            "SELECT COUNT(*) FROM incidents"
        ).fetchone()[0]

        active_incidents = g.db.execute(
            "SELECT COUNT(*) FROM incidents WHERE resolved_at IS NULL"
        ).fetchone()[0]

        # Get last API update times
        last_updates = g.db.execute(
            "SELECT MAX(last_api_update) FROM incidents"
        ).fetchone()[0]

        return jsonify(
            {
                "last_refresh_time": last_refresh,
                "total_incidents": total_incidents,
                "active_incidents": active_incidents,
                "last_incident_update": last_updates.isoformat()
                if last_updates
                else "Never",
                "current_time": now_utc().isoformat(),
                "api_error_state": api_error_state,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Scheduler Setup ---
if __name__ == "__main__":
    init_db()  # Ensure DB is initialized
    # Start scheduler in a separate thread
    start_scheduler(app)
    try:
        app.run(
            debug=True, use_reloader=False, port=5050
        )  # use_reloader=False is important when using APScheduler
    finally:
        stop_scheduler()
