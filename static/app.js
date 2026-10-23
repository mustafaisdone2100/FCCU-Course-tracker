/**
 * FCCU Spring 2027 Course & Seat Tracker Frontend Application
 */

const state = {
  activeTerm: "2027SP",
  terms: [],
  courses: [],
  departments: [],
  stats: null,
  alerts: [],
  searchQuery: "",
  selectedDept: "ALL",
  selectedStatus: "all",
  selectedLevel: "ALL",
  selectedDay: "ALL",
  sortBy: "course_code",
  viewMode: "grid", // 'grid' or 'table'
  soundEnabled: localStorage.getItem("fccu_sound") !== "false",
  desktopNotifications: Notification.permission === "granted",
  autoSyncEnabled: true,
  syncCountdown: 30,
  syncTimer: null,
  countdownTimer: null,
  eventSource: null,
  activeAlertBanner: null,
};

// Web Audio API Chime Synthesizer
let audioCtx = null;
function playAlertChime() {
  if (!state.soundEnabled) return;
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    if (!audioCtx) audioCtx = new AudioContextClass();
    if (audioCtx.state === "suspended") audioCtx.resume();

    const now = audioCtx.currentTime;
    
    // Tone 1: E5 (659.25 Hz)
    const osc1 = audioCtx.createOscillator();
    const gain1 = audioCtx.createGain();
    osc1.type = "sine";
    osc1.frequency.setValueAtTime(659.25, now);
    gain1.gain.setValueAtTime(0.25, now);
    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
    osc1.connect(gain1);
    gain1.connect(audioCtx.destination);
    osc1.start(now);
    osc1.stop(now + 0.35);

    // Tone 2: A5 (880 Hz)
    const osc2 = audioCtx.createOscillator();
    const gain2 = audioCtx.createGain();
    osc2.type = "sine";
    osc2.frequency.setValueAtTime(880.0, now + 0.12);
    gain2.gain.setValueAtTime(0.3, now + 0.12);
    gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.6);
    osc2.connect(gain2);
    gain2.connect(audioCtx.destination);
    osc2.start(now + 0.12);
    osc2.stop(now + 0.6);

  } catch (e) {
    console.warn("Could not play synthesized audio chime:", e);
  }
}

// Browser Desktop Notifications
function requestDesktopNotification() {
  if (!("Notification" in window)) {
    showToast("Your browser does not support desktop notifications.", "warning");
    return;
  }
  Notification.requestPermission().then((perm) => {
    state.desktopNotifications = perm === "granted";
    updateNotificationBtnUI();
    if (perm === "granted") {
      showToast("🔔 Desktop notifications enabled!", "success");
      sendNotification("FCCU Course Tracker", "You will be alerted when seats open up!");
    } else {
      showToast("Desktop notifications were disabled or denied.", "warning");
    }
  });
}

function sendNotification(title, body) {
  if (state.desktopNotifications && Notification.permission === "granted") {
    try {
      new Notification(title, {
        body: body,
        icon: "/static/icon.png",
      });
    } catch (e) {
      console.warn("Desktop notification error:", e);
    }
  }
}

// Toast Notifications
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(20px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Server-Sent Events (SSE) for Real-Time Seat Alerts
function setupEventStream() {
  if (state.eventSource) {
    state.eventSource.close();
  }

  state.eventSource = new EventSource("/api/stream");

  state.eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "seat_alert") {
        handleIncomingSeatAlert(payload.data);
      } else if (payload.type === "sync_completed") {
        handleSyncCompleted(payload.data);
      }
    } catch (e) {
      console.error("SSE parse error:", e);
    }
  };

  state.eventSource.onerror = (err) => {
    console.warn("SSE connection interrupted, retrying in 5s...");
  };
}

function handleIncomingSeatAlert(alertData) {
  playAlertChime();
  const isNewSec = alertData.alert_type === "NEW_SECTION";
  const notifTitle = isNewSec ? "🆕 FCCU New Section Added!" : "🎉 FCCU Seat Opened!";
  sendNotification(notifTitle, alertData.message);

  // Add to alert list
  state.alerts.unshift(alertData);
  updateAlertBadge();

  // Show top alert banner
  displayTopAlertBanner(alertData);

  // If new section, reload course catalog so it renders immediately
  if (isNewSec) {
    loadCourses(false);
  } else {
    // Update in-memory course seat count
    const match = state.courses.find((c) => c.uniqueness === alertData.uniqueness);
    if (match) {
      match.available = alertData.new_available;
      match.capacity = alertData.capacity;
      match.enrolled = Math.max(0, alertData.capacity - alertData.new_available);
      match.is_full = alertData.new_available <= 0;
      match.seat_status = alertData.new_available <= 0 ? "full" : (alertData.new_available <= 3 ? "low" : "open");
      renderCourses();
    }
  }

  // Refresh stats
  loadStats();
}

function displayTopAlertBanner(alertData) {
  const bannerContainer = document.getElementById("alert-banner-container");
  if (!bannerContainer) return;

  const isNewSec = alertData.alert_type === "NEW_SECTION";
  const icon = isNewSec ? "🆕" : "🎉";
  const bgStyle = isNewSec
    ? "background: linear-gradient(90deg, rgba(6, 182, 212, 0.25), rgba(99, 102, 241, 0.25)); border-color: rgba(34, 211, 238, 0.5);"
    : "background: linear-gradient(90deg, rgba(245, 158, 11, 0.2), rgba(16, 185, 129, 0.2)); border-color: rgba(245, 158, 11, 0.4);";
  const textColor = isNewSec ? "#cffafe" : "#fef3c7";

  bannerContainer.innerHTML = `
    <div class="alert-banner" style="${bgStyle}">
      <div class="alert-banner-content" style="color: ${textColor};">
        <span style="font-size: 1.3rem;">${icon}</span>
        <div>
          <div>${alertData.message}</div>
          <div style="font-size: 0.75rem; color: #94a3b8; font-weight: normal;">
            ${new Date(alertData.created_at || Date.now()).toLocaleTimeString()} &bull; Empower SIS Live Alert
          </div>
        </div>
      </div>
      <div style="display: flex; gap: 0.5rem;">
        <button class="btn btn-amber btn-sm" onclick="filterToCourse('${alertData.course_code}')">View Course</button>
        <button class="btn btn-secondary btn-sm" onclick="dismissBanner()">Dismiss</button>
      </div>
    </div>
  `;
}

function dismissBanner() {
  const bannerContainer = document.getElementById("alert-banner-container");
  if (bannerContainer) bannerContainer.innerHTML = "";
}

function filterToCourse(code) {
  state.searchQuery = code;
  const searchInput = document.getElementById("search-input");
  if (searchInput) searchInput.value = code;
  renderCourses();
  dismissBanner();
}

function handleSyncCompleted(syncData) {
  document.getElementById("last-sync-time").textContent = new Date(syncData.last_sync).toLocaleTimeString();
  if (syncData.stats) {
    state.stats = syncData.stats;
    renderStats();
  }
}

// Data Fetching
async function loadTerms() {
  try {
    const res = await fetch("/api/terms");
    const data = await res.json();
    state.terms = data.terms;
    state.activeTerm = data.default;
    renderTermSelector();
  } catch (e) {
    console.error("Failed to load terms:", e);
  }
}

async function loadDepartments() {
  try {
    const res = await fetch(`/api/departments?term=${encodeURIComponent(state.activeTerm)}`);
    const data = await res.json();
    state.departments = data.departments;
    renderDepartmentFilter();
  } catch (e) {
    console.error("Failed to load departments:", e);
  }
}

async function loadStats() {
  try {
    const res = await fetch(`/api/stats?term=${encodeURIComponent(state.activeTerm)}`);
    const data = await res.json();
    state.stats = data.stats;
    renderStats();
  } catch (e) {
    console.error("Failed to load stats:", e);
  }
}

async function loadAlerts() {
  try {
    const res = await fetch(`/api/alerts?term=${encodeURIComponent(state.activeTerm)}&limit=50`);
    const data = await res.json();
    state.alerts = data.alerts;
    updateAlertBadge();
  } catch (e) {
    console.error("Failed to load alerts:", e);
  }
}

async function loadCourses(showSpinner = true) {
  if (showSpinner) {
    document.getElementById("courses-loading").style.display = "block";
    document.getElementById("courses-container").style.display = "none";
  }

  try {
    const res = await fetch(`/api/courses?term=${encodeURIComponent(state.activeTerm)}`);
    const data = await res.json();
    state.courses = data.courses || [];
    renderCourses();
  } catch (e) {
    console.error("Failed to load courses:", e);
    showToast("Failed to fetch course data from server.", "warning");
  } finally {
    document.getElementById("courses-loading").style.display = "none";
    document.getElementById("courses-container").style.display = "block";
  }
}

async function refreshFromEmpower() {
  const syncBtn = document.getElementById("sync-now-btn");
  const origText = syncBtn.innerHTML;
  syncBtn.innerHTML = `<span>🔄</span> Syncing...`;
  syncBtn.disabled = true;

  try {
    const res = await fetch(`/api/refresh?term=${encodeURIComponent(state.activeTerm)}`, { method: "POST" });
    const data = await res.json();
    if (data.success) {
      showToast(`✅ Synced with Empower! ${data.courses_count} courses updated.`, "success");
      await Promise.all([loadCourses(false), loadStats(), loadDepartments()]);
      resetCountdown();
    } else {
      showToast(`Sync warning: ${data.message || data.error}`, "warning");
    }
  } catch (e) {
    showToast("Sync request failed. Empower might be slow or busy.", "warning");
  } finally {
    syncBtn.innerHTML = origText;
    syncBtn.disabled = false;
  }
}

// Watchlist Toggle
async function toggleWatchlist(uniqueness, event) {
  if (event) event.stopPropagation();

  try {
    const res = await fetch("/api/watchlist/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uniqueness, term: state.activeTerm }),
    });
    const data = await res.json();
    
    // Update local state
    const c = state.courses.find((item) => item.uniqueness === uniqueness);
    if (c) {
      c.is_watched = data.is_watched;
      renderCourses();
    }
    loadStats();
    showToast(data.is_watched ? "⭐ Added to course watchlist!" : "Removed from watchlist", "info");
  } catch (e) {
    showToast("Failed to update watchlist", "warning");
  }
}

// Simulation Feature (Test Alert: seat_opened or new_section)
async function triggerTestAlert(type = "seat_opened") {
  const isSection = type === "new_section";
  showToast(isSection ? "🆕 Simulating new section creation on Empower..." : "🧪 Simulating seat opening on Empower...", "info");
  try {
    const res = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term: state.activeTerm, type }),
    });
    const data = await res.json();
    if (data.success) {
      showToast(isSection ? "New section alert triggered! Check banner & sound." : "Seat alert triggered! Check banner & sound.", "success");
      await Promise.all([loadCourses(false), loadStats()]);
    }
  } catch (e) {
    showToast("Failed to trigger test alert", "warning");
  }
}

// Rendering Functions
function renderTermSelector() {
  const select = document.getElementById("term-select");
  if (!select) return;

  select.innerHTML = state.terms
    .map((t) => `<option value="${t.id}" ${t.id === state.activeTerm ? "selected" : ""}>${t.name}</option>`)
    .join("");

  select.onchange = (e) => {
    state.activeTerm = e.target.value;
    state.selectedDept = "ALL";
    state.searchQuery = "";
    document.getElementById("search-input").value = "";
    loadAllData();
  };
}

function renderDepartmentFilter() {
  const select = document.getElementById("dept-select");
  if (!select) return;

  const totalSections = state.departments.reduce((sum, d) => sum + d.count, 0);
  let html = `<option value="ALL">All Departments (${totalSections})</option>`;
  html += state.departments
    .map((d) => `<option value="${d.dept}" ${d.dept === state.selectedDept ? "selected" : ""}>${d.dept} &mdash; ${d.count} sections</option>`)
    .join("");

  select.innerHTML = html;
  select.onchange = (e) => {
    state.selectedDept = e.target.value;
    renderCourses();
  };
}

function renderStats() {
  if (!state.stats) return;
  const s = state.stats;

  document.getElementById("stat-total-courses").textContent = s.total_courses || 0;
  document.getElementById("stat-total-sections").textContent = `${s.total_sections || 0} sections`;
  document.getElementById("stat-avail-seats").textContent = s.total_available || 0;
  document.getElementById("stat-enrolled").textContent = `${s.total_enrolled || 0} enrolled`;
  document.getElementById("stat-full-count").textContent = s.full_sections || 0;
  document.getElementById("stat-low-count").textContent = `${s.low_sections || 0} low seats`;
  const newSecEl = document.getElementById("stat-new-sections");
  if (newSecEl) newSecEl.textContent = s.new_sections || 0;
  document.getElementById("stat-watched-count").textContent = s.watched_count || 0;
}

function updateAlertBadge() {
  const badge = document.getElementById("alerts-badge");
  if (badge) {
    badge.textContent = state.alerts.length;
    badge.style.display = state.alerts.length > 0 ? "inline-block" : "none";
  }
}

function renderCourses() {
  const container = document.getElementById("courses-container");
  if (!container) return;

  // Filter courses in memory
  let filtered = state.courses.filter((c) => {
    // Dept filter
    if (state.selectedDept !== "ALL" && c.dept !== state.selectedDept) return false;

    // Strict undergraduate filter: keep 100-400 levels & MATH 095, exclude postgraduate 500+
    const codeClean = (c.code || "").trim().toUpperCase();
    if (!codeClean.includes("095") && codeClean !== "95") {
      const matchNum = codeClean.match(/\d+/);
      if (matchNum) {
        const num = parseInt(matchNum[0], 10);
        if (num < 100 || num >= 500) return false;
      }
    }

    // Level filter (100, 200, 300, 400, 095)
    if (state.selectedLevel !== "ALL") {
      if (state.selectedLevel === "095") {
        if (!codeClean.includes("095") && codeClean !== "95") return false;
      } else {
        const prefix = state.selectedLevel[0];
        if (!codeClean.startsWith(prefix) || codeClean.length < 3) return false;
      }
    }

    // Status filter
    if (state.selectedStatus === "open" && c.available <= 3) return false;
    if (state.selectedStatus === "low" && (c.available <= 0 || c.available > 3)) return false;
    if (state.selectedStatus === "full" && c.available > 0) return false;
    if (state.selectedStatus === "watched" && !c.is_watched) return false;
    if (state.selectedStatus === "new" && !c.is_new) return false;

    // Day filter
    if (state.selectedDay !== "ALL") {
      if (!c.days || !c.days.includes(state.selectedDay)) return false;
    }

    // Search query (code, title, instructor, classroom)
    if (state.searchQuery.trim()) {
      const q = state.searchQuery.toLowerCase().trim();
      const match =
        c.course_code.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q) ||
        (c.instructor && c.instructor.toLowerCase().includes(q)) ||
        (c.classroom && c.classroom.toLowerCase().includes(q));
      if (!match) return false;
    }

    return true;
  });

  // Sort courses
  filtered.sort((a, b) => {
    if (state.sortBy === "available_asc") return a.available - b.available;
    if (state.sortBy === "available_desc") return b.available - a.available;
    if (state.sortBy === "title") return a.title.localeCompare(b.title);
    if (state.sortBy === "dept") return a.dept.localeCompare(b.dept);
    return a.course_code.localeCompare(b.course_code) || a.section.localeCompare(b.section);
  });

  document.getElementById("filtered-count").textContent = `${filtered.length} section${filtered.length === 1 ? "" : "s"} shown`;

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div style="font-size: 3rem; margin-bottom: 1rem;">🔍</div>
        <h3>No courses match your criteria</h3>
        <p>Try resetting filters or searching for another course code / instructor.</p>
        <button class="btn btn-secondary" style="margin-top: 1rem;" onclick="resetFilters()">Reset All Filters</button>
      </div>
    `;
    return;
  }

  if (state.viewMode === "grid") {
    container.innerHTML = `<div class="courses-grid">${filtered.map(renderCourseCard).join("")}</div>`;
  } else {
    container.innerHTML = renderCourseTable(filtered);
  }
}

function renderCourseCard(c) {
  const percentFilled = c.capacity > 0 ? Math.min(100, Math.round((c.enrolled / c.capacity) * 100)) : 0;
  
  // Status pill & bar color
  let pillClass = "pill-open";
  let pillText = `${c.available} Open`;
  let barColor = "linear-gradient(90deg, #10b981, #059669)";

  if (c.available <= 0) {
    pillClass = "pill-full";
    pillText = "FULL (0 Seats)";
    barColor = "linear-gradient(90deg, #f43f5e, #e11d48)";
  } else if (c.available <= 3) {
    pillClass = "pill-low";
    pillText = `Only ${c.available} Left!`;
    barColor = "linear-gradient(90deg, #f59e0b, #d97706)";
  }

  // Days list
  const allDays = ["M", "T", "W", "R", "F", "S"];
  const daysHtml = allDays
    .map((d) => `<span class="day-chip ${c.days && c.days.includes(d) ? "active-day" : ""}">${d}</span>`)
    .join("");

  return `
    <div class="course-card glass ${c.is_watched ? "is-watched" : ""}" id="card-${c.uniqueness}">
      <div>
        <div class="course-header">
          <div class="course-code-wrap">
            <span class="course-code-badge">${c.course_code}</span>
            <span class="section-badge">Sec ${c.section}</span>
            ${c.is_new ? `<span style="background: rgba(34, 211, 238, 0.2); border: 1px solid rgba(34, 211, 238, 0.5); color: #67e8f9; font-size: 0.65rem; font-weight: 800; padding: 2px 6px; border-radius: 4px; letter-spacing: 0.05em;">NEW</span>` : ""}
            <span class="dept-badge">${c.dept}</span>
          </div>
          <button class="btn-star ${c.is_watched ? "starred" : ""}" 
                  title="${c.is_watched ? 'Remove from Watchlist' : 'Watch for Seat Openings'}" 
                  onclick="toggleWatchlist('${c.uniqueness}', event)">
            ${c.is_watched ? "★" : "☆"}
          </button>
        </div>

        <h3 class="course-title" style="margin-top: 0.5rem;">${c.title}</h3>
      </div>

      <!-- Seat Meter -->
      <div class="seat-meter-wrap">
        <div class="seat-meter-header">
          <span style="font-weight: 600; color: #d1d5db;">
            ${c.available} / ${c.capacity} Seats Available
          </span>
          <span class="status-pill ${pillClass}">${pillText}</span>
        </div>
        <div class="seat-meter-bar-bg">
          <div class="seat-meter-bar-fill" style="width: ${percentFilled}%; background: ${barColor};"></div>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--text-dim); margin-top: 2px;">
          <span>${c.enrolled} Enrolled (${percentFilled}%)</span>
          <span>${c.credit} Credits</span>
        </div>
      </div>

      <!-- Details -->
      <div class="course-meta">
        <div class="meta-row">
          <span style="color: var(--text-dim);">👤 Instructor:</span>
          <strong style="color: #e5e7eb;">${c.instructor || "TBA"}</strong>
        </div>
        <div class="meta-row">
          <span style="color: var(--text-dim);">📍 Room:</span>
          <span>${c.classroom || "TBA"}</span>
        </div>
        <div class="meta-row" style="justify-content: space-between; flex-wrap: wrap;">
          <div style="display: flex; align-items: center; gap: 0.4rem;">
            <span style="color: var(--text-dim);">📅</span>
            <div class="days-chips">${daysHtml}</div>
          </div>
          <span style="font-size: 0.75rem; color: #a5b4fc; font-weight: 500;">
            ${c.time_range || "Schedule TBA"}
          </span>
        </div>
      </div>

      <!-- Action Bar with Email Alert Button -->
      <div class="card-action-bar">
        <button class="btn-notify-card" onclick="openSubscribeModal('${c.uniqueness}', '${c.course_code}', '${c.section}', '${(c.title || '').replace(/'/g, "\\'")}', event)">
          <span>🔔</span>
          <span>Notify Me on Email</span>
        </button>
      </div>
    </div>
  `;
}

function renderCourseTable(courses) {
  return `
    <div class="table-wrap glass">
      <table class="courses-table">
        <thead>
          <tr>
            <th>Watch</th>
            <th>Code</th>
            <th>Sec</th>
            <th>Title</th>
            <th>Instructor</th>
            <th>Schedule</th>
            <th>Room</th>
            <th>Seats (Avail / Cap)</th>
            <th>Status</th>
            <th>Email Alert</th>
          </tr>
        </thead>
        <tbody>
          ${courses.map((c) => {
            let pillClass = c.available <= 0 ? "pill-full" : (c.available <= 3 ? "pill-low" : "pill-open");
            let pillText = c.available <= 0 ? "FULL" : `${c.available} Left`;
            return `
              <tr class="${c.is_watched ? "is-watched" : ""}">
                <td>
                  <button class="btn-star ${c.is_watched ? "starred" : ""}" onclick="toggleWatchlist('${c.uniqueness}', event)">
                    ${c.is_watched ? "★" : "☆"}
                  </button>
                </td>
                <td><strong>${c.course_code}</strong></td>
                <td>
                  <span class="section-badge">${c.section}</span>
                  ${c.is_new ? `<span style="background: rgba(34, 211, 238, 0.2); border: 1px solid rgba(34, 211, 238, 0.5); color: #67e8f9; font-size: 0.65rem; font-weight: 800; padding: 1px 4px; border-radius: 4px; margin-left: 2px;">NEW</span>` : ""}
                </td>
                <td>${c.title}</td>
                <td>${c.instructor || "TBA"}</td>
                <td><span style="font-size: 0.75rem; color: #a5b4fc;">${c.schedule_raw}</span></td>
                <td>${c.classroom || "TBA"}</td>
                <td><strong>${c.available}</strong> / ${c.capacity}</td>
                <td><span class="status-pill ${pillClass}">${pillText}</span></td>
                <td>
                  <button class="btn-notify-sm" onclick="openSubscribeModal('${c.uniqueness}', '${c.course_code}', '${c.section}', '${(c.title || '').replace(/'/g, "\\'")}', event)">
                    🔔 Alert
                  </button>
                </td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>

    </div>
  `;
}

function resetFilters() {
  state.searchQuery = "";
  state.selectedDept = "ALL";
  state.selectedStatus = "all";
  state.selectedLevel = "ALL";
  state.selectedDay = "ALL";
  document.getElementById("search-input").value = "";
  document.getElementById("dept-select").value = "ALL";
  
  document.querySelectorAll(".chip[data-status]").forEach((el) => {
    el.classList.toggle("active", el.dataset.status === "all");
  });
  document.querySelectorAll(".chip[data-level]").forEach((el) => {
    el.classList.toggle("active", el.dataset.level === "ALL");
  });
  document.querySelectorAll(".chip[data-day]").forEach((el) => {
    el.classList.toggle("active", el.dataset.day === "ALL");
  });

  renderCourses();
}

function exportToCSV() {
  if (!state.courses.length) {
    showToast("No courses to export.", "warning");
    return;
  }

  const headers = ["Term", "Course Code", "Section", "Title", "Credit", "Instructor", "Classroom", "Schedule", "Available Seats", "Total Capacity", "Enrolled", "Status"];
  const rows = state.courses.map((c) => [
    c.term,
    `"${c.course_code}"`,
    `"${c.section}"`,
    `"${c.title.replace(/"/g, '""')}"`,
    c.credit,
    `"${(c.instructor || "").replace(/"/g, '""')}"`,
    `"${(c.classroom || "").replace(/"/g, '""')}"`,
    `"${(c.schedule_raw || "").replace(/"/g, '""')}"`,
    c.available,
    c.capacity,
    c.enrolled,
    c.seat_status
  ]);

  const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map((e) => e.join(","))].join("\n");
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement("a");
  link.setAttribute("href", encodedUri);
  link.setAttribute("download", `FCCU_${state.activeTerm}_Courses.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  showToast("📥 Exported course catalog to CSV!", "success");
}

// Background sync countdown
function startCountdownTimer() {
  if (state.countdownTimer) clearInterval(state.countdownTimer);
  state.syncCountdown = 30;

  state.countdownTimer = setInterval(() => {
    if (!state.autoSyncEnabled) return;
    state.syncCountdown--;
    const el = document.getElementById("sync-countdown");
    if (el) el.textContent = `${state.syncCountdown}s`;

    if (state.syncCountdown <= 0) {
      state.syncCountdown = 30;
      refreshFromEmpower();
    }
  }, 1000);
}

function resetCountdown() {
  state.syncCountdown = 30;
  const el = document.getElementById("sync-countdown");
  if (el) el.textContent = "30s";
}

// Alerts Drawer
function openAlertsModal() {
  const modal = document.getElementById("alerts-modal");
  const list = document.getElementById("alerts-modal-list");
  if (!modal || !list) return;

  if (state.alerts.length === 0) {
    list.innerHTML = `<div class="empty-state"><p>No seat change alerts recorded yet. When a seat opens in a full course, it will appear here!</p></div>`;
  } else {
    list.innerHTML = state.alerts.map((a) => `
      <div class="alert-item alert-opened">
        <div style="font-weight: 600; color: #a7f3d0;">${a.message}</div>
        <div class="alert-time">${new Date(a.created_at || Date.now()).toLocaleString()}</div>
      </div>
    `).join("");
  }

  modal.style.display = "flex";
}

function closeAlertsModal() {
  const modal = document.getElementById("alerts-modal");
  if (modal) modal.style.display = "none";
}

// Notification & Sound Toggles
function updateNotificationBtnUI() {
  const btn = document.getElementById("notify-toggle-btn");
  if (btn) {
    if (state.desktopNotifications) {
      btn.innerHTML = `🔔 Alerts On`;
      btn.classList.add("btn-amber");
    } else {
      btn.innerHTML = `🔕 Enable Alerts`;
      btn.classList.remove("btn-amber");
    }
  }
}

function toggleSound() {
  state.soundEnabled = !state.soundEnabled;
  localStorage.setItem("fccu_sound", state.soundEnabled);
  const btn = document.getElementById("sound-toggle-btn");
  if (btn) {
    btn.innerHTML = state.soundEnabled ? `🔊 Sound On` : `🔇 Muted`;
  }
  if (state.soundEnabled) playAlertChime();
  showToast(state.soundEnabled ? "Audio chimes enabled" : "Audio chimes muted", "info");
}

// Initial setup
async function loadAllData() {
  await Promise.all([
    loadCourses(),
    loadStats(),
    loadDepartments(),
    loadAlerts()
  ]);
  resetCountdown();
}

document.addEventListener("DOMContentLoaded", async () => {
  // Bind UI Controls
  const searchInput = document.getElementById("search-input");
  let searchTimeout;
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        state.searchQuery = e.target.value;
        renderCourses();
      }, 150);
    });
  }

  // Sort dropdown
  const sortSelect = document.getElementById("sort-select");
  if (sortSelect) {
    sortSelect.addEventListener("change", (e) => {
      state.sortBy = e.target.value;
      renderCourses();
    });
  }

  // Status Filter Chips
  document.querySelectorAll(".chip[data-status]").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip[data-status]").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.selectedStatus = chip.dataset.status;
      renderCourses();
    });
  });

  // Level Filter Chips (100-400 & MATH 095)
  document.querySelectorAll(".chip[data-level]").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip[data-level]").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.selectedLevel = chip.dataset.level;
      renderCourses();
    });
  });

  // Day Filter Chips
  document.querySelectorAll(".chip[data-day]").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip[data-day]").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.selectedDay = chip.dataset.day;
      renderCourses();
    });
  });

  // View switch (grid vs table)
  const viewGridBtn = document.getElementById("view-grid-btn");
  const viewTableBtn = document.getElementById("view-table-btn");
  if (viewGridBtn && viewTableBtn) {
    viewGridBtn.addEventListener("click", () => {
      state.viewMode = "grid";
      viewGridBtn.classList.add("active");
      viewTableBtn.classList.remove("active");
      renderCourses();
    });
    viewTableBtn.addEventListener("click", () => {
      state.viewMode = "table";
      viewTableBtn.classList.add("active");
      viewGridBtn.classList.remove("active");
      renderCourses();
    });
  }

  updateNotificationBtnUI();
  setupEventStream();
  await loadTerms();
  await loadAllData();
  startCountdownTimer();
});

// ==========================================
// Email Alert Subscriptions Management
// ==========================================

let currentSubTarget = null;

function openSubscribeModal(uniqueness, courseCode, section, title, event) {
  if (event) event.stopPropagation();

  currentSubTarget = { uniqueness, courseCode, section, title };
  document.getElementById("sub-modal-code").textContent = courseCode;
  document.getElementById("sub-modal-sec").textContent = `Sec ${section}`;
  document.getElementById("sub-modal-title").textContent = title;

  // Pre-fill email if previously saved
  const savedEmail = localStorage.getItem("fccu_user_email") || "";
  const emailInput = document.getElementById("sub-email-input");
  if (emailInput) {
    emailInput.value = savedEmail;
  }

  // Reset options and status
  document.getElementById("sub-opt-seat").checked = true;
  document.getElementById("sub-opt-newsec").checked = true;
  const statusMsg = document.getElementById("sub-status-msg");
  statusMsg.style.display = "none";
  statusMsg.textContent = "";

  document.getElementById("subscribe-modal").style.display = "flex";
}

function closeSubscribeModal() {
  document.getElementById("subscribe-modal").style.display = "none";
  currentSubTarget = null;
}

async function submitSubscription(e) {
  e.preventDefault();
  if (!currentSubTarget) return;

  const emailInput = document.getElementById("sub-email-input");
  const email = emailInput.value.trim();
  const notifySeatOpen = document.getElementById("sub-opt-seat").checked;
  const notifyNewSection = document.getElementById("sub-opt-newsec").checked;
  const submitBtn = document.getElementById("sub-submit-btn");
  const statusMsg = document.getElementById("sub-status-msg");

  if (!email) return;
  if (!notifySeatOpen && !notifyNewSection) {
    statusMsg.style.display = "block";
    statusMsg.style.background = "rgba(239, 68, 68, 0.2)";
    statusMsg.style.border = "1px solid #ef4444";
    statusMsg.style.color = "#fca5a5";
    statusMsg.textContent = "Please select at least one notification option.";
    return;
  }

  // Save email locally for convenience
  localStorage.setItem("fccu_user_email", email);

  const origBtn = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<span>⏳</span> Subscribing...`;

  try {
    const res = await fetch("/api/subscriptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: email,
        course_code: currentSubTarget.courseCode,
        section: currentSubTarget.section,
        uniqueness: currentSubTarget.uniqueness,
        title: currentSubTarget.title,
        term: state.activeTerm,
        notify_seat_open: notifySeatOpen,
        notify_new_section: notifyNewSection,
      }),
    });
    const data = await res.json();

    if (res.ok && data.success) {
      statusMsg.style.display = "block";
      statusMsg.style.background = "rgba(16, 185, 129, 0.2)";
      statusMsg.style.border = "1px solid #10b981";
      statusMsg.style.color = "#6ee7b7";
      statusMsg.innerHTML = `✅ <strong>Alert Active!</strong> We will email <em>${email}</em> immediately when a seat opens or new section is added.`;
      showToast(`🔔 Email alert confirmed for ${currentSubTarget.courseCode} Sec ${currentSubTarget.section}!`, "success");

      setTimeout(() => {
        closeSubscribeModal();
      }, 1800);
    } else {
      statusMsg.style.display = "block";
      statusMsg.style.background = "rgba(239, 68, 68, 0.2)";
      statusMsg.style.border = "1px solid #ef4444";
      statusMsg.style.color = "#fca5a5";
      statusMsg.textContent = data.detail || "Failed to save subscription.";
    }
  } catch (err) {
    statusMsg.style.display = "block";
    statusMsg.style.background = "rgba(239, 68, 68, 0.2)";
    statusMsg.style.border = "1px solid #ef4444";
    statusMsg.style.color = "#fca5a5";
    statusMsg.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = origBtn;
  }
}

function openManageSubscriptionsModal() {
  const modal = document.getElementById("manage-subs-modal");
  const emailInput = document.getElementById("manage-subs-email-input");
  const savedEmail = localStorage.getItem("fccu_user_email") || "";
  if (emailInput && savedEmail) {
    emailInput.value = savedEmail;
    lookupSubscriptions();
  }
  modal.style.display = "flex";
}

function closeManageSubscriptionsModal() {
  document.getElementById("manage-subs-modal").style.display = "none";
}

async function lookupSubscriptions() {
  const emailInput = document.getElementById("manage-subs-email-input");
  const listEl = document.getElementById("manage-subs-list");
  const email = emailInput.value.trim();

  if (!email) {
    listEl.innerHTML = `<div style="color: #94a3b8; font-size: 0.85rem; padding: 1rem; text-align: center;">Please enter your email address.</div>`;
    return;
  }

  localStorage.setItem("fccu_user_email", email);
  listEl.innerHTML = `<div style="color: #94a3b8; font-size: 0.85rem; padding: 1rem; text-align: center;">Searching for active alerts...</div>`;

  try {
    const res = await fetch(`/api/subscriptions?email=${encodeURIComponent(email)}`);
    const data = await res.json();
    const subs = data.subscriptions || [];

    if (subs.length === 0) {
      listEl.innerHTML = `
        <div style="background: rgba(15, 23, 42, 0.5); padding: 1.5rem; border-radius: 10px; text-align: center; color: #94a3b8; font-size: 0.85rem;">
          No active alerts found for <strong>${email}</strong>.<br>
          Click <strong>🔔 Notify Me on Email</strong> on any course card to set one up!
        </div>
      `;
      return;
    }

    listEl.innerHTML = subs.map((s) => {
      const secText = s.section && s.section !== "ALL" ? `Sec ${s.section}` : "All Sections";
      return `
        <div class="sub-item-card">
          <div>
            <div style="display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.2rem;">
              <strong style="color: #60a5fa; font-size: 0.95rem;">${s.course_code}</strong>
              <span class="section-badge" style="font-size: 0.7rem;">${secText}</span>
              ${s.capacity ? `<span style="font-size: 0.7rem; color: #94a3b8;">(${s.available}/${s.capacity} seats)</span>` : ""}
            </div>
            <div style="font-size: 0.8rem; color: #94a3b8;">${s.title || ""}</div>
            <div style="font-size: 0.7rem; color: #64748b; margin-top: 0.25rem;">
              ${s.notify_seat_open ? "🟢 Seat Openings" : ""} ${s.notify_new_section ? "• 🆕 New Sections" : ""}
            </div>
          </div>
          <button class="btn btn-secondary" style="color: #f87171; border-color: rgba(248, 113, 113, 0.3); font-size: 0.75rem; padding: 0.3rem 0.6rem;" onclick="cancelSubscription(${s.id})">
            Unsubscribe
          </button>
        </div>
      `;
    }).join("");
  } catch (err) {
    listEl.innerHTML = `<div style="color: #f87171; font-size: 0.85rem; padding: 1rem; text-align: center;">Failed to fetch subscriptions.</div>`;
  }
}

async function cancelSubscription(subId) {
  const email = (document.getElementById("manage-subs-email-input").value || "").trim();
  try {
    const res = await fetch(`/api/subscriptions/${subId}?email=${encodeURIComponent(email)}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showToast("Unsubscribed from course alerts.", "info");
      lookupSubscriptions();
    } else {
      showToast(data.detail || "Failed to unsubscribe", "warning");
    }
  } catch (e) {
    showToast("Network error trying to unsubscribe.", "warning");
  }
}

