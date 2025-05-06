/**
 * Main JavaScript file for the FIC Dashboard.
 * 
 * This file handles all client-side functionality including:
 * - Time updates and display
 * - Incident duration calculations
 * - Modal interactions
 * - Chart rendering
 * - Auto-refresh functionality
 */

document.addEventListener('DOMContentLoaded', function () {
    // --- Time Updates ---
    const currentTimeElem = document.getElementById('current-time');
    const lastRefreshTimeElem = document.getElementById('last-refresh-time');

    /**
     * Updates the current time display with the local time.
     */
    function updateCurrentTime() {
        if (currentTimeElem) {
            currentTimeElem.textContent = new Date().toLocaleString('en-US', {
                year: 'numeric', month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'
            });
        }
    }
    updateCurrentTime();
    setInterval(updateCurrentTime, 1000);

    /**
     * Fetches and updates the last refresh time from the server.
     */
    function updateLastRefreshTime() {
        fetch('/get-last-refresh-time')
            .then(response => {
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                return response.json();
            })
            .then(data => {
                if (lastRefreshTimeElem && data.last_refresh_time) {
                    lastRefreshTimeElem.textContent = data.last_refresh_time;
                }
            })
            .catch(error => {
                console.error('Error fetching last refresh time:', error);
            });
    }

    // Update last refresh time initially and then every 10 seconds
    updateLastRefreshTime();
    setInterval(updateLastRefreshTime, 10000);

    // Auto-refresh page (if desired, often better to use HTMX or fetch for partials)
    // setTimeout(() => {
    //     console.log("Auto-reloading page for fresh data...");
    //     location.reload();
    // }, 60000); // Refresh every 60 seconds - adjust as needed

    // --- Incident Durations & Flashing ---
    const incidentRows = document.querySelectorAll('tr[data-first-detected]');
    // Use the server-provided UTC timestamp as the "current time" basis for duration calculations
    // This avoids client/server time discrepancies affecting initial duration display.
    // For ongoing timers, client's Date.now() is fine.
    let now_ts_client = Date.now();
    let now_ts_server = serverNowUTCTimestamp * 1000; // Convert Python timestamp (seconds) to JS (ms)
    let client_server_time_diff_ms = now_ts_client - now_ts_server;

    /**
     * Formats a duration in seconds into a human-readable string.
     * 
     * @param {number} seconds - The duration in seconds
     * @returns {string} Formatted duration string (e.g., "2d 3h 45m 30s")
     */
    function formatDuration(seconds) {
        if (seconds < 0) seconds = 0;
        const d = Math.floor(seconds / (3600 * 24));
        const h = Math.floor(seconds % (3600 * 24) / 3600);
        const m = Math.floor(seconds % 3600 / 60);
        const s = Math.floor(seconds % 60);
        return `${d > 0 ? d + 'd ' : ''}${h > 0 || d > 0 ? h + 'h ' : ''}${m > 0 || h > 0 || d > 0 ? m + 'm ' : ''}${s}s`;
    }

    /**
     * Updates incident durations and applies flashing effect for critical incidents.
     */
    function updateDurationsAndFlash() {
        const now = Math.floor((Date.now() - client_server_time_diff_ms) / 1000); // Adjusted current time in seconds

        incidentRows.forEach(row => {
            const firstDetected = parseInt(row.dataset.firstDetected, 10);
            const durationSeconds = now - firstDetected;

            const durationElem = row.querySelector('.incident-duration');
            if (durationElem) {
                durationElem.textContent = formatDuration(durationSeconds);
            }

            const status = row.dataset.status;
            const isResponded = row.dataset.responded === 'true';

            if (!isResponded && (status === 'CRITICAL' || status === 'ERROR') && durationSeconds > 60) {
                row.classList.add('flash-red');
            } else {
                row.classList.remove('flash-red');
            }
        });
    }
    updateDurationsAndFlash(); // Initial call
    setInterval(updateDurationsAndFlash, 1000); // Update every second


    // --- Charts ---
    const incidentsByDayCtx = document.getElementById('incidentsByDayChart');
    if (incidentsByDayCtx && typeof weeklyStats !== 'undefined') { // weeklyStats should be passed from template
        new Chart(incidentsByDayCtx, {
            type: 'bar',
            data: {
                labels: weeklyStats.daily_counts_labels, // e.g., ['Mon', 'Tue', ...]
                datasets: [{
                    label: '# of Incidents',
                    data: weeklyStats.daily_counts_data, // e.g., [12, 19, ...]
                    backgroundColor: 'rgba(122, 162, 247, 0.7)', // tn-blue with alpha
                    borderColor: 'rgba(122, 162, 247, 1)', // tn-blue
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false, // Important for sizing in a container
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { color: '#c0caf5' }, // tn-text
                        grid: { color: '#565f89' } // tn-comment
                    },
                    x: {
                        ticks: { color: '#c0caf5' }, // tn-text
                        grid: { color: '#565f89' } // tn-comment
                    }
                },
                plugins: {
                    legend: {
                        labels: { color: '#c0caf5' } // tn-text
                    }
                }
            }
        });
    } else if (incidentsByDayCtx) {
        console.warn("incidentsByDayChart canvas found, but weeklyStats data is missing or undefined.");
    }

    // --- Modal Handling ---
    // Initialize all modals as hidden
    const modals = document.querySelectorAll('.modal');
    modals.forEach(modal => {
        modal.classList.add('hidden');
    });

    // Respond Modal
    const respondModal = document.getElementById('respondModal');
    const respondModalForm = document.getElementById('respondModalForm');
    const respondModalIncidentId = document.getElementById('respondModalIncidentId');
    const respondModalJobName = document.getElementById('respondModalJobName');

    /**
     * Opens the respond modal for a specific incident.
     * 
     * @param {number} incidentId - The ID of the incident
     * @param {string} jobName - The name of the job
     */
    window.openRespondModal = function (incidentId, jobName) {
        if (!respondModal || !respondModalForm || !respondModalIncidentId || !respondModalJobName) {
            console.error('Respond modal elements not found');
            return;
        }
        // Hide all other modals first
        modals.forEach(modal => modal.classList.add('hidden'));
        respondModalIncidentId.value = incidentId;
        respondModalJobName.textContent = jobName;
        respondModalForm.action = `/respond-incident/${incidentId}`;
        // Set default priority to P3
        const prioritySelect = document.getElementById('respondModalPriority');
        if (prioritySelect) {
            prioritySelect.value = 'P3';
        }
        respondModal.classList.remove('hidden');
    }

    /**
     * Closes the respond modal and resets its form.
     */
    window.closeRespondModal = function () {
        if (!respondModal || !respondModalForm) {
            console.error('Respond modal elements not found');
            return;
        }
        respondModal.classList.add('hidden');
        respondModalForm.reset();
    }

    // INC Link Modal
    const incLinkModal = document.getElementById('incLinkModal');
    const incLinkModalForm = document.getElementById('incLinkModalForm');
    const incLinkModalIncidentId = document.getElementById('incLinkModalIncidentId');
    const incLinkModalIncNumber = document.getElementById('incLinkModalIncNumber');
    const incLinkModalIncLink = document.getElementById('incLinkModalIncLink');

    /**
     * Opens the INC link modal for a specific incident.
     * 
     * @param {number} incidentId - The ID of the incident
     * @param {string} currentIncNumber - Current incident number
     * @param {string} currentIncLink - Current incident link
     */
    window.openIncLinkModal = function (incidentId, currentIncNumber, currentIncLink) {
        if (!incLinkModal || !incLinkModalForm || !incLinkModalIncidentId || !incLinkModalIncNumber || !incLinkModalIncLink) {
            console.error('INC Link modal elements not found');
            return;
        }
        // Hide all other modals first
        modals.forEach(modal => modal.classList.add('hidden'));
        incLinkModalIncidentId.value = incidentId;
        incLinkModalIncNumber.value = currentIncNumber || '';
        incLinkModalIncLink.value = currentIncLink || '';
        incLinkModalForm.action = `/update-inc-link/${incidentId}`;
        incLinkModal.classList.remove('hidden');
    }

    /**
     * Closes the INC link modal and resets its form.
     */
    window.closeIncLinkModal = function () {
        if (!incLinkModal || !incLinkModalForm) {
            console.error('INC Link modal elements not found');
            return;
        }
        incLinkModal.classList.add('hidden');
        incLinkModalForm.reset();
    }

    // Add Engineer Modal
    const addEngineerModal = document.getElementById('addEngineerModal');
    const addEngineerForm = document.querySelector('#addEngineerModal form');

    /**
     * Opens the add engineer modal.
     */
    window.openAddEngineerModal = function () {
        if (!addEngineerModal || !addEngineerForm) {
            console.error('Add Engineer modal elements not found');
            return;
        }
        // Hide all other modals first
        modals.forEach(modal => modal.classList.add('hidden'));
        addEngineerModal.classList.remove('hidden');
    }

    /**
     * Closes the add engineer modal and resets its form.
     */
    window.closeAddEngineerModal = function () {
        if (!addEngineerModal || !addEngineerForm) {
            console.error('Add Engineer modal elements not found');
            return;
        }
        addEngineerModal.classList.add('hidden');
        addEngineerForm.reset();
    }

    // Close modals when clicking outside
    window.addEventListener('click', function (event) {
        if (event.target.classList.contains('modal')) {
            if (!respondModal.classList.contains('hidden')) {
                closeRespondModal();
            }
            if (!incLinkModal.classList.contains('hidden')) {
                closeIncLinkModal();
            }
            if (!addEngineerModal.classList.contains('hidden')) {
                closeAddEngineerModal();
            }
        }
    });

    // Close modals on ESC key
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            if (!respondModal.classList.contains('hidden')) {
                closeRespondModal();
            }
            if (!incLinkModal.classList.contains('hidden')) {
                closeIncLinkModal();
            }
            if (!addEngineerModal.classList.contains('hidden')) {
                closeAddEngineerModal();
            }
        }
    });
});

// Make weeklyStats globally available for Chart.js if it's rendered in a script tag in HTML
// This is done by Flask template rendering:
// <script>
//   const weeklyStats = {{ stats|tojson }};
// </script>
// Add this script tag in your index.html <head> or before main.js
