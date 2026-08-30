# Knowledge Discovery — Devpost write-up (draft v1)

Track: **The Fortified Enterprise Fleet** · All Things Agentic Hackathon
Tagline: **AI shouldn't replace human connections. It should create them.**

---

## Inspiration

Companies ordered everyone back to the office to restore "collaboration": 92% of employers name in-person collaboration as a top benefit of the office ([WeWork, 2023](https://www.wework.com/newsroom/leaders-desire-for-more-in-person-collaboration-is-driving-the-return-to-office-wework-survey-finds)). But being in the same building is not the same as being connected. Atlassian's behavioral-science team measured 1,600+ team gatherings and found sporadic office attendance had **no measurable effect** on team connection ([Atlassian Teamwork Lab, 2024](https://www.atlassian.com/blog/distributed-work/intentional-togetherness-research)). An 18-month causal study at MIT found proximity creates new weak ties only **within about 150 meters** — beyond that, no significant effect ([Carmody et al., *Nature Computational Science*, 2022](https://doi.org/10.1038/s43588-022-00296-z)). In a company of thousands, the colleague who has your answer is almost always further than 150 meters away.

The cost is concrete. Knowledge workers spend **1.7 hours every week** just searching for the *right person* who can answer their question ([APQC, 2021](https://www.apqc.org/about-apqc/news-press-release/apqc-survey-finds-one-quarter-knowledge-workers-time-lost-due), n=982). **28%** say their organization is simply too big to know who holds the answer ([Forrester/Starmind, 2022](http://web.archive.org/web/20240130042729/https://www.starmind.ai/hubfs/Assets%202022/Forrester-Opportunity-Snapshot-2022.pdf)). **56%** say the only way to get the information they need is to ask someone or book a meeting ([Atlassian State of Teams 2025](https://atlassianblog.wpengine.com/wp-content/uploads/2025/03/the-state-of-teams-2025.pdf), n=12,000). And 27% of companies admit LinkedIn knows their employees better than they do ([i4cp, 2021](https://www.i4cp.com/press-releases/study-only-30-of-companies-say-their-employees-have-needed-skills-but-few-understand-current-capabilities)).

The turn that shaped this project: Catalini showed that the real mechanism behind proximity is **lower search cost** — once two labs had collaborated, separating them didn't reduce collaboration ([*Management Science*, 2018](https://doi.org/10.1287/mnsc.2017.2798)). Search cost can be lowered by other means. Asking a person is still the right move — the problem is knowing *who*. So we built a system where AI does the searching, and humans do the meeting.

## What it does

Every employee gets a **personal AI agent**. It does not answer questions on their behalf — it finds the colleague who can, and prepares a 15-minute introduction.

Autonomously, with no human in the loop until the moment that matters:

1. **Observe / Detect** — the agent notices your work has stalled (five deterministic signals; no LLM involved in detection).
2. **Explore** — it sweeps ~400 employee profiles via embeddings, public items only.
3. **Evaluate** — each candidate's *own agent* judges relevance in an isolated inference call. A candidate's agent can recognize a fit from a **private** profile item — and confirm relevance without revealing its content to anyone.
4. **Prepare** — it drafts the question and an introduction card: *"Introduction prepared — not sent."*
5. **The human boundary** — nothing is ever sent until the owner clicks **"Ask for 15 min"** after reviewing (and editing) the draft. There is no code path that contacts a person autonomously.

On the other side, the request arrives as a letter: who needs you, why you, what was shared, what stayed private. The candidate can accept, share a resource instead, or **decline quietly — declining is invisible to the requester**. Only mutual human consent creates the connection.

The whole organization is rendered as the **Company Atlas**: a parchment nautical chart where departments are islands, people are ink dots, agent activity is thin dashed survey routes, and a human connection is a bridge. Humans are large (serif names, darkest ink); agents are small (dashed lines and footnotes). A discovered-but-unapproved route is dashed; only mutual consent draws a solid line — *a path that didn't exist yesterday*. Every run is inspectable in **Bridge Trace**, the audit view.

## How we built it

**Gemini + Google Cloud end to end** (project `knowledge-discovery-2026`, asia-northeast1):

- **Cloud Run** — FastAPI service: agent-to-agent messaging, the secretary pipeline, three UIs (My Agent / Connection Request / Bridge Trace).
- **Cloud Scheduler → OIDC → `/internal/autonomous-sweep`** — the fleet runs unattended every 30 minutes. The endpoint verifies signature, audience, issuer, and the exact invoker service account; no API key is accepted on this route. Slot-keyed idempotency makes duplicate deliveries no-ops.
- **Vertex AI — Gemini 3.7 Flash** (question drafts, per-candidate evaluation) and **gemini-embedding-2** (profile search, similarity floor 0.62).
- **Firestore (native)** — profiles ×400, agents, tasks, cards, autonomy policies, sweep-run claims, and `messages` — the append-only audit log.
- **Vertex AI Agent Engine (GEAP Agent Runtime)** — the secretary also runs as a first-class managed agent (ADK): a deterministic scheduled operation triggers the same sweep API, and a read-only LLM dialogue agent answers "what's on my plate today?" with a single self-scoped tool. Six agents are registered in the GEAP Agent Registry.
- **323 offline tests** — every external service sits behind an interface with deterministic fakes; the suite runs with no network and no credentials.

Process: the system was designed with an adversarial **design-loop** (design → independent critique → revision → implementation → refutation rounds, 15 critique rounds across two vendors' models), then red-teamed by three independent auditors before submission.

## The security story (why "Fortified")

- **The human approval boundary is structural, not cosmetic.** `contact_mode` is fixed to `always_ask`, enforced server-side. The autonomous agent can observe, detect, explore, evaluate, prepare — and stop.
- **Graduated, server-enforced autonomy**: Monitor ⊇ Search ⊇ Ask ⊇ Prepare. Turning off an upper permission structurally disables everything below it. Normalized on save; never trusted from the client.
- **Privacy by type system, not by LLM self-report.** Private profile items are masked fail-closed keyed off the real `visibility` field; a model that lies about what it cited cannot unmask anything.
- **Data boundary = process boundary.** Each candidate is evaluated in its own isolated inference call; cross-candidate leakage is structurally impossible.
- **Audit that respects privacy**: before approval, the trail records counts only — no names, no free text. Names appear only after a human approves. Delivery is never authorized by an LLM score alone — a missing or malformed score fails closed to "no connection" (hardened after our own red team).
- **Honest scoping**: the public demo uses a shared tenant-scoped key so judges can drive the whole flow from one URL. The production path (IAP, per-employee principals, horizontal-authorization guards) is implemented and tested; flipping the deployed mode is the one-line change that closes the demo relaxations.

## Challenges we ran into

- Making autonomy *provable* rather than promised: idempotent scheduled sweeps (schedule-slot claims with CAS), OIDC verification that fails closed, and an audit trail that distinguishes counts-only from named records.
- Letting an agent use private knowledge *without leaking it* — the consent flow where relevance is confirmed but content never moves.
- Designing the UI so the power balance is legible: humans large, agents small, and "not sent" states that look unmistakably different from sent ones.

## Accomplishments we're proud of

- A no-click demo: a stalled task becomes a prepared introduction with zero human input — and the system still cannot contact anyone.
- The decline path is a first-class feature: quiet, invisible, and optionally generous (share a resource instead).
- A fully offline test suite for a multi-agent LLM system.

## What we learned

The hard part of enterprise agents isn't capability — it's *where to stop*. Every valuable behavior in this system is defined by a boundary: what the sweep may touch, what the audit may record, what the candidate's agent may reveal, and the one click no machine is allowed to make.

## What's next

- **Real data-source connectors** (calendar, mail, docs) behind the same visibility model — the interfaces already exist.
- **Per-employee identity in production** (IAP mode; implemented, tested, one deployment flag away).
- **Long-term agent memory** via Vertex AI Memory Bank, so the secretary's picture of your expertise deepens over time.
- **Interop**: exposing the secretary via A2A/MCP so other enterprise agents can request introductions through the same consent and audit boundary — and exploring Agentspace/Spark integration as it matures.

---

*Built with Gemini 3.7 Flash, gemini-embedding-2, Cloud Run, Cloud Scheduler, Firestore, and Vertex AI Agent Engine. Fictional company (Meridian Care Partners Group, 401 employees) — all data synthetic.*
