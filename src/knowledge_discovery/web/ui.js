/* Knowledge Discovery — My Agent (requester) UI logic, Phase 1 redesign.
   NOTE: server.py does not statically serve this directory (only the raw
   HTML shells at /requester /candidate /audit), so this exact content is
   inlined into requester.html's <script> tag. Keep both files in sync. */

const API_KEY = (new URLSearchParams(location.search)).get("api_key") || "";

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// Demo persona directory (README: one-person-plays-all demo device, R-1).
// /api/me does not return a display name, so first names shown in the
// greeting / connection graphics are looked up from this fixed demo roster.
const PERSONA_DIRECTORY = {
  emp_jordan_lee: "Jordan Lee",
  emp_marcus_delgado: "Marcus Delgado",
  emp_rachel_kim: "Rachel Kim",
  emp_elena_vasquez: "Elena Vasquez",
  emp_tom_whitfield: "Tom Whitfield",
};

function displayName(idOrName) {
  if (!idOrName) return "Colleague";
  if (PERSONA_DIRECTORY[idOrName]) return PERSONA_DIRECTORY[idOrName];
  return idOrName;
}

function firstName(idOrName) {
  const full = displayName(idOrName);
  return full.split(" ")[0];
}

function initials(fullName) {
  const parts = String(fullName || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

let REQUESTER_ID = "emp_jordan_lee";
let pollInterval = null;
const STAG_CARDS = {}; // card_id -> latest card payload, for the reveal flow

function withKey(path) {
  return path + (path.includes("?") ? "&" : "?") + "api_key=" + encodeURIComponent(API_KEY);
}

function setupChrome() {
  if (!API_KEY) {
    document.body.insertAdjacentHTML("afterbegin",
      '<div class="api-key-warning">Missing api_key: open this page as /requester?api_key=YOUR_KEY</div>');
  }
  const candLink = document.getElementById("navCandidate");
  const auditLink = document.getElementById("navAudit");
  if (candLink) candLink.href = withKey("/candidate");
  if (auditLink) auditLink.href = withKey("/audit");
}

function switchRequester(id) {
  REQUESTER_ID = id;
  autonomyLoaded = false;
  fetchDigest();
  fetchStatuses();
  const autonomyDetails = document.getElementById("autonomyDisclosure");
  if (autonomyDetails && autonomyDetails.open) {
    loadAutonomyPolicy();
    autonomyLoaded = true;
  }
}

// -------------------------------------------------------------------------
// Principal resolution (design §16.1): a "human" caller is pinned to their
// own employee_id and cannot switch personas via the demo dropdown.
// -------------------------------------------------------------------------

async function initPrincipal() {
  try {
    const res = await fetch(`/api/me`, { headers: { "X-API-Key": API_KEY } });
    if (!res.ok) return;
    const me = await res.json();
    if (me.mode === "human" && me.employee_id) {
      REQUESTER_ID = me.employee_id;
      const sel = document.getElementById("requesterSelect");
      if (sel) sel.style.display = "none";
    }
  } catch (err) {
    console.error("me fetch error:", err);
  }
  document.getElementById("greeting").innerText = `Good morning, ${firstName(REQUESTER_ID)}`;
}

// -------------------------------------------------------------------------
// Manual "Ask your agent" (kept, de-emphasized)
// -------------------------------------------------------------------------

const DEMO_QUESTIONS = {
  1: "A hospital client of mine wants to relocate one of their clinics. Who in our group knows how to find sites that can actually host a medical facility — zoning, conversion, that kind of thing?",
  2: "The owner of a small clinic I work with mentioned she's thinking about retiring in a few years. Who has experience with practice succession conversations?",
  3: "We need someone with experience in ambulatory surgical center operational licensing and state compliance documentation."
};

function setQuestion(num) {
  document.getElementById("questionText").value = DEMO_QUESTIONS[num] || "";
}

function clearForm() {
  document.getElementById("questionText").value = "";
  document.getElementById("queryMeta").innerText = "";
}

async function submitInquiry() {
  const q = document.getElementById("questionText").value.trim();
  if (!q) {
    alert("Please enter a question.");
    return;
  }
  const submitBtn = document.getElementById("submitBtn");
  submitBtn.disabled = true;
  submitBtn.innerText = "Dispatching...";

  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ requester_id: REQUESTER_ID, question_text: q })
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Error dispatching query: " + (data.detail || "Unknown error"));
      return;
    }

    document.getElementById("queryMeta").innerText =
      `Inquiry ID: ${data.query_id} · dispatched to ${data.dispatched_count} candidate agent(s)`;
    document.getElementById("liveStatus").style.display = "inline-flex";
    await fetchStatuses();

    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(fetchStatuses, 2000);
  } catch (err) {
    alert("Failed to connect to server: " + err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerText = "Ask";
  }
}

// -------------------------------------------------------------------------
// Digest: TODAY reminders + "Your agent suggests" (profile diff) cards
// -------------------------------------------------------------------------

async function fetchDigest() {
  try {
    const res = await fetch(`/api/secretary/digest?employee_id=${REQUESTER_ID}`, {
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) return;
    const data = await res.json();
    renderDigest(data);
  } catch (err) {
    console.error("Digest fetch error:", err);
  }
}

async function triggerSweep() {
  try {
    const res = await fetch("/api/secretary/sweep", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({origin: "manual"})
    });
    if (!res.ok) {
      alert("Sweep failed: " + res.statusText);
      return;
    }
    await fetchDigest();
  } catch (err) {
    alert("Sweep error: " + err.message);
  }
}

// Autonomy status display (design §8 A/C-17): "· Monitoring automatically"
// + relative last-sweep time when effective Monitor is on, "· Monitoring
// paused" when it is off. Naive Date-diff — fine even against DEMO_TODAY skew.
function formatRelativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} h ago`;
  const d = new Date(iso);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[d.getMonth()]} ${d.getDate()}`;
}

function renderAgentBadge(data) {
  const el = document.getElementById("agentBadgeText");
  if (!el) return;
  const effective = (data.autonomy && data.autonomy.effective) || {};
  if (!effective.monitor_stalled_work) {
    el.innerText = "Your agent · Monitoring paused";
    return;
  }
  let text = "Your agent · Monitoring automatically";
  if (data.last_sweep && data.last_sweep.at) {
    text += ` · Last sweep ${formatRelativeTime(data.last_sweep.at)}`;
  }
  el.innerText = text;
}

function renderDigest(data) {
  document.getElementById("digestDateBadge").innerText = data.date || "Today";
  renderAgentBadge(data);

  // Reminders
  const reminderList = document.getElementById("reminderList");
  const reminders = data.reminders || [];
  if (reminders.length === 0) {
    reminderList.innerHTML = '<div class="empty-note">No pending schedule deadlines for today.</div>';
  } else {
    reminderList.innerHTML = reminders.map(r => {
      let tagClass = "tag-upcoming";
      let tagText = r.due_date;
      if (r.due_category === "overdue") { tagClass = "tag-overdue"; tagText = "Overdue"; }
      else if (r.due_category === "today") { tagClass = "tag-today"; tagText = "Due today"; }
      else if (r.due_category === "tomorrow") { tagClass = "tag-tomorrow"; tagText = "Due tomorrow"; }
      return `
        <div class="reminder-row">
          <span class="reminder-title">${esc(r.title)}</span>
          <span class="tag ${tagClass}">${esc(tagText)} · ${esc(r.due_date)}</span>
        </div>`;
    }).join("");
  }

  // "Your agent suggests" (profile diff cards) — stays inside TODAY
  const suggestContainer = document.getElementById("todaySuggestions");
  const diffCards = data.profile_diff_cards || [];
  suggestContainer.innerHTML = diffCards.map(renderSuggestCard).join("");

  // Need Detection (stagnation cards) — its own section below TODAY
  const needContainer = document.getElementById("needDetectionSection");
  const stagCards = data.stagnation_cards || [];
  stagCards.forEach(c => { STAG_CARDS[c.card_id] = c; });
  if (stagCards.length === 0) {
    needContainer.innerHTML = "";
  } else {
    needContainer.innerHTML = stagCards.map(renderNeedCard).join("");
  }
}

function renderSuggestCard(card) {
  const p = card.payload || {};
  return `
    <div class="card suggest-card" id="card_box_${esc(card.card_id)}">
      <div class="suggest-label">Your agent suggests</div>
      <div class="suggest-subject">From a recent email: <strong>${esc(p.subject || "Email")}</strong></div>
      <div class="suggest-preview">[${esc(p.item_key || "current_work")}] ${esc(p.body_draft || "")}</div>
      <div id="diff_edit_box_${esc(card.card_id)}" class="suggest-edit-box">
        <textarea id="diff_edit_text_${esc(card.card_id)}">${esc(p.body_draft || "")}</textarea>
        <button class="btn-primary" onclick="submitEditDiff('${esc(card.card_id)}')">Save &amp; apply</button>
      </div>
      <div class="btn-row">
        <button class="btn-primary" onclick="reviewDiff('${esc(card.card_id)}', 'apply')">Apply</button>
        <button class="btn-secondary" onclick="toggleEditDiff('${esc(card.card_id)}')">Edit &amp; apply</button>
        <button class="btn-secondary" onclick="reviewDiff('${esc(card.card_id)}', 'private_apply')">Apply as private</button>
        <button class="btn-text" onclick="reviewDiff('${esc(card.card_id)}', 'dismiss')">Skip</button>
      </div>
    </div>`;
}

async function reviewDiff(cardId, action, editedBody = null) {
  try {
    const res = await fetch(`/api/secretary/profile-diff/${encodeURIComponent(cardId)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ action, edited_body: editedBody })
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Review failed: " + (data.detail || "Error"));
      return;
    }
    await fetchDigest();
  } catch (err) {
    alert("Review error: " + err.message);
  }
}

function toggleEditDiff(cardId) {
  const box = document.getElementById(`diff_edit_box_${cardId}`);
  if (box) box.style.display = (box.style.display === "none" || !box.style.display) ? "block" : "none";
}

async function submitEditDiff(cardId) {
  const textEl = document.getElementById(`diff_edit_text_${cardId}`);
  const edited = textEl ? textEl.value.trim() : "";
  await reviewDiff(cardId, "edit_apply", edited);
}

// -------------------------------------------------------------------------
// Need Detection: "It looks like this is stalling" -> Find someone -> reveal
// -------------------------------------------------------------------------

function renderNeedCard(card) {
  const p = card.payload || {};
  return `
    <div class="card need-card" id="card_box_${esc(card.card_id)}" data-state="initial">
      <span class="need-evidence">${esc(p.evidence_line || "This is stalling.")}</span>
      <div class="need-task-title">${esc(p.task_title || p.task_id)}</div>
      <div class="need-copy">It looks like this is stalling. I can look across your organization for someone who has relevant experience.</div>
      <div class="btn-row">
        <button class="btn-primary" onclick="findSomeone('${esc(card.card_id)}')">Find someone who can help</button>
        <button class="btn-text" onclick="dismissCard('${esc(card.card_id)}')">Dismiss</button>
      </div>
    </div>`;
}

function findSomeone(cardId) {
  const box = document.getElementById(`card_box_${cardId}`);
  if (!box) return;
  box.dataset.state = "exploring";
  box.innerHTML = `
    <div class="explore-transition">
      <span class="explore-spark"></span>
      <span class="explore-copy">Looking across your organization…</span>
    </div>`;
  setTimeout(() => revealCandidates(cardId), 1700);
}

function revealCandidates(cardId) {
  const box = document.getElementById(`card_box_${cardId}`);
  const card = STAG_CARDS[cardId];
  if (!box || !card) return;
  box.dataset.state = "revealed";
  const p = card.payload || {};
  const cands = (p.preview && p.preview.candidates) ? p.preview.candidates : [];

  if (cands.length === 0) {
    box.innerHTML = `
      <div class="need-task-title">${esc(p.task_title || p.task_id)}</div>
      <div class="need-copy">No matching colleagues found across public profiles yet.</div>
      <div class="btn-row"><button class="btn-text" onclick="dismissCard('${esc(cardId)}')">Dismiss</button></div>`;
    return;
  }

  const top = cands[0];
  const rest = cands.slice(1);

  box.innerHTML = `
    <div class="person-card">
      <div class="avatar">${esc(initials(top.name || top.employee_id))}</div>
      <div class="person-main">
        <div class="person-name">${esc(top.name || top.employee_id)}</div>
        <div class="person-why">Why ${esc((top.name || top.employee_id).split(" ")[0])}?</div>
        <ul class="person-reasons"><li>${esc(top.reason_text || "")}</li></ul>
        <div class="ai-disclosure">🤖 Generated by AI</div>
        <div class="person-timing">15 min should be enough.</div>
        <label class="question-label" for="draft_${esc(cardId)}">Question draft (edit before sending)</label>
        <textarea id="draft_${esc(cardId)}" class="question-box">${esc(p.question_draft || "")}</textarea>
        <div class="fixed-notice">Candidates may differ at request time — the full search runs only after you confirm.</div>
        <div class="btn-row">
          <button class="btn-primary" onclick="confirmStagnationCard('${esc(cardId)}')">Ask for 15 min</button>
          <button class="btn-text" onclick="dismissCard('${esc(cardId)}')">Dismiss</button>
        </div>
        ${rest.length > 0 ? `
          <div class="secondary-candidates">
            ${rest.map(c => `
              <div class="secondary-candidate-row">
                <div class="avatar avatar-sm">${esc(initials(c.name || c.employee_id))}</div>
                <div class="secondary-candidate-text"><strong>${esc(c.name || c.employee_id)}</strong> — ${esc(c.reason_text || "")}</div>
              </div>`).join("")}
          </div>` : ""}
      </div>
    </div>`;
}

async function confirmStagnationCard(cardId) {
  const textarea = document.getElementById(`draft_${cardId}`);
  const editedQuestion = textarea ? textarea.value.trim() : "";
  if (!editedQuestion) {
    alert("Please enter a question to dispatch.");
    return;
  }
  try {
    const res = await fetch("/api/secretary/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ card_id: cardId, edited_question: editedQuestion })
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Confirm failed: " + (data.detail || "Error"));
      return;
    }

    document.getElementById("queryMeta").innerText = `Inquiry ID: ${data.query_audit_id} · dispatched via your agent`;
    document.getElementById("liveStatus").style.display = "inline-flex";

    await fetchDigest();
    await fetchStatuses();

    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(fetchStatuses, 2000);
  } catch (err) {
    alert("Confirm error: " + err.message);
  }
}

async function dismissCard(cardId) {
  try {
    const res = await fetch(`/api/secretary/cards/${encodeURIComponent(cardId)}/dismiss`, {
      method: "POST",
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) {
      alert("Dismiss failed");
      return;
    }
    delete STAG_CARDS[cardId];
    await fetchDigest();
  } catch (err) {
    alert("Dismiss error: " + err.message);
  }
}

// -------------------------------------------------------------------------
// Agent autonomy (design §8 B): secondary control, loaded on first open,
// checkbox changes PUT immediately and re-render from the normalized
// response (server-side normalization is authoritative, §5.2).
// -------------------------------------------------------------------------

let autonomyLoaded = false;

function onAutonomyToggle() {
  const details = document.getElementById("autonomyDisclosure");
  if (details && details.open && !autonomyLoaded) {
    autonomyLoaded = true;
    loadAutonomyPolicy();
  }
}

function showAutonomyError(msg) {
  const errEl = document.getElementById("autonomyError");
  if (!errEl) return;
  errEl.innerText = msg;
  errEl.style.display = "block";
}

async function loadAutonomyPolicy() {
  try {
    const res = await fetch(`/api/secretary/autonomy?employee_id=${REQUESTER_ID}`, {
      headers: { "X-API-Key": API_KEY }
    });
    const data = await res.json();
    if (!res.ok) {
      showAutonomyError("Could not load autonomy settings.");
      return;
    }
    document.getElementById("autonomyLoading").style.display = "none";
    document.getElementById("autonomyControls").style.display = "block";
    renderAutonomyPolicy(data);
  } catch (err) {
    showAutonomyError("Could not load autonomy settings.");
  }
}

function renderAutonomyPolicy(data) {
  const eff = data.effective || {};
  document.getElementById("autonomyMonitor").checked = !!eff.monitor_stalled_work;
  document.getElementById("autonomySearch").checked = !!eff.search_organization;
  document.getElementById("autonomyAsk").checked = !!eff.ask_candidate_agents;
  document.getElementById("autonomyPrepare").checked = !!eff.prepare_introduction;

  document.getElementById("autonomySearch").disabled = !eff.monitor_stalled_work;
  document.getElementById("autonomyAsk").disabled = !eff.search_organization;
  document.getElementById("autonomyPrepare").disabled = !eff.ask_candidate_agents;

  document.getElementById("autonomyError").style.display = "none";
}

async function updateAutonomyFlag(field, value) {
  const payload = {
    employee_id: REQUESTER_ID,
    monitor_stalled_work: document.getElementById("autonomyMonitor").checked,
    search_organization: document.getElementById("autonomySearch").checked,
    ask_candidate_agents: document.getElementById("autonomyAsk").checked,
    prepare_introduction: document.getElementById("autonomyPrepare").checked,
    contact_mode: "always_ask"
  };
  payload[field] = value;
  try {
    const res = await fetch("/api/secretary/autonomy", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      showAutonomyError("Could not save — please try again.");
      return;
    }
    renderAutonomyPolicy(data);
    fetchDigest();
  } catch (err) {
    showAutonomyError("Could not save — please try again.");
  }
}

// -------------------------------------------------------------------------
// Connection Requests (status)
// -------------------------------------------------------------------------

async function fetchStatuses() {
  try {
    const res = await fetch(`/api/requester/${REQUESTER_ID}/status`, {
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) return;
    const data = await res.json();
    renderStatuses(data.statuses || []);
  } catch (err) {
    console.error("Status fetch error:", err);
  }
}

function renderStatuses(statuses) {
  const container = document.getElementById("statusContainer");
  if (!statuses || statuses.length === 0) {
    container.innerHTML = '<div class="empty-note">No requests yet. Ask for an intro above to get started.</div>';
    return;
  }

  container.innerHTML = statuses.map((item) => {
    const name = esc(item.respondent_name || "Your colleague");
    if (item.state === "pending") {
      return `
        <div class="card status-card">
          <div class="status-head">
            <span class="status-name">Waiting for ${name}'s agent</span>
            <span class="status-tag waiting">Pending</span>
          </div>
          <div class="status-body">Their personal agent is reviewing the request.</div>
        </div>`;
    }
    if (item.state === "matched") {
      const you = firstName(REQUESTER_ID);
      const them = displayName(item.respondent_name || "");
      return `
        <div class="card status-card">
          <div class="connection-created">
            <div class="connection-pair">
              <div class="avatar">${esc(initials(you))}</div>
              <div class="connection-line"></div>
              <div class="avatar">${esc(initials(them))}</div>
            </div>
            <div class="connection-caption">A new connection was made.</div>
            <div class="connection-sub">15 min · time to be coordinated</div>
          </div>
        </div>`;
    }
    if (item.state === "declined") {
      let bodyHtml;
      if (item.attachment) {
        let attHtml = "";
        if (item.attachment.type === "link") {
          const safe = /^https?:\/\//.test(item.attachment.content) ? esc(item.attachment.content) : "#";
          attHtml = `<div class="attachment-box">Shared link: <a href="${safe}" target="_blank" rel="noopener">${esc(item.attachment.content)}</a></div>`;
        } else if (item.attachment.type === "doc") {
          attHtml = `<div class="attachment-box">Shared document: <a href="${withKey('/attachments/' + encodeURIComponent(item.attachment.content))}" target="_blank">${esc(item.attachment.content)}</a></div>`;
        } else {
          attHtml = `<div class="attachment-box">${esc(item.attachment.content)}</div>`;
        }
        bodyHtml = `${name} shared a resource instead.${attHtml}`;
      } else {
        bodyHtml = `${name} can't make it this time.${item.decline_reason ? ` <em>"${esc(item.decline_reason)}"</em>` : ""}`;
      }
      return `
        <div class="card status-card">
          <div class="status-head">
            <span class="status-name">${name}</span>
            <span class="status-tag ${item.attachment ? "shared" : "waiting"}">${item.attachment ? "Shared" : "Unavailable"}</span>
          </div>
          <div class="status-body">${bodyHtml}</div>
        </div>`;
    }
    return "";
  }).join("");
}

// -------------------------------------------------------------------------
// Boot
// -------------------------------------------------------------------------

setupChrome();
initPrincipal().then(() => {
  fetchDigest();
  fetchStatuses();
});
