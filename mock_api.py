# mock_api.py
import random
import time
from flask import Flask, jsonify

mock_app = Flask(__name__)

# In-memory store for mock job states
MOCK_JOBS = {
    "job_001": {
        "name": "Critical Backup Failure",
        "status": "CRITICAL",
        "log_url": "http://logs.example.com/job_001",
        "last_change": time.time(),
    },
    "job_002": {
        "name": "Data Pipeline Stalled",
        "status": "ERROR",
        "log_url": "http://logs.example.com/job_002",
        "last_change": time.time(),
    },
    "job_003": {
        "name": "User Login Latency High",
        "status": "WARNING",
        "log_url": "http://logs.example.com/job_003",
        "last_change": time.time(),
    },
    "job_004": {
        "name": "Hourly Report Generation",
        "status": "OK",
        "log_url": "http://logs.example.com/job_004",
        "last_change": time.time(),
    },
    "job_005": {
        "name": "Cache Refresh Info",
        "status": "LOG",
        "log_url": "http://logs.example.com/job_005",
        "last_change": time.time(),
    },
    "job_006": {
        "name": "Payment Gateway Timeout",
        "status": "CRITICAL",
        "log_url": "http://logs.example.com/job_006",
        "last_change": time.time() - 70,
    },  # Older critical
}


def simulate_status_changes():
    """Simulates random status changes for jobs."""
    for job_id, job_data in MOCK_JOBS.items():
        # ~10% chance of status change for active issues
        if (
            job_data["status"] in ["CRITICAL", "ERROR", "WARNING"]
            and random.random() < 0.1
        ):
            new_status = random.choice(["CRITICAL", "ERROR", "WARNING", "OK"])
            if new_status != job_data["status"]:
                print(
                    f"Mock API: Job {job_id} changed status from {job_data['status']} to {new_status}"
                )
                job_data["status"] = new_status
                job_data["last_change"] = time.time()
        # ~5% chance of a new critical issue appearing from an OK job
        elif job_data["status"] == "OK" and random.random() < 0.05:
            new_status = random.choice(["CRITICAL", "ERROR"])
            print(
                f"Mock API: Job {job_id} changed status from {job_data['status']} to {new_status}"
            )
            job_data["status"] = new_status
            job_data["last_change"] = time.time()
        # ~5% chance of an OK job becoming LOG
        elif job_data["status"] == "OK" and random.random() < 0.05:
            job_data["status"] = "LOG"
            job_data["last_change"] = time.time()


@mock_app.route("/api/jobs", methods=["GET"])
def get_jobs():
    simulate_status_changes()
    # Return only jobs that are not "OK" or "LOG" unless specifically requested,
    # or filter them in the main app. For simplicity, let's return most of them.
    # The dashboard logic will filter out "LOG" from status display.

    # Simulate some jobs disappearing (resolved)
    current_jobs = []
    for job_id, data in MOCK_JOBS.items():
        # ~5% chance an active issue resolves itself
        if data["status"] != "OK" and random.random() < 0.02:
            print(
                f"Mock API: Job {job_id} resolved itself (simulated by not returning)."
            )
            MOCK_JOBS[job_id]["status"] = "OK"  # Mark as OK internally
            MOCK_JOBS[job_id]["last_change"] = time.time()

        current_jobs.append(
            {
                "id": job_id,
                "name": data["name"],
                "status": data["status"],  # CRITICAL, ERROR, WARNING, OK, LOG
                "log_url": data["log_url"],
                "timestamp": data[
                    "last_change"
                ],  # Add a timestamp for when this status was reported
            }
        )
    return jsonify(current_jobs)


if __name__ == "__main__":
    # Run this in a separate terminal: python mock_api.py
    mock_app.run(port=5001, debug=True, use_reloader=False)
