# Knowledge Discovery — Tacit-Knowledge Connection Engine

A multi-agent system where every employee's personal AI agent helps answer
*"who in our company might know about this?"* — not by delivering a document,
but by proposing a real human connection. Built for the All Things Agentic
Hackathon (track: The Fortified Enterprise Fleet).

**Core idea**: CRMs record accounts inside one business unit. The knowledge that
crosses business units — "who has actually seen a clinic relocate before a
sale?" — lives in people's heads and never lands in Salesforce, because it is
made of confidences, relationships and case nuance. This system does
*exploratory* matching over that tacit layer ("this person **might** know,
given their background") and always resolves to a human connection: a 15-minute
meeting proposal, or an honest decline with a reason and optional resources.

## Architecture (governance three-in-one)

| Concern | This implementation | Enterprise (GEAP) equivalent |
|---|---|---|
| Who exists (agent discovery) | `agents` registry (Firestore) | GEAP Agent Registry |
| What may flow (control) | Schema registry + `supported_intents` checks in the transmission layer | Model Armor |
| What did flow (audit) | `messages` collection + chat-style audit view | Agent Observability |

Key properties:

- **Candidate-isolated inference**: each candidate's agent sees ONLY the
  question and its own profile (data boundary = process boundary).
- **Private items**: things an owner deliberately keeps off the public profile.
  Only the owner's agent knows them; when a question relates, the owner alone
  gets a discreet ask. The audit log records that a private-based ask happened —
  never its content. Masking is decided by the type system (item visibility +
  content scan), not by the LLM's self-report.
- **Honest non-matches**: the engine is allowed to say "no meaningful
  connection" (deterministic vector floor OR LLM null), and dropped candidates
  appear in the audit view with their reasons.

Stack: Python / FastAPI on Cloud Run · Firestore (native) · Gemini 3.7 Flash +
gemini-embedding-2 via **Vertex AI** · google-genai SDK.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r scripts/requirements.txt

# Google Cloud auth (Firestore + Vertex AI)
gcloud auth application-default login
gcloud auth application-default set-quota-project <PROJECT_ID>

# Seed 4 personas + 396 synthetic employees (embeddings via Vertex)
GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=<PROJECT_ID> PYTHONPATH=src \
  .venv/bin/python scripts/generate_seeds.py --use-firestore --project <PROJECT_ID> --embedder gemini

# Serve
USE_FIRESTORE=1 GOOGLE_CLOUD_PROJECT=<PROJECT_ID> GOOGLE_GENAI_USE_VERTEXAI=true \
  DEMO_API_KEY=<your-demo-key> VECTOR_FLOOR=0.62 PYTHONPATH=src:. \
  .venv/bin/uvicorn 'knowledge_discovery.server:create_app_from_env' --factory --port 8080
```

Open `http://localhost:8080/requester?api_key=<your-demo-key>` (also
`/candidate`, `/audit`). All API and UI access requires the `api_key`.

`VECTOR_FLOOR=0.62` is calibrated for gemini-embedding-2 (unrelated profiles
score ~0.59 in that space; the default 0.20 suits only the offline test
embedder).

## Tests

```bash
.venv/bin/python -m unittest discover -s tests   # 64 tests, no network/credentials needed
```

All external services (Firestore, Gemini) sit behind interfaces with in-memory
/ deterministic fakes; the suite runs fully offline.

## Deploy (Cloud Run)

```bash
gcloud run deploy knowledge-discovery --source . \
  --region=asia-northeast1 \
  --service-account=<runtime-sa> \
  --allow-unauthenticated --min-instances=0 --memory=512Mi \
  --set-env-vars="USE_FIRESTORE=1,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=<PROJECT_ID>,VECTOR_FLOOR=0.62" \
  --set-secrets="DEMO_API_KEY=demo-api-key:latest"
```

The runtime service account needs only `roles/datastore.user` and
`roles/aiplatform.user` (plus `secretmanager.secretAccessor` on the demo-key
secret). Firestore is created in native mode via gcloud and has no client-SDK
rules deployed — client SDK access is denied by default; every read/write goes
through the server, which applies the requester/audit projections.

## Demo-mode simplifications (deliberate)

- Single shared demo API key; the candidate screen has a persona switcher so
  one operator can play all four roles. Per-user auth is out of scope by spec.
- `POST /api/probe/unregistered-intent` exists solely to demonstrate the
  transmission-layer rejection (red row in the audit view) live.
- `/api/secretary/*` (digest/confirm/dismiss/profile-diff review) does not
  verify that the caller is the owning employee_id — it trusts the single
  shared demo API key, same as `/api/query`'s existing `requester_id` param.
  Anyone with the demo URL can read another persona's digest by swapping
  `employee_id`. Out of scope by spec (M3, same posture as the persona
  switcher above).
