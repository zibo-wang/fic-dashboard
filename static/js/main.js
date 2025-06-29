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
            const now = new Date();
            const sydneyTime = new Date(now.toLocaleString('en-US', { timeZone: 'Australia/Sydney' }));
            const timeStr = sydneyTime.toLocaleTimeString('en-US', {
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                timeZone: 'Australia/Sydney'
            });
            currentTimeElem.textContent = timeStr;
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

    // Update last refresh time initially and then every 5 seconds
    updateLastRefreshTime();
    setInterval(updateLastRefreshTime, 5000);

    /**
     * Updates the dashboard statistics without requiring a full page reload.
     */
    function updateDashboardStats() {
        fetch('/get-stats')
            .then(response => {
                if (response.status === 302) {
                    // Session expired, redirect to login
                    console.log('Session expired, redirecting to login');
                    window.location.href = '/login';
                    return;
                }
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                if (!data) {
                    console.log('No data received from stats endpoint');
                    return;
                }


                // Update sidebar counts
                const issueCountElements = document.querySelectorAll('[data-stat="issue-count"]');
                const respondedCountElements = document.querySelectorAll('[data-stat="responded-count"]');
                const pendingCountElements = document.querySelectorAll('[data-stat="pending-count"]');

                issueCountElements.forEach(el => el.textContent = data.issue_count);
                respondedCountElements.forEach(el => el.textContent = data.responded_count);
                pendingCountElements.forEach(el => el.textContent = data.pending_count);

                // Update main dashboard stats
                const totalThisWeekEl = document.querySelector('[data-stat="total-this-week"]');
                const resolvedThisWeekEl = document.querySelector('[data-stat="resolved-this-week"]');
                const avgRespondTimeEl = document.querySelector('[data-stat="avg-respond-time"]');
                const avgResolveTimeEl = document.querySelector('[data-stat="avg-resolve-time"]');

                if (totalThisWeekEl) {
                    totalThisWeekEl.textContent = data.stats.total_this_week;
                }
                if (resolvedThisWeekEl) {
                    resolvedThisWeekEl.textContent = data.stats.resolved_this_week;
                }
                if (avgRespondTimeEl) {
                    const respondMinutes = data.stats.avg_respond_time_seconds ? (data.stats.avg_respond_time_seconds / 60).toFixed(2) : '0.00';
                    avgRespondTimeEl.textContent = respondMinutes;
                }
                if (avgResolveTimeEl) {
                    const resolveHours = data.stats.avg_resolve_time_seconds ? (data.stats.avg_resolve_time_seconds / 3600).toFixed(2) : '0.00';
                    avgResolveTimeEl.textContent = resolveHours;
                }

                // Update chart data if chart exists
                if (window.incidentsByDayChart && data.stats.daily_counts_data) {
                    window.incidentsByDayChart.data.datasets[0].data = data.stats.daily_counts_data;
                    window.incidentsByDayChart.update('none'); // Update without animation for better performance
                }
            })
            .catch(error => {
                console.error('Error updating dashboard stats:', error);
            });
    }

    // Update stats initially and then every 30 seconds
    updateDashboardStats();
    setInterval(updateDashboardStats, 30000);

    // Check API error state
    function checkApiErrorState() {
        fetch('/get-api-error-state')
            .then(response => {
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                return response.json();
            })
            .then(data => {
                const errorBanner = document.getElementById('apiErrorBanner');
                const errorText = document.getElementById('apiErrorText');
                if (errorBanner && errorText) {
                    if (data.has_error) {
                        errorBanner.classList.add('show');
                        errorText.textContent = `API Error: ${data.last_error}`;
                    } else {
                        errorBanner.classList.remove('show');
                    }
                }
            })
            .catch(error => {
                console.error('Error checking API state:', error);
            });
    }

    // Check API error state initially and then every 10 seconds
    checkApiErrorState();
    setInterval(checkApiErrorState, 5000);

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

            // Don't flash for API_ERROR status
            if (!isResponded && status !== 'API_ERROR' && (status === 'CRITICAL' || status === 'ERROR') && durationSeconds > 60) {
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
        window.incidentsByDayChart = new Chart(incidentsByDayCtx, {
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

    // Add Incident Modal
    const addIncidentModal = document.getElementById('addIncidentModal');
    const addIncidentForm = document.querySelector('#addIncidentModal form');

    /**
     * Opens the add incident modal.
     */
    window.openAddIncidentModal = function () {
        if (!addIncidentModal || !addIncidentForm) {
            console.error('Add Incident modal elements not found');
            return;
        }
        // Hide all other modals first
        modals.forEach(modal => modal.classList.add('hidden'));
        addIncidentModal.classList.remove('hidden');
    }

    /**
     * Closes the add incident modal and resets its form.
     */
    window.closeAddIncidentModal = function () {
        if (!addIncidentModal || !addIncidentForm) {
            console.error('Add Incident modal elements not found');
            return;
        }
        addIncidentModal.classList.add('hidden');
        addIncidentForm.reset();
    }

    // Resolve Modal
    const resolveModal = document.getElementById('resolveModal');
    const resolveModalForm = document.getElementById('resolveModalForm');
    const resolveModalIncidentId = document.getElementById('resolveModalIncidentId');
    const resolveModalJobName = document.getElementById('resolveModalJobName');
    const resolveModalReason = document.getElementById('resolveModalReason');
    const resolveModalAction = document.getElementById('resolveModalAction');
    const resolveModalPending = document.getElementById('resolveModalPending');

    /**
     * Opens the resolve modal for a specific incident.
     *
     * @param {number} incidentId - The ID of the incident
     * @param {string} jobName - The name of the job
     */
    window.openResolveModal = function (incidentId, jobName) {
        if (!resolveModal || !resolveModalForm || !resolveModalIncidentId || !resolveModalJobName ||
            !resolveModalReason || !resolveModalAction || !resolveModalPending) {
            console.error('Resolve modal elements not found');
            return;
        }
        // Hide all other modals first
        modals.forEach(modal => modal.classList.add('hidden'));
        resolveModalIncidentId.value = incidentId;
        resolveModalJobName.textContent = jobName;
        resolveModalForm.action = `/resolve-incident/${incidentId}`;
        // Clear all form fields
        resolveModalReason.value = '';
        resolveModalAction.value = '';
        resolveModalPending.value = '';
        resolveModal.classList.remove('hidden');
        // Focus on the first textarea
        setTimeout(() => resolveModalReason.focus(), 100);
    }

    /**
     * Closes the resolve modal and resets its form.
     */
    window.closeResolveModal = function () {
        if (!resolveModal || !resolveModalForm) {
            console.error('Resolve modal elements not found');
            return;
        }
        resolveModal.classList.add('hidden');
        resolveModalForm.reset();
    }

    // Resolve Notes View Modal
    const resolveNotesModal = document.getElementById('resolveNotesModal');
    const resolveNotesModalJobName = document.getElementById('resolveNotesModalJobName');
    const resolveNotesModalContent = document.getElementById('resolveNotesModalContent');

    /**
     * Opens the resolve notes view modal to display full resolution details.
     *
     * @param {string} jobName - The name of the job
     * @param {string} resolveNotes - The full resolution notes
     */
    window.openResolveNotesModal = function (jobName, resolveNotes) {
        if (!resolveNotesModal || !resolveNotesModalJobName || !resolveNotesModalContent) {
            console.error('Resolve notes modal elements not found');
            return;
        }

        // Hide all other modals first
        modals.forEach(modal => modal.classList.add('hidden'));

        resolveNotesModalJobName.textContent = jobName;

        // Parse and format the structured notes
        const sections = resolveNotes.split('\n\n');
        let formattedContent = '';

        sections.forEach(section => {
            if (section.trim()) {
                const lines = section.split('\n');
                const header = lines[0];
                const content = lines.slice(1).join('\n');

                if (header.startsWith('**') && header.endsWith(':**')) {
                    // This is a section header
                    const sectionTitle = header.replace(/\*\*/g, '').replace(':', '');
                    formattedContent += `
                        <div class="mb-4">
                            <h4 class="text-sm font-semibold text-tn-cyan mb-2">${sectionTitle}</h4>
                            <div class="bg-tn-bg-alt p-3 rounded border border-tn-comment">
                                <p class="text-sm text-tn-text whitespace-pre-wrap">${content}</p>
                            </div>
                        </div>
                    `;
                } else {
                    // Fallback for unstructured content
                    formattedContent += `
                        <div class="mb-4">
                            <div class="bg-tn-bg-alt p-3 rounded border border-tn-comment">
                                <p class="text-sm text-tn-text whitespace-pre-wrap">${section}</p>
                            </div>
                        </div>
                    `;
                }
            }
        });

        resolveNotesModalContent.innerHTML = formattedContent;
        resolveNotesModal.classList.remove('hidden');
    }

    /**
     * Closes the resolve notes view modal.
     */
    window.closeResolveNotesModal = function () {
        if (!resolveNotesModal) {
            console.error('Resolve notes modal elements not found');
            return;
        }
        resolveNotesModal.classList.add('hidden');
    }

    // Close modals when clicking outside
    window.addEventListener('click', function (event) {
        if (event.target.classList.contains('modal')) {
            if (respondModal && !respondModal.classList.contains('hidden')) {
                closeRespondModal();
            }
            if (incLinkModal && !incLinkModal.classList.contains('hidden')) {
                closeIncLinkModal();
            }
            if (addEngineerModal && !addEngineerModal.classList.contains('hidden')) {
                closeAddEngineerModal();
            }
            if (addIncidentModal && !addIncidentModal.classList.contains('hidden')) {
                closeAddIncidentModal();
            }
            if (resolveModal && !resolveModal.classList.contains('hidden')) {
                closeResolveModal();
            }
            if (resolveNotesModal && !resolveNotesModal.classList.contains('hidden')) {
                closeResolveNotesModal();
            }
        }
    });

    // Close modals on ESC key
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            if (respondModal && !respondModal.classList.contains('hidden')) {
                closeRespondModal();
            }
            if (incLinkModal && !incLinkModal.classList.contains('hidden')) {
                closeIncLinkModal();
            }
            if (addEngineerModal && !addEngineerModal.classList.contains('hidden')) {
                closeAddEngineerModal();
            }
            if (addIncidentModal && !addIncidentModal.classList.contains('hidden')) {
                closeAddIncidentModal();
            }
            if (resolveModal && !resolveModal.classList.contains('hidden')) {
                closeResolveModal();
            }
            if (resolveNotesModal && !resolveNotesModal.classList.contains('hidden')) {
                closeResolveNotesModal();
            }
        }
    });

    // Enhanced auto-refresh functionality
    let lastIncidentCount = document.querySelectorAll('tr[data-first-detected]').length;
    let lastRefreshTimeValue = lastRefreshTimeElem?.textContent || 'Never';

    // Function to check if any modal is currently open
    function isAnyModalOpen() {
        const allModals = [
            respondModal,
            incLinkModal,
            addEngineerModal,
            addIncidentModal,
            resolveModal,
            resolveNotesModal
        ];

        return allModals.some(modal => modal && !modal.classList.contains('hidden'));
    }

    // Function to check if page needs refresh
    function checkForUpdates() {
        // Don't refresh if any modal is open
        if (isAnyModalOpen()) {
            console.log('Skipping refresh - modal is open');
            return;
        }

        // Check both incident count and last refresh time
        Promise.all([
            fetch('/get-incident-count').then(r => r.json()),
            fetch('/get-last-refresh-time').then(r => r.json())
        ]).then(([countData, refreshData]) => {
            const currentCount = countData.count || 0;
            const currentRefreshTime = refreshData.last_refresh_time || 'Never';

            // Check if either incident count or refresh time has changed
            if (currentCount !== lastIncidentCount ||
                (currentRefreshTime !== 'Never' && currentRefreshTime !== lastRefreshTimeValue)) {
                console.log(`Updates detected - Count: ${lastIncidentCount} → ${currentCount}, Time: ${lastRefreshTimeValue} → ${currentRefreshTime}`);
                location.reload();
            }

            // Update the stored values
            lastIncidentCount = currentCount;
            lastRefreshTimeValue = currentRefreshTime;
        }).catch(error => {
            console.error('Error checking for updates:', error);
        });
    }

    // Check for updates every 30 seconds
    setInterval(checkForUpdates, 30000);

    // Force refresh every 2 minutes as a fallback (but still respect open modals)
    setInterval(() => {
        if (isAnyModalOpen()) {
            console.log('Skipping forced refresh - modal is open');
            return;
        }
        console.log('Forcing periodic refresh');
        location.reload();
    }, 120000);
});

// Make weeklyStats globally available for Chart.js if it's rendered in a script tag in HTML
// This is done by Flask template rendering:
// <script>
//   const weeklyStats = {{ stats|tojson }};
// </script>
// Add this script tag in your index.html <head> or before main.js