# Knowledge Discovery — Devpost write-up (draft v1)

Track: **The Fortified Enterprise Fleet** · All Things Agentic Hackathon
Tagline: **AI shouldn't replace human connections. It should create them.**

---

## Inspiration

People, people, people — the age when AI agents do our work for us is at the door. So what is left for us humans to do?

"What truly makes us human: loving and being loved" ([Kai-Fu Lee, *AI Superpowers*, 2018](https://www.goodreads.com/work/quotes/59924665-ai-superpowers-china-silicon-valley-and-the-new-world-order)).
"We'll find that humans are wired to care about other humans" ([Sam Altman, MIT, 2024](https://mitsloan.mit.edu/ideas-made-to-matter/sam-altman-believes-ai-will-change-world-and-everything-else)).

The people building AI converge on the same answer. Human connection.

Companies bet on that answer too. Coming out of the pandemic, they called everyone back to the office, with collaboration as the loudest stated reason: 92% of employers rank in-person collaboration among the top benefits of attendance, and 35% name "wanting more collaboration" as the single biggest reason for their attendance policy ([WeWork, 2023](https://www.wework.com/newsroom/leaders-desire-for-more-in-person-collaboration-is-driving-the-return-to-office-wework-survey-finds)). But being in the building is not being connected. Atlassian's behavioral-science team measured 1,600+ team gatherings: sporadic office attendance had **no measurable effect** on team connection ([Atlassian Teamwork Lab, 2024](https://www.atlassian.com/blog/distributed-work/intentional-togetherness-research)). An 18-month causal study at MIT found that new working ties form only within about **150 meters** — beyond that, no significant effect ([Carmody et al., *Nature Computational Science*, 2022](https://doi.org/10.1038/s43588-022-00296-z)). In a company of thousands, almost every colleague is past 150 meters.

So people ask around. Knowledge workers spend **1.7 hours every week** just finding the right person to ask ([APQC, 2021](https://www.apqc.org/about-apqc/news-press-release/apqc-survey-finds-one-quarter-knowledge-workers-time-lost-due), n=982). **28%** say their organization is too large to even know who holds the answer ([Forrester/Starmind, 2022](http://web.archive.org/web/20240130042729/https://www.starmind.ai/hubfs/Assets%202022/Forrester-Opportunity-Snapshot-2022.pdf)). **56%** say the only way to get the information they need is to ask someone or book a meeting ([Atlassian State of Teams 2025](https://atlassianblog.wpengine.com/wp-content/uploads/2025/03/the-state-of-teams-2025.pdf), n=12,000). And 27% of companies admit LinkedIn knows their employees better than they do ([i4cp, 2021](https://www.i4cp.com/press-releases/study-only-30-of-companies-say-their-employees-have-needed-skills-but-few-understand-current-capabilities)).

The turning point sits on an old campus in Paris. Asbestos removal forced the labs of the Jussieu campus to relocate five times over fifteen years, with no say in where they landed; who ended up next door was close to random. Co-located labs went on to collaborate **3.5 times more**, the effect concentrated in the pairs that had been hardest to find each other — and once a pair had started working together, separating them did not stop it ([Catalini, *Management Science*, 2018](https://doi.org/10.1287/mnsc.2017.2798)). Proximity was never magic. It was low search cost. Once you have been found, distance stops mattering.

Search cost is something AI can lower. Asking a person is still the right move — the only missing piece is "who." So that is what we built: AI does the searching. Humans do the meeting.

## What it does

Every employee gets a personal AI agent. Most days it is an ordinary assistant: it lines up today's deadlines and asks whether you'd like your profile updated with something it spotted in your mail. That kind of thing.

Jordan has a problem. A client has sent her a request from a completely different field — "your group does that business too, doesn't it?" That is the whole reason. She can't just turn it down. She also has no time to go hunting for the colleague who might know. She replies "I'll check and get back to you," and the task goes quietly into the pile.

Her agent notices. And from that moment, it moves differently.

A company of 400 people has 400 agents. Hers guesses who might know and asks around — "this one: could your owner help?" Each agent answers from what it knows about its own owner. Even when the answer is yes because of something only its owner knows, it reveals nothing. It answers yes or no. That's all.

When the candidates narrow to one, the agent drafts an introduction. Then it stops. The screen reads: *Introduction prepared — not sent.* Ready, but not sent. Until Jordan reads the draft, edits it, and clicks "Ask for 15 min," nothing reaches anyone. The one decision to contact a person is reserved, by construction, for a human.

What reaches Marcus is not a notification or a ticket but a letter: who needs you, why you, what was shared, what stayed private. He can accept. He can share a resource instead. He can decline quietly — Jordan will never see it. A connection is made only when both people nod.

And the organization becomes a chart. The Company Atlas: departments are islands, people are ink dots, agent activity is thin dashed survey routes. Humans are drawn large; agents are drawn small. A route AI has found stays dashed — only mutual human consent draws the solid bridge, *a path that didn't exist yesterday*. Everything the agents did along the way is inspectable in Bridge Trace, the audit view.

## Try it

The demo is live on Cloud Run, right now. In the demo's world, "Jordan" is a healthcare-staffing manager; the out-of-field request is a clinic relocation — a real-estate question. The colleague who had done it twice sat on a different island entirely.

`/requester` is Jordan's screen. `/candidate` is the letter that reaches Marcus. `/audit` is Bridge Trace. The keyed link is in this submission's testing instructions; the whole flow — request, approve, respond, audit — runs from that one link. The data is all synthetic; you can't break anything.

## How we built it

**Gemini + Google Cloud end to end**, on the Gemini Enterprise Agent Platform (GEAP, formerly Vertex AI). Project `knowledge-discovery-2026`, Tokyo (asia-northeast1).

- **Cloud Run** — the FastAPI service. Agent-to-agent messaging, the secretary pipeline, all three screens — everything lives here. Why not GKE or Cloud Functions? Because the whole service fits in one stateless container. No cluster to operate, and you still get an authenticated HTTPS endpoint, scale-to-zero idle cost, and native OIDC with Cloud Scheduler.
- **Cloud Scheduler → OIDC → `/internal/autonomous-sweep`** — unattended, every thirty minutes. The route verifies signature, audience, issuer, and the exact invoker service account; it accepts no API key. Why not Pub/Sub or Cloud Tasks? Because what we need is cron, not a queue. The OIDC token proves the caller without a shared secret, and the schedule slot doubles as the idempotency key.
- **Gemini 3.7 Flash** — question drafts and per-candidate relevance judgments. Why not a Pro-class model? Candidate isolation means up to twenty small, structured judgments per sweep. What matters is latency and unit cost — and safety comes from fail-closed score validation, not model depth.
- **gemini-embedding-2 on GEAP** — profile search; the 0.62 similarity floor is calibrated for this model. Why not the direct Gemini API? ADC/IAM auth keeps API keys out of code and environment, and inference stays inside the same project, the same governance, as the data.
- **Firestore (native)** — profiles ×400, agents, tasks, cards, autonomy policies, sweep-run claims. And `messages`, the append-only audit log. Why not Cloud SQL or AlloyDB? A profile is a per-person document — items and visibility differ person by person — so the document model iterates without schema migrations, transactions give the CAS the idempotency claims need, and tenants map to named databases.
- **GEAP Agent Runtime (Agent Engine, ADK)** — the secretary also runs as a first-class managed agent. The scheduled operation hits the same sweep API deterministically; the dialogue agent is read-only; six agents sit in the GEAP Agent Registry. Why not hand-roll the loop on Cloud Run? Governance. Register the agent as a first-class platform citizen — and deliberately keep delivery authority out of its hands.
- **323 offline tests** — every external service sits behind an interface. Deterministic fakes; the suite runs with no network and no credentials.

Other data sources: none. The company, the 401 people, the profiles, the tasks, the mail — all generated by a script (the fictional Meridian Care Partners Group). Embeddings are computed on GEAP at seed time. Not one byte of real data.

## The security story (why "Fortified")

Approval is structural.

- The human approval boundary. `contact_mode` is fixed to `always_ask`, enforced server-side. The agent observes, detects, explores, evaluates, prepares — and stops. The code path that doesn't stop was never written.
- Autonomy comes in grades: Monitor ⊇ Search ⊇ Ask ⊇ Prepare. Switch off an upper permission and everything below it stops, structurally. Normalized on save. The client's word is never trusted.
- Privacy is enforced by the type system, not by the model's self-report. Private items are masked fail-closed off the real `visibility` field. A model that lies about what it cited can unmask nothing.
- Data boundary = process boundary. Candidates are evaluated one by one, each in its own isolated inference. Cross-candidate leakage isn't policed — it's impossible.
- The audit respects privacy too. Before approval: counts only — no names, no free text. Names appear after a human says yes. And delivery is never authorized by an LLM score alone: missing, non-numeric, out of range — all fail closed to "no connection" (our own red team hit this spot; we hardened it).
- Honestly: the demo runs on one shared key, so a judge can drive the whole flow from a single URL. The production path — IAP, per-employee principals, horizontal-authorization guards — is implemented and tested. What remains is one deployment flag.

## Challenges we ran into

Making autonomy provable, not promised: idempotent scheduled sweeps, fail-closed OIDC, an audit trail that separates counts from names — all of it serves that one goal.
Letting an agent use private knowledge without leaking it. Relevance gets confirmed; content never moves. This consent flow was the hardest thing we built.
A UI where the power balance is visible. Humans large, agents small — and a "not sent" that cannot be mistaken for "sent."

## Accomplishments that we're proud of

The no-click demo: a stalled task becomes a prepared introduction with zero human input — and the system still cannot contact anyone.
Declining as a first-class feature: quiet, invisible, and generous if you want it to be — share a resource instead.
A multi-agent LLM system whose entire test suite runs offline.

## What we learned

The hard part of enterprise agents wasn't capability. It was where to stop. Everything valuable in this system is made of boundaries: what the sweep may touch, what the audit may record, what a candidate's agent may reveal — and the one click no machine is ever allowed to make.

That lesson is not theoretical. We hammered the design through fifteen adversarial critique rounds across two vendors' models, then had three independent auditors red-team the result before submission. They found a real hole — a path where a missing LLM score could authorize delivery. Fail-closed stopped being a philosophy and became a regression test.

## What's next

- Real data-source connectors — calendar, mail, docs — behind the same visibility model. The interfaces already exist.
- Per-employee identity in production. IAP mode is implemented and tested; what remains is one deployment flag.
- Long-term agent memory: with GEAP Memory Bank, the secretary's picture of your expertise deepens over time.
- Interop: expose the secretary over A2A/MCP, so other agents can request introductions through the same consent and audit boundary. Agentspace/Spark as they mature.

---

*Built with Gemini 3.7 Flash, gemini-embedding-2, Cloud Run, Cloud Scheduler, Firestore, and GEAP Agent Runtime. Fictional company (Meridian Care Partners Group, 401 employees) — all data synthetic.*
