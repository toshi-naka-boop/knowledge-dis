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

The demo is live on Cloud Run: `/requester` is Jordan's side, `/candidate` is Marcus's letters, `/audit` is Bridge Trace. Every page takes an `api_key` in the URL; the full keyed link is provided in this submission's testing instructions, so you can drive the whole flow — request, approve, respond, audit — from one link. All data is synthetic; click anything.

## How we built it

In the demo, "Jordan" is a healthcare-staffing manager, and the out-of-field request is a clinic relocation — a real-estate question. The colleague who had done it twice sat on a different island entirely.

**Gemini + Google Cloud end to end** (project `knowledge-discovery-2026`, asia-northeast1):

- **Cloud Run** — FastAPI service: agent-to-agent messaging, the secretary pipeline, three UIs (My Agent / Connection Request / Bridge Trace).
- **Cloud Scheduler → OIDC → `/internal/autonomous-sweep`** — the fleet runs unattended every 30 minutes. The endpoint verifies signature, audience, issuer, and the exact invoker service account; no API key is accepted on this route. Slot-keyed idempotency makes duplicate deliveries no-ops.
- **Vertex AI — Gemini 3.7 Flash** (question drafts, per-candidate evaluation) and **gemini-embedding-2** (profile search, similarity floor 0.62).
- **Firestore (native)** — profiles ×400, agents, tasks, cards, autonomy policies, sweep-run claims, and `messages` — the append-only audit log.
- **Vertex AI Agent Engine (GEAP Agent Runtime)** — the secretary also runs as a first-class managed agent (ADK): a deterministic scheduled operation triggers the same sweep API, and a read-only LLM dialogue agent answers "what's on my plate today?" with a single self-scoped tool. Six agents are registered in the GEAP Agent Registry.
- **323 offline tests** — every external service sits behind an interface with deterministic fakes; the suite runs with no network and no credentials.

Other data sources: none. The company, its 401 employees, their profiles, tasks, and mail seeds are all synthetic, generated by a deterministic script (fictional Meridian Care Partners Group); profile embeddings are computed from that synthetic corpus via Vertex AI at seed time. No real or external data is used.

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
