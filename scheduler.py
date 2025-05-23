"""Background scheduler for the FIC Dashboard.

This module implements a background scheduler that periodically fetches job statuses
from an external API and updates the database accordingly. It handles:
- Fetching job statuses every 30 seconds
- Creating new incidents for failed jobs
- Updating existing incidents
- Resolving incidents when jobs are fixed
- Caching API responses to reduce load

The scheduler uses APScheduler for task scheduling and httpx for HTTP requests.
"""

import logging

import httpx
from apscheduler.schedulers.background import BackgroundScheduler

from database import (
    get_db_connection,
    now_utc,
)  # Use the same now_utc for consistency

# Configure basic logging for the scheduler
logging.basicConfig(level=logging.INFO)
scheduler_logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = BackgroundScheduler(
    daemon=True
)  # daemon=True allows app to exit even if scheduler is running

# Cache for API responses (simple in-memory)
# For more robust caching, consider Flask-Caching or redis
api_data_cache = {"data": None, "last_fetched": None}
CACHE_DURATION_SECONDS = (
    25  # Slightly less than fetch interval to ensure fresh data
)

# Track API error state
api_error_state = {
    "has_error": False,
    "last_error": None,
    "last_error_time": None,
}


def update_last_refresh_time_in_db():
    """Updates the last refresh time in the database."""
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
        scheduler_logger.info(f"Updated last refresh time to {current_time}")
    except Exception as e:
        scheduler_logger.error(f"Error updating last refresh time: {e}")
        conn.rollback()
    finally:
        conn.close()


def fetch_and_update_job_statuses(flask_app):
    """Fetches job statuses from the API and updates the database.

    This function is called by the scheduler every 30 seconds. It:
    1. Fetches job statuses from the API
    2. Updates existing incidents if their status has changed
    3. Creates new incidents for failed jobs
    4. Resolves incidents when jobs are fixed
    5. Updates the last refresh time in the database

    Args:
        flask_app: The Flask application instance, used for configuration and context
    """
    global api_data_cache, api_error_state
    current_time = now_utc()

    # Check cache
    if api_data_cache["data"] and api_data_cache["last_fetched"]:
        if (
            current_time - api_data_cache["last_fetched"]
        ).total_seconds() < CACHE_DURATION_SECONDS:
            scheduler_logger.info("Using cached API data.")
            api_jobs = api_data_cache["data"]
        else:
            api_jobs = None  # Cache expired
    else:
        api_jobs = None  # No cache

    # If no cached data, fetch from API
    if not api_jobs:
        try:
            # Fetch from API
            api_url = flask_app.config.get("MOCK_API_URL")
            if not api_url:
                scheduler_logger.error("MOCK_API_URL not configured")
                api_error_state["has_error"] = True
                api_error_state["last_error"] = "API_URL not configured"
                api_error_state["last_error_time"] = current_time
                return

            with httpx.Client(timeout=10.0) as client:  # 10 second timeout
                response = client.get(api_url)
                response.raise_for_status()  # Raise exception for non-200 status codes
                api_jobs = response.json()

            # Update cache
            api_data_cache["data"] = api_jobs
            api_data_cache["last_fetched"] = current_time
            # Clear error state on successful fetch
            api_error_state["has_error"] = False
            api_error_state["last_error"] = None

        except httpx.RequestError as e:
            scheduler_logger.error(f"Failed to fetch from API: {e}")
            api_error_state["has_error"] = True
            api_error_state["last_error"] = str(e)
            api_error_state["last_error_time"] = current_time
            return
        except Exception as e:
            scheduler_logger.error(f"Unexpected error during API fetch: {e}")
            api_error_state["has_error"] = True
            api_error_state["last_error"] = str(e)
            api_error_state["last_error_time"] = current_time
            return

    if not api_jobs:
        scheduler_logger.warning("No jobs data received from API.")
        return

    with (
        flask_app.app_context()
    ):  # Need app context to use g.db or direct db connection
        conn = get_db_connection()
        try:
            # Get current active incidents from DB to compare
            # We only care about job_api_id for matching
            db_incidents_raw = conn.execute(
                "SELECT job_api_id, status, resolved_at FROM incidents WHERE resolved_at IS NULL"
            ).fetchall()
            # Convert to a dictionary for easier lookup
            db_active_incidents = {
                row[0]: {"status": row[1], "resolved_at": row[2]}
                for row in db_incidents_raw
            }

            api_job_ids_seen = set()

            for job_data in api_jobs:
                job_api_id = job_data.get("id")
                job_name = job_data.get("name")
                status = job_data.get(
                    "status"
                )  # CRITICAL, ERROR, WARNING, OK, LOG
                log_url = job_data.get("log_url")

                if not job_api_id or not job_name or not status:
                    scheduler_logger.warning(
                        f"Skipping incomplete job data: {job_data}"
                    )
                    continue

                api_job_ids_seen.add(job_api_id)

                # We only care about CRITICAL, ERROR, WARNING for creating/updating incidents
                # OK or LOG status from API means the issue is resolved or not an issue.
                if status in ["CRITICAL", "ERROR", "WARNING"]:
                    # Check if this job has an active incident
                    if job_api_id in db_active_incidents:
                        # Existing active incident, check if status changed
                        if db_active_incidents[job_api_id]["status"] != status:
                            scheduler_logger.info(
                                f"Updating status for {job_api_id} from {db_active_incidents[job_api_id]['status']} to {status}"
                            )
                            conn.execute(
                                """
                                UPDATE incidents SET status = ?, log_url = ?, last_api_update = ?
                                WHERE job_api_id = ? AND resolved_at IS NULL
                            """,
                                (status, log_url, current_time, job_api_id),
                            )
                        else:
                            # Just update the last_api_update timestamp
                            conn.execute(
                                """
                                UPDATE incidents SET log_url = ?, last_api_update = ?
                                WHERE job_api_id = ? AND resolved_at IS NULL
                            """,
                                (log_url, current_time, job_api_id),
                            )
                    else:
                        # Check if this job was previously resolved
                        prev_incident = conn.execute(
                            "SELECT id FROM incidents WHERE job_api_id = ? AND resolved_at IS NOT NULL ORDER BY resolved_at DESC LIMIT 1",
                            (job_api_id,),
                        ).fetchone()

                        if prev_incident:
                            scheduler_logger.info(
                                f"Previously resolved job {job_api_id} has failed again. Creating new incident."
                            )

                        # Create new incident (whether it's a new job or a reoccurrence)
                        scheduler_logger.info(
                            f"New incident detected: {job_api_id} - {job_name} ({status})"
                        )
                        conn.execute(
                            """
                            INSERT INTO incidents (job_api_id, job_name, status, log_url, first_detected_at, last_api_update)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """,
                            (
                                job_api_id,
                                job_name,
                                status,
                                log_url,
                                current_time,
                                current_time,
                            ),
                        )
                elif status in ["OK", "LOG"]:
                    # If an incident was active and now API says it's OK/LOG, resolve it
                    if job_api_id in db_active_incidents:
                        scheduler_logger.info(
                            f"Incident {job_api_id} resolved via API (status: {status})."
                        )
                        # For pending incidents (no responder), set responded_at to first_detected_at
                        # This ensures they appear in history with proper timing
                        conn.execute(
                            """
                            UPDATE incidents 
                            SET resolved_at = ?, 
                                status = ?, 
                                last_api_update = ?,
                                responded_at = CASE 
                                    WHEN responded_at IS NULL THEN first_detected_at 
                                    ELSE responded_at 
                                END
                            WHERE job_api_id = ? AND resolved_at IS NULL
                        """,
                            (current_time, status, current_time, job_api_id),
                        )  # Keep status as OK/LOG for historical

            # Check for incidents in DB that are no longer reported by API (implicitly resolved)
            # These are active incidents in our DB whose job_api_id was NOT in the latest API pull.
            # Ensure last_api_update is sufficiently old to avoid race conditions with flapping APIs.
            # For example, if an incident disappears for one cycle but comes back, we might prematurely resolve it.
            # A grace period or more sophisticated logic might be needed for production.
            # For now, any active incident not in `api_job_ids_seen` is considered resolved.
            grace_period_seconds = 120  # e.g., 2 minutes. If not seen by API for this long, mark as resolved.

            for job_api_id, incident_details in db_active_incidents.items():
                if job_api_id not in api_job_ids_seen:
                    # Check last_api_update time
                    last_seen_query = "SELECT last_api_update FROM incidents WHERE job_api_id = ? AND resolved_at IS NULL"
                    last_seen_row = conn.execute(
                        last_seen_query, (job_api_id,)
                    ).fetchone()
                    if last_seen_row and last_seen_row[0]:
                        time_since_last_api_update = (
                            current_time - last_seen_row[0]
                        ).total_seconds()
                        if time_since_last_api_update > grace_period_seconds:
                            scheduler_logger.info(
                                f"Incident {job_api_id} no longer in API response after grace period. Marking as resolved."
                            )
                            conn.execute(
                                """
                                UPDATE incidents SET resolved_at = ?
                                WHERE job_api_id = ? AND resolved_at IS NULL
                            """,
                                (current_time, job_api_id),
                            )
                        else:
                            scheduler_logger.info(
                                f"Incident {job_api_id} not in current API response, but within grace period ({time_since_last_api_update:.0f}s). Waiting."
                            )
                    else:  # Should not happen if it's in db_active_incidents from a valid state
                        scheduler_logger.warning(
                            f"Incident {job_api_id} has no last_api_update, but is active. Odd state."
                        )

            conn.commit()

            # Update the last refresh time after successful processing
            update_last_refresh_time_in_db()

            scheduler_logger.info("Job status update complete.")

        except Exception as e:
            conn.rollback()
            scheduler_logger.error(
                f"Error updating database: {e}", exc_info=True
            )
        finally:
            conn.close()


def schedule_api_fetch(flask_app):
    """Adds the job to the scheduler if not already scheduled.

    Args:
        flask_app: The Flask application instance
    """
    job_id = "fetch_job_statuses"
    if not scheduler.get_job(job_id):
        # Fetch initial data immediately, then schedule
        fetch_and_update_job_statuses(flask_app)
        scheduler.add_job(
            fetch_and_update_job_statuses,
            "interval",
            seconds=30,  # As per requirement
            args=[flask_app],
            id=job_id,
            replace_existing=True,
        )
        scheduler_logger.info(
            f"Scheduled API fetch job '{job_id}' to run every 30 seconds."
        )
    else:
        scheduler_logger.info(f"API fetch job '{job_id}' already scheduled.")


def start_scheduler(flask_app):
    """Starts the background scheduler.

    Args:
        flask_app: The Flask application instance
    """
    if not scheduler.running:
        schedule_api_fetch(flask_app)  # Add the job
        scheduler.start()
        scheduler_logger.info("Background scheduler started.")
    else:
        scheduler_logger.info("Background scheduler already running.")


def stop_scheduler():
    """Stops the background scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        scheduler_logger.info("Background scheduler stopped.")
