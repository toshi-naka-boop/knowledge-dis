/* Knowledge Discovery — My Agent (requester) UI logic, Company Atlas redesign.
   NOTE: server.py does not statically serve this directory (only the raw
   HTML shells at /requester /candidate /audit), so this exact content is
   inlined here. Keep in sync with web/ui.js. */

const API_KEY = (new URLSearchParams(location.search)).get("api_key") || "";
// Demo/screenshot device: with ?reveal=1, the first request_draft stagnation
// card skips the "Looking across your organization…" animation and renders
// its PersonCard immediately. Normal URLs (no reveal=1) are unaffected.
const REVEAL_PARAM = (new URLSearchParams(location.search)).get("reveal") === "1";
// Demo staging: ?view=atlas opens the full-atlas Bridge Trace view on load,
// for recording without an on-camera click. (?autonomy=1 is obsolete — the
// Autonomy Policy card is now always visible — and is accepted as a no-op.)
let revealedOnce = false;

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// Demo persona directory (README: one-person-plays-all demo device, R-1).
// /api/me does not return a display name, so names shown in the chart /
// connection graphics are looked up from this fixed demo roster.
const PERSONA_DIRECTORY = {
  emp_jordan_lee: "Jordan Lee",
  emp_marcus_delgado: "Marcus Delgado",
  emp_rachel_kim: "Rachel Kim",
  emp_elena_vasquez: "Elena Vasquez",
  emp_tom_whitfield: "Tom Whitfield",
};
const PERSONA_DEPT = {
  emp_jordan_lee: "Healthcare Staffing",
  emp_marcus_delgado: "Real Estate",
  emp_rachel_kim: "Healthcare Staffing",
  emp_elena_vasquez: "Transition Advisory",
  emp_tom_whitfield: "Corporate Services",
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

let REQUESTER_ID = "emp_jordan_lee";
let pollInterval = null;
const STAG_CARDS = {}; // card_id -> latest card payload, for the reveal flow

// The atlas view's introduction card: filled from the top candidate of the
// first revealed request_draft stagnation card. Null when nothing is prepared.
let ATLAS_INTRO = null; // { cardId, name, reason, questionDraft }
let ATLAS_STATE = "discovered"; // discovered | asked | connected

function withKey(path) {
  if (!API_KEY) return path; // cookie session (/login) rides along on its own
  return path + (path.includes("?") ? "&" : "?") + "api_key=" + encodeURIComponent(API_KEY);
}

function setupChrome() {
  if (!API_KEY) {
    // No key in the URL: a /login cookie session may be active. Probe once
    // and bounce to the sign-in page if it is not.
    fetch("/api/me").then((r) => { if (r.status === 401) location.replace("/login"); }).catch(() => {});
  }
  const candLink = document.getElementById("navCandidate");
  if (candLink) candLink.href = withKey("/candidate");
  for (const id of ["navAudit", "footnoteTraceLink", "traceLink"]) {
    const el = document.getElementById(id);
    if (el) el.href = withKey("/audit");
  }
}

// ---------------------------------------------------------------------------
// View + atlas-state machine (handoff: discovered → asked → connected)
// ---------------------------------------------------------------------------

function showView(which) {
  document.getElementById("viewRail").hidden = which !== "rail";
  document.getElementById("viewAtlas").hidden = which !== "atlas";
  document.getElementById("topbarContext").innerText =
    which === "atlas" ? "MERIDIAN CARE PARTNERS — ORGANIZATION ATLAS" : "MY AGENT";
}

function setAtlasState(state) {
  if (ATLAS_STATE === state) return;
  ATLAS_STATE = state;
  const wrap = document.getElementById("atlasWrap");
  wrap.className = "atlas-wrap atlas--" + state;
  const intro = document.getElementById("introCard");
  const asked = document.getElementById("askedCard");
  intro.hidden = !(state === "discovered" && ATLAS_INTRO);
  asked.hidden = state !== "asked";
  if (state === "connected") {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    document.getElementById("stampText").textContent = `INTRODUCED · ${hh}:${mm}`;
    document.getElementById("traceLabel").innerText = "LEDGER";
    document.getElementById("traceText").innerHTML =
      "introduction asked&nbsp;&nbsp;·&nbsp;&nbsp;accepted — 15 min, time to be coordinated&nbsp;&nbsp;·&nbsp;&nbsp;crossing recorded in the atlas";
  }
}

function renderIntroCard() {
  const intro = document.getElementById("introCard");
  if (!ATLAS_INTRO || ATLAS_STATE !== "discovered") {
    intro.hidden = true;
    return;
  }
  document.getElementById("introName").innerText = ATLAS_INTRO.name;
  document.getElementById("introIsland").innerText =
    (PERSONA_DEPT[ATLAS_INTRO.employeeId] || "CANDIDATE").toUpperCase() + " ISLAND";
  document.getElementById("introEvidence").innerText = ATLAS_INTRO.reason;
  document.getElementById("introQuestion").value = ATLAS_INTRO.questionDraft;
  document.getElementById("introAskBtn").innerText = `Ask ${ATLAS_INTRO.name.split(" ")[0]} for 15 min`;
  intro.hidden = false;
}

async function confirmFromIntroCard() {
  if (!ATLAS_INTRO) return;
  const edited = document.getElementById("introQuestion").value.trim();
  if (!edited) { alert("Please enter a question to dispatch."); return; }
  await confirmStagnation(ATLAS_INTRO.cardId, edited);
}

function switchRequester(id) {
  REQUESTER_ID = id;
  ATLAS_INTRO = null;
  setAtlasState("discovered");
  renderIntroCard();
  document.getElementById("topbarStatus").innerText =
    `${displayName(id)} · ${PERSONA_DEPT[id] || ""}`;
  document.getElementById("greeting").innerText = `Good morning, ${firstName(id)}`;
  document.getElementById("atlasJordanName").textContent = displayName(id);
  document.getElementById("cornerName").textContent = displayName(id);
  fetchDigest();
  fetchStatuses();
  loadAutonomyPolicy();
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
  document.getElementById("topbarStatus").innerText =
    `${displayName(REQUESTER_ID)} · ${PERSONA_DEPT[REQUESTER_ID] || ""}`;
  document.getElementById("greeting").innerText = `Good morning, ${firstName(REQUESTER_ID)}`;
  document.getElementById("atlasJordanName").textContent = displayName(REQUESTER_ID);
  document.getElementById("cornerName").textContent = displayName(REQUESTER_ID);
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

// Autonomy status display (design §8 A/C-17): "Automatic sweep · <relative>"
// when effective Monitor is on, "Monitoring paused" when off. Naive Date
// diff — fine even against DEMO_TODAY skew.
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

function renderSweepStatus(data) {
  const el = document.getElementById("agentBadgeText");
  if (!el) return;
  const effective = (data.autonomy && data.autonomy.effective) || {};
  if (!effective.monitor_stalled_work) {
    el.innerText = "Your agent · monitoring paused";
    return;
  }
  let text = "Your agent · monitoring automatically";
  if (data.last_sweep && data.last_sweep.at) {
    text += ` · last sweep ${formatRelativeTime(data.last_sweep.at)}`;
  }
  el.innerText = text;
}

function renderDigest(data) {
  document.getElementById("digestDateBadge").innerText = data.date ? ("TODAY · " + data.date) : "TODAY";
  renderSweepStatus(data);

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

  // Need Detection (stagnation cards) — its own section above
  const needContainer = document.getElementById("needDetectionSection");
  const stagCards = data.stagnation_cards || [];
  stagCards.forEach(c => { STAG_CARDS[c.card_id] = c; });
  const monitorOn = !!((data.autonomy && data.autonomy.effective || {}).monitor_stalled_work);
  // Rail/chart balance: the chart pane appears only once a stall is worth
  // showing (request_draft tier); the calm morning is pure secretary.
  const hasNeed = stagCards.some(c => c.tier === "request_draft");
  const railView = document.getElementById("viewRail");
  railView.classList.toggle("view--need", hasNeed);
  railView.classList.toggle("view--calm", !hasNeed);
  if (stagCards.length === 0) {
    needContainer.innerHTML = "";
    document.getElementById("cornerNeed").textContent = "";
    document.getElementById("cornerRoutes").style.display = "none";
  } else {
    const ordered = [...stagCards].sort(
      (a, b) => (a.tier === "request_draft" ? 0 : 1) - (b.tier === "request_draft" ? 0 : 1)
    );
    needContainer.innerHTML = ordered.map(c => renderNeedCard(c, monitorOn)).join("");
    const primary = ordered[0];
    if (primary && primary.tier === "request_draft") {
      const p = primary.payload || {};
      document.getElementById("cornerNeed").textContent =
        `NEED · ${(p.task_title || p.task_id || "").toUpperCase().slice(0, 40)} ¹`;
      document.getElementById("cornerRoutes").style.display = "";
      document.getElementById("footnoteText").innerText =
        `stall detected — ${p.evidence_line || "no recent activity"}`;
      document.getElementById("atlasJordanNeed").textContent =
        `NEED · ${(p.task_title || "").slice(0, 34)} ¹`;
    }
  }

  if (REVEAL_PARAM && !revealedOnce) {
    const target = stagCards.find(c => c.tier === "request_draft");
    if (target) {
      revealedOnce = true;
      revealCandidates(target.card_id);
    }
  }
}

function renderSuggestCard(card) {
  const p = card.payload || {};
  return `
    <div class="suggest-card" id="card_box_${esc(card.card_id)}">
      <div class="eyebrow" style="margin-bottom: 6px; color: var(--action);">YOUR AGENT SUGGESTS</div>
      <div class="suggest-subject">From a recent email: <strong>${esc(p.subject || "Email")}</strong></div>
      <div class="suggest-preview">[${esc(p.item_key || "current_work")}] ${esc(p.body_draft || "")}</div>
      <div id="diff_edit_box_${esc(card.card_id)}" class="suggest-edit-box">
        <textarea id="diff_edit_text_${esc(card.card_id)}">${esc(p.body_draft || "")}</textarea>
        <button class="btn-action" onclick="submitEditDiff('${esc(card.card_id)}')">Save &amp; apply</button>
      </div>
      <div class="btn-row" style="margin-top: 0;">
        <button class="btn-action" style="height: 34px; padding: 0 16px;" onclick="reviewDiff('${esc(card.card_id)}', 'apply')">Apply</button>
        <button class="btn-ghost" style="height: 34px; padding: 0 14px;" onclick="toggleEditDiff('${esc(card.card_id)}')">Edit &amp; apply</button>
        <button class="btn-ghost" style="height: 34px; padding: 0 14px;" onclick="reviewDiff('${esc(card.card_id)}', 'private_apply')">Apply as private</button>
        <button class="btn-quiet" onclick="reviewDiff('${esc(card.card_id)}', 'dismiss')">Skip</button>
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
// Need Detection: "YOUR AGENT NOTICED" -> Find someone -> reveal -> ask
// -------------------------------------------------------------------------

// Comma-split evidence_line into short "· "-joined muted fragments
// (e.g. "Rescheduled 2 times · No updates for 5 days").
function splitEvidence(line) {
  return String(line || "").split(",").map(s => s.trim()).filter(Boolean).join(" · ");
}

function renderNeedCard(card, monitorOn) {
  const p = card.payload || {};
  if (card.tier === "request_draft") {
    return `
    <div class="atl-card" id="card_box_${esc(card.card_id)}" data-state="initial">
      <div class="eyebrow">YOUR AGENT NOTICED ¹</div>
      <div class="card-headline">${esc(p.task_title || p.task_id)} has stalled.</div>
      <div class="card-body">${esc(splitEvidence(p.evidence_line) || "This is stalling.")} I can look across your organization for someone who has relevant experience.</div>
      <div class="btn-row">
        <button class="btn-action" onclick="findSomeone('${esc(card.card_id)}')">Find someone who can help</button>
        <button class="btn-quiet" onclick="dismissCard('${esc(card.card_id)}')">Dismiss</button>
      </div>
      <div class="card-meta">nothing has been sent to anyone</div>
    </div>`;
  }
  const shortEvidence = String(p.evidence_line || "").split(",")[0].trim().replace(/\.$/, "");
  const monitorLabel = monitorOn ? "Monitoring" : "Paused";
  return `
    <div class="need-compact" id="card_box_${esc(card.card_id)}" data-state="initial">
      <span class="need-compact-title">${esc(p.task_title || p.task_id)}</span>
      <span class="need-monitor">${esc(shortEvidence || "Stalling")} · ${monitorLabel}</span>
      <button class="btn-quiet" onclick="dismissCard('${esc(card.card_id)}')">Dismiss</button>
    </div>`;
}

function findSomeone(cardId) {
  const box = document.getElementById(`card_box_${cardId}`);
  if (!box) return;
  box.dataset.state = "exploring";
  box.innerHTML = `
    <div class="sweep-line" style="border-top: none; margin-top: 0; padding-top: 0;">
      <span class="pulse-dot"></span>
      <span class="sweep-line-text">Sweeping the organization for relevant experience…</span>
    </div>
    <div class="card-meta">nothing has been sent to anyone</div>`;
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
      <div class="eyebrow">YOUR AGENT NOTICED ¹</div>
      <div class="card-headline">${esc(p.task_title || p.task_id)}</div>
      <div class="card-body">No matching colleagues found across public profiles yet.</div>
      <div class="btn-row"><button class="btn-quiet" onclick="dismissCard('${esc(cardId)}')">Dismiss</button></div>`;
    return;
  }

  const top = cands[0];
  const rest = cands.slice(1);

  // Feed the atlas view's introduction card (handoff screen 1).
  ATLAS_INTRO = {
    cardId,
    employeeId: top.employee_id || "",
    name: top.name || top.employee_id || "Candidate",
    reason: top.reason_text || "",
    questionDraft: p.question_draft || "",
  };
  renderIntroCard();
  const cta = document.getElementById("chartCtaText");
  if (cta) cta.innerText = "Introduction prepared — see the route on the atlas ›";

  box.innerHTML = `
    <div class="eyebrow">INTRODUCTION PREPARED — NOT SENT</div>
    <div class="card-meta" style="margin: 0 0 10px;">${esc(p.task_title || p.task_id)}</div>
    <div class="person-name">${esc(top.name || top.employee_id)}</div>
    <div class="person-island">${esc((PERSONA_DEPT[top.employee_id] || "").toUpperCase() || "CANDIDATE")}</div>
    <div class="person-evidence">${esc(top.reason_text || "")} ²</div>
    <div class="ai-footnote">² relevance assessed by their agent — private items masked</div>
    <div class="person-timing" style="font-size: 13px; font-weight: 600; margin-bottom: 10px;">15 min should be enough.</div>
    <label class="question-label" for="draft_${esc(cardId)}">QUESTION DRAFT — EDIT BEFORE ASKING</label>
    <textarea id="draft_${esc(cardId)}" class="question-box">${esc(p.question_draft || "")}</textarea>
    <div class="fixed-notice">Candidates may differ at request time — the full search runs only after you confirm.</div>
    <div class="btn-row">
      <button class="btn-action" onclick="confirmStagnationCard('${esc(cardId)}')">Ask ${esc(firstName(top.name || top.employee_id))} for 15 min</button>
      <button class="btn-quiet" onclick="dismissCard('${esc(cardId)}')">Dismiss</button>
      <button class="btn-quiet" onclick="showView('atlas')">view on the atlas ›</button>
    </div>
    <div class="card-meta">Nothing is sent until you decide.</div>
    ${rest.length > 0 ? `
      <div class="secondary-candidates">
        ${rest.map(c => `
          <div class="secondary-candidate-text"><strong>${esc(c.name || c.employee_id)}</strong> — ${esc(c.reason_text || "")}</div>`).join("")}
      </div>` : ""}`;
}

async function confirmStagnationCard(cardId) {
  const textarea = document.getElementById(`draft_${cardId}`);
  const editedQuestion = textarea ? textarea.value.trim() : "";
  if (!editedQuestion) {
    alert("Please enter a question to dispatch.");
    return;
  }
  await confirmStagnation(cardId, editedQuestion);
}

// Shared confirm path for the rail card and the atlas introduction card.
async function confirmStagnation(cardId, editedQuestion) {
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
    setAtlasState("asked");

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
    if (ATLAS_INTRO && ATLAS_INTRO.cardId === cardId) {
      ATLAS_INTRO = null;
      renderIntroCard();
    }
    await fetchDigest();
  } catch (err) {
    alert("Dismiss error: " + err.message);
  }
}

// -------------------------------------------------------------------------
// Autonomy Policy card (design §8 B): always visible, checkbox changes PUT
// immediately and re-render from the normalized response (server-side
// normalization is authoritative, §5.2).
// -------------------------------------------------------------------------

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
// Connection Requests (status) — also drives the atlas state machine
// -------------------------------------------------------------------------

async function fetchStatuses() {
  try {
    const res = await fetch(`/api/requester/${REQUESTER_ID}/status`, {
      headers: { "X-API-Key": API_KEY }
    });
    if (!res.ok) return;
    const data = await res.json();
    const statuses = data.statuses || [];
    renderStatuses(statuses);
    // Atlas state: a made connection wins; otherwise a pending ask keeps the
    // dash advancing; otherwise fall back to the discovered state.
    if (statuses.some(s => s.state === "matched")) {
      setAtlasState("connected");
    } else if (statuses.some(s => s.state === "pending")) {
      setAtlasState("asked");
    }
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
        <div class="status-card">
          <div class="status-head">
            <span class="status-name">Request sent to ${name}</span>
            <span class="status-tag waiting">PENDING</span>
          </div>
          <div class="status-body">Their agent is reviewing it. Declining is invisible to you both.</div>
        </div>`;
    }
    if (item.state === "matched") {
      return `
        <div class="status-card">
          <div class="connection-created">
            <svg width="240" height="34" viewBox="0 0 240 34">
              <circle cx="16" cy="17" r="8" fill="#26221A"/>
              <path d="M 24 17 Q 120 8 216 17" fill="none" stroke="#A13A20" stroke-width="2.6"/>
              <circle cx="224" cy="17" r="8" fill="#A13A20"/>
            </svg>
            <div class="connection-caption">A new crossing was recorded.</div>
            <div class="connection-sub">${esc(displayName(REQUESTER_ID))} ↔ ${name} · 15 min · time to be coordinated</div>
            <div class="connection-tagline">AI shouldn't replace human connections. It should create them.</div>
            <button class="btn-quiet connection-link" onclick="showView('atlas')">see it on the atlas ›</button>
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
        <div class="status-card">
          <div class="status-head">
            <span class="status-name">${name}</span>
            <span class="status-tag ${item.attachment ? "shared" : "waiting"}">${item.attachment ? "SHARED" : "UNAVAILABLE"}</span>
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
  loadAutonomyPolicy();
  if ((new URLSearchParams(location.search)).get("view") === "atlas") {
    showView("atlas");
  }
});
