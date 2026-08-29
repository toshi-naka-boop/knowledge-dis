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

## Authentication (design v15 §16.1)

`AUTH_MODE` selects how each request's `Principal` (`mode: demo|human|system`,
`tenant_id`, `employee_id`, `email`) is resolved. Every `/api/*` route enforces
a default-deny permission table over this Principal (design.md §16.1). Two
exceptions carry no authentication at all, unchanged from before Part A: the
static UI shells (`/`, `/requester`, `/candidate`, `/audit` — they contain no
data of their own) and `GET /attachments/{id}` (pre-placed demo documents).
Both modes below resolve `tenant_id` from the tenant ledger (see "Tenants"
below, design.md §16.2) rather than a fixed constant.

- **`AUTH_MODE=demo_key` (default)**: unchanged from the pre-Part-A behavior,
  except the tenant is now whichever tenant's key matched. `X-API-Key` header
  or `api_key` query parameter must match one tenant's key in the ledger;
  the request resolves to `mode=demo` bound to that tenant, and each API
  trusts the identity it is given in the request body/path (single shared
  key *per tenant*, single actor plays all personas within it — see
  "Demo-mode simplifications" below).
- **`AUTH_MODE=iap`**: for Cloud Run behind Identity-Aware Proxy
  (`--no-allow-unauthenticated`, IAP enabled on the service). Requests must
  carry `X-Goog-IAP-JWT-Assertion`; it is verified with
  `google.oauth2.id_token.verify_token` (ES256, IAP's public key endpoint,
  `clock_skew_in_seconds=30`, issuer `https://cloud.google.com/iap`, `email`
  claim required). The verified email resolves to a Principal via the tenant
  ledger:
  - email listed in some tenant's `system_accounts` (e.g. that tenant's
    Scheduler job's OIDC identity) -> `mode=system`, bound to that tenant
  - email's domain listed in some tenant's `email_domains`, and the email is
    registered in that tenant's own `identities` collection (seeded by
    `scripts/generate_seeds.py --database <tenant's database>`) -> `mode=human`,
    `employee_id` resolved from that tenant's identity record
  - otherwise -> `403`
  - `IAP_AUDIENCE` (Cloud Run IAP format:
    `/projects/<PROJECT_NUMBER>/locations/<REGION>/services/<SERVICE>`) is
    required and format-checked at startup only when `AUTH_MODE=iap`. API
    keys are not accepted in this mode — machine callers (Cloud Scheduler)
    must also go through IAP via an OIDC token.
  - The IAP public key is cached in-process (~1h, Cache-Control aware); a
    failed refetch keeps serving the previous key for one more window before
    failing closed with `401` (never a silent `503`).

`GET /api/me` returns the resolved `{mode, tenant_id, employee_id}` and is
used by `requester.html`/`candidate.html` to hide the demo persona switcher
once `mode != demo`.

## Tenants (design v15 §16.2, Part B)

Each tenant is a separate Firestore **database** under the same GCP project —
not a separate process, and not a query-time filter. A request only ever sees
`ContextRouter.for_tenant(principal.tenant_id)`'s own `Store`; there is no
`X-KD-Tenant` header, no all-tenant API key, and no code path that queries
across tenants. **Blast radius**: a leaked tenant API key (or a compromised
IAP identity bound to that tenant) exposes that tenant's data only — every
other tenant is untouched. Firestore Security Rules are not part of this
boundary (they don't apply to the server's own client); the boundary is
"every route only ever asks for its own tenant's context, and there is no
function that returns another tenant's".

**Ledger** — env `TENANTS_JSON`, a JSON array; unset defaults to today's
single-tenant setup (`meridian` / `(default)` / `meridian-care.example` /
`DEMO_API_KEY`):

```json
[
  {
    "tenant_id": "meridian",
    "database": "(default)",
    "email_domains": ["meridian-care.example"],
    "api_key_env": "DEMO_API_KEY",
    "system_accounts": ["kd-scheduler-sa@<PROJECT_ID>.iam.gserviceaccount.com"]
  },
  {
    "tenant_id": "acme",
    "database": "kd-tenant-acme",
    "email_domains": ["acme.example"],
    "api_key_env": "ACME_API_KEY",
    "system_accounts": []
  }
]
```

Each row needs `api_key_env` (the name of an env var holding the key, so keys
live in Secret Manager / the local shell environment, never in the ledger
itself as a literal value — round-14 E-13). Startup fails closed if
`tenant_id`, `database`, the resolved `api_key`, or any `email_domains` /
`system_accounts` entry (compared case-insensitively) repeats across tenants.

- **API keys are per-tenant** (§16.2 C-42/W-2): `demo`/`system` principals are
  bound to whichever tenant's key matched theirs; there is no key that spans
  tenants. Cloud Scheduler jobs and the B-stage Agent Runtime are one set
  *per tenant* (each Runtime's `KD_API_KEY` env holds that tenant's key).
- **Process-lifetime cache**: `ContextRouter` builds each tenant's
  Store/Service/Secretary lazily, on first request, and caches them for the
  life of the process. Editing `TENANTS_JSON` only takes effect after a
  restart (`gcloud run services update ... --update-env-vars` triggers one);
  there is no live cache-invalidation endpoint.
- **Second database (real Firestore) — create, seed, verify, delete**:

  ```bash
  # 1. create the database (one-time)
  gcloud firestore databases create --database=kd-tenant-b --location=asia-northeast1 --type=firestore-native

  # 2. seed it (identities included) via --database
  GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=<PROJECT_ID> PYTHONPATH=src \
    .venv/bin/python scripts/generate_seeds.py --use-firestore --project <PROJECT_ID> \
    --database kd-tenant-b --embedder gemini

  # 3. add the tenant to TENANTS_JSON, redeploy/restart the service, and confirm
  #    with that tenant's key that /api/agents, /api/secretary/digest, etc. only
  #    ever return kd-tenant-b's data (goal 25's real-Firestore smoke check)

  # 4. delete the verification database when done
  gcloud firestore databases delete --database=kd-tenant-b --quiet
  ```

## Data source connectors (design v15 §16.3)

`SOURCE_CONNECTOR` selects what the secretary's sweep-time sync step (§14.7 /
§16.3 "sync-then-detect") reads from before stagnation detection runs:

- **`SOURCE_CONNECTOR=seed` (default)**: a no-op. The demo/offline-test data
  already lives directly in `Store` (seeded by `scripts/generate_seeds.py`);
  nothing is fetched or reconciled, and switching between `seed` and
  `google_workspace` can never affect previously-synced `gws` data (a
  `SeedConnector` sweep never touches Store's `gws` records at all).
- **`SOURCE_CONNECTOR=google_workspace`**: pulls Tasks / Calendar / Gmail over
  REST using the author's own ADC (read-only scopes). **`GWS_SELF_EMPLOYEE_ID`
  is required** — there is no per-owner credential (Domain-Wide Delegation is
  a future item), so this connector only ever supports single-owner mode: the
  one employee_id it syncs. If `SOURCE_CONNECTOR=google_workspace` is set
  without `GWS_SELF_EMPLOYEE_ID`, the sweep never fetches anything (fail
  closed) and records a configuration error in `sync_errors` on every sweep
  instead of guessing an owner.
  - `GWS_GMAIL_ENABLED=false` (default): Gmail is opt-in. When `true`, only
    messages the owner has labelled `kd-secretary` are read (`GWS_GMAIL_DAYS`,
    default 7; `GWS_GMAIL_MAX_RESULTS`, default 20; `GWS_MAIL_BODY_CHARS`,
    default 2000).
  - `GWS_CAL_DAYS_AHEAD` (default 3): the Calendar lookahead window for
    `meeting_prep` reminders.
  - Author-ADC login: `gcloud auth application-default login --scopes=cloud-platform,tasks.readonly,calendar.readonly[,gmail.readonly]`.
  - `PYTHONPATH=src .venv/bin/python scripts/gws_probe.py --owner <employee_id> [--apply-to-memory]`
    prints fetch counts (and, with `--apply-to-memory`, a real
    `run_sweep()`/`get_morning_digest()` result against a throwaway empty
    `InMemoryStore`) — counts and kinds only, never titles/subjects/bodies.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests   # ~200 tests, no network/credentials needed (13 B-stage tests skip without google-adk)
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

## Autonomous sweep (design/autonomous-agent v4)

A third job, `kd-autonomous-sweep`, fires `POST /internal/autonomous-sweep` every
30 minutes with an **OIDC identity token** (no API key accepted on this route):

```bash
gcloud scheduler jobs create http kd-autonomous-sweep \
  --location=asia-northeast1 --schedule="*/30 * * * *" --time-zone=Asia/Tokyo \
  --uri="https://<SERVICE_URL>/internal/autonomous-sweep" --http-method=POST \
  --oidc-service-account-email=kd-scheduler-sa@<PROJECT_ID>.iam.gserviceaccount.com \
  --oidc-token-audience="https://<SERVICE_URL>" --attempt-deadline=180s
```

The endpoint requires env `AUTONOMOUS_SWEEP_AUDIENCE` (= service URL) and
`AUTONOMOUS_SWEEP_INVOKER` (= scheduler SA email) on the Cloud Run service; when
unset it fail-closes with 404. All HTTP-triggered sweeps default to
`origin="scheduled"` and are gated per-user by the Autonomy Policy (the UI's
"Run sweep" button alone sends `origin="manual"`, the ungated override). Run-level
idempotency is keyed on the Cloud Scheduler schedule slot, so duplicate deliveries
— and forced `jobs run` inside an already-claimed slot — return `deduplicated:true`
without re-executing.

**Reseed vs scheduler:** pause the job around a reseed to keep the trace clean
(cards are idempotent either way):

```bash
gcloud scheduler jobs pause  kd-autonomous-sweep --location=asia-northeast1   # before reseed
gcloud scheduler jobs resume kd-autonomous-sweep --location=asia-northeast1   # after reseed
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

Demo-mode notes: (1) the session `user_id` is asserted by the caller — anyone allowed to
query the Runtime can name any user_id; the tool simply never lets the LLM pick a different
employee (same single-key simplification as `/api/secretary/*`; production maps agent
identity/IAM to employees). (2) Runtime Sessions keep each user_id's digest summaries;
tracing is left disabled. (3) `kd-scheduler-sa` holds only a custom role with
`aiplatform.reasoningEngines.query/get`.

Teardown after the demo (stops all Runtime cost):

```bash
gcloud scheduler jobs delete kd-autonomous-sweep --location=asia-northeast1 --quiet
gcloud scheduler jobs delete kd-secretary-sweep-runtime --location=asia-northeast1 --quiet
curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://asia-northeast1-aiplatform.googleapis.com/v1/projects/<PROJECT_ID>/locations/asia-northeast1/reasoningEngines/<ID>"
```

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
