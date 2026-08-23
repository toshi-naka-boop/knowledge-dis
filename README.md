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

## Secretary sweep trigger (Cloud Scheduler, design v9 §14.7 A-stage)

The proactive secretary (`POST /api/secretary/sweep`) is fired daily by a Cloud
Scheduler HTTP job that carries the demo API key as a header (the service stays
`--allow-unauthenticated`; the endpoint itself returns 401 without the key).
Production would use OIDC + a dedicated invoker SA (write-up future item).

```bash
gcloud scheduler jobs create http kd-secretary-sweep \
  --location=asia-northeast1 --schedule="0 8 * * *" --time-zone=Asia/Tokyo \
  --uri="https://<SERVICE_URL>/api/secretary/sweep" --http-method=POST \
  --headers="X-API-Key=<DEMO_API_KEY>" --attempt-deadline=180s

gcloud scheduler jobs run kd-secretary-sweep --location=asia-northeast1   # manual fire
```

## B-stage: secretary on GEAP Agent Runtime (design v11 §14.7)

The secretary also runs as a first-class agent on **GEAP Agent Runtime** (Vertex AI
Agent Engine, `reasoningEngines/4310793666370207744`, asia-northeast1). Two strictly
separated entry points:

- **Scheduled trigger = deterministic operation (no LLM).** `run_daily_sweep` is a
  registered Agent Engine operation that calls Cloud Run `POST /api/secretary/sweep`
  and returns its JSON. A non-2xx from Cloud Run raises, so the `:query` response is
  non-2xx and Cloud Scheduler records a failure (failures are never silenced). The
  A-stage job keeps running in parallel (sweep is idempotent) as a safety net.
- **Dialogue = read-only LLM agent.** `LlmAgent` (Gemini 3.7 Flash, model endpoint
  pinned to `global`) with a single tool `get_my_digest` that is scoped to the
  session's own `user_id` (no employee_id argument). `run_daily_sweep` and every
  write operation (confirm / dismiss / review) are deliberately **not** LLM tools, so
  the Runtime secretary has no delivery authority.

Detection, state machine, preview search and confirm all stay on Cloud Run; the
Runtime agent only orchestrates and converses. Package: `src/secretary_agent/`
(independent of `knowledge_discovery`; deps pinned in `scripts/requirements-agent.txt`).

```bash
# B-stage env (pinned deps) and tests (skip-0 here; skipped in the main venv)
python3 -m venv .venv-agent && .venv-agent/bin/pip install -r scripts/requirements-agent.txt
.venv-agent/bin/python -m unittest tests.test_secretary_agent

# deploy / update (ADC; Reasoning Engine service agent needs secretAccessor on demo-api-key)
GOOGLE_CLOUD_PROJECT=<PROJECT_ID> .venv-agent/bin/python scripts/deploy_secretary_agent.py \
  --project <PROJECT_ID> --location asia-northeast1 \
  --staging-bucket gs://<PROJECT_ID>-agent-staging \
  --api-base-url https://<SERVICE_URL> [--update projects/<N>/locations/asia-northeast1/reasoningEngines/<ID>]

# scheduled trigger (OAuth as kd-scheduler-sa with roles/aiplatform.user; JSON body)
gcloud scheduler jobs create http kd-secretary-sweep-runtime --location=asia-northeast1 \
  --schedule="55 7 * * *" --time-zone=Asia/Tokyo --http-method=POST \
  --uri="https://asia-northeast1-aiplatform.googleapis.com/v1/projects/<PROJECT_ID>/locations/asia-northeast1/reasoningEngines/<ID>:query" \
  --headers="Content-Type=application/json" --message-body='{"class_method":"run_daily_sweep","input":{}}' \
  --oauth-service-account-email=kd-scheduler-sa@<PROJECT_ID>.iam.gserviceaccount.com \
  --attempt-deadline=180s --max-retry-attempts=3

# talk to the secretary as an employee (SDK)
#   agent_engines.get(<resource>).stream_query(user_id="emp_jordan_lee", session_id=..., message="What's on my plate today?")
```

**Agent Registry.** The five Cloud Run agents (4 personal agents + A-stage secretary)
and the Runtime secretary are registered in Agent Registry (asia-northeast1) as
`no-spec` / `http-json` services with capability descriptions
(`gcloud alpha agent-registry services create ...`, listed via
`gcloud alpha agent-registry agents list --location=asia-northeast1`).

Operational note (verified 2026-08-23): with default Runtime limits the replica restarted its
workers on every request and Vertex answered `400 FAILED_PRECONDITION / Service Unavailable`
to Cloud Scheduler even though the sweep had completed on Cloud Run. Two changes fixed it:
`run_daily_sweep` is an async operation that runs the blocking HTTP call in a thread, and the
deployment pins `resource_limits cpu=4/memory=8Gi`, `min_instances=1/max_instances=2`,
`container_concurrency=4` (all set by `scripts/deploy_secretary_agent.py`). After that a
Scheduler fire succeeds in one attempt (HTTP 200, one sweep). Avoid hammering the Runtime
concurrently in tests; the production schedule fires once a day.

Demo-mode note: Runtime Sessions keep each employee's own digest summaries under
their own `user_id` (owner-scoped); tracing is left disabled.

## Demo reset & recording-day procedure

Seed dates are relative to a base date; the server evaluates "today" from
`DEMO_TODAY` (ISO date, falls back to the real UTC date). To reset for a recording:

```bash
# 1. wipe everything (agents/profiles/messages/tasks/schedules/mail_seeds/cards) and reseed
GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=<PROJECT_ID> GOOGLE_CLOUD_LOCATION=global PYTHONPATH=src \
  .venv/bin/python scripts/generate_seeds.py --use-firestore --project <PROJECT_ID> \
  --embedder gemini --clear --today YYYY-MM-DD

# 2. pin the same date on the service
gcloud run services update knowledge-discovery --region=asia-northeast1 --update-env-vars DEMO_TODAY=YYYY-MM-DD

# 3. run one sweep (or wait for the Scheduler job) so the digest cards exist
```

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
