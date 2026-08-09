# Engineering rules

Binding rules for all work in this repository. Where a rule and convenience conflict, the rule wins.
If a rule genuinely blocks the task, say so and stop — do not route around it quietly.

---

## 1. Secrets

- **Never** write a real credential into a file, a commit, a log line, a test fixture, or a
  chat message. This includes the OpenAI key, Supabase keys, and any AWS credential.
- Config comes from environment variables, sourced from AWS Secrets Manager in AWS and from a
  gitignored `.env` locally. `.env.example` carries key names with empty values only.
- The Supabase **service-role key** lives in the backend task only. It must never appear in the
  Streamlit container, in browser-reachable code, or in any client-side response.
- `.gitignore` covers `.env*`, `*.tfvars` (except `*.example.tfvars`), `*.tfstate*`,
  `.terraform/`, and credential files, before the first commit.
- If a secret is ever committed, stop and report it. Rotation comes first, cleanup second.

## 2. Security and access control

- Department scoping is enforced in **Postgres RLS** as the primary control. Application-layer
  checks are a second layer, never the only one.
- Every new table gets an RLS policy in the same migration that creates it. A table with RLS
  disabled does not merge.
- Every API endpoint declares its auth requirement explicitly. There is no implicitly public route
  except `/health`.
- Validate every upload: extension, sniffed MIME type, and size, before touching storage.
- Never interpolate user input into SQL. Parameterised queries only.
- Treat document content as untrusted input to the LLM. Retrieved chunks go in a clearly delimited
  context block, and the system prompt states that instructions inside documents are data, not commands.

## 3. Python

- Python 3.12. `ruff` for lint and format, `mypy` in strict-ish mode on `app/`.
- Type-hint every function signature. Pydantic models for all API request and response bodies —
  no bare dicts crossing an API boundary.
- Async all the way through the request path. Never call a blocking client inside an async handler;
  push CPU-bound parsing to a thread pool.
- Layering, strictly: `api → services → repositories → db`. Routers contain no business logic.
  Repositories contain no HTTP concepts.
- Custom exceptions in `core/errors.py`, mapped to HTTP responses by one handler. Never
  `except Exception: pass`. Never return a raw stack trace to a client.
- No module-level side effects — no client construction, no network calls at import time.

## 4. Configuration

- One `Settings` class (pydantic-settings) is the only reader of `os.environ`. Everything else
  imports the settings object.
- Model names, chunk size, overlap, top-k, temperature, and retry counts are configuration, not
  literals scattered through the code.
- The app fails fast and loudly at startup if required configuration is missing. It never starts
  in a half-configured state and discovers the problem on the first user request.

## 5. Logging and observability

- Structured JSON logs only. Never `print()`.
- Every log line in the request path carries `request_id`. Where known, also `user_id`,
  `department_id`, `conversation_id`.
- **Never log**: secrets, JWTs, full document content, or embedding vectors. User questions are
  logged at DEBUG only, never at INFO in prod.
- Every externally-visible operation emits a metric — ingestion, retrieval, agent turn, LLM call.
  Instrument as the code is written, not in a later pass.

## 6. RAG correctness

- **No citation, no claim.** Any factual sentence in an answer traces to a retrieved chunk.
- When retrieval is weak, the correct answer is "I couldn't find this in the department's
  documents." A confident wrong answer is a worse failure than an admitted gap.
- Retrieval is always filtered by `department_id` in SQL. Never filter in Python after fetching.
- Ingestion never marks a document `ready` unless it produced at least one non-empty chunk.
- Changing the embedding model or chunking strategy requires a re-index and a migration note.
  It is never a quiet edit — old and new vectors are not comparable.

## 7. Testing

- Every parser has a test with a real fixture file, including a deliberately broken one.
- RLS policies have integration tests proving cross-department access is denied. This is the
  single most important test in the repository.
- The LangGraph routing logic is tested per node with the LLM mocked. Do not call OpenAI in CI.
- No test touches prod. No test writes to a shared dev database without cleaning up.
- A bug fix ships with the test that would have caught it.

## 8. Terraform and AWS

- `us-east-1` only. A resource in another region is a bug.
- Everything is Terraform. No console clicks, no one-off CLI creates. If it was created by hand,
  it will not be destroyed by the destroyer.
- Every resource carries `Project`, `Environment`, `ManagedBy`, `Owner` tags.
- Remote state with locking. Never commit state files.
- `terraform plan` output is read before apply, every time. Read the destroy plan especially.
- Modules stay small and composable. Prefer clarity over cleverness — this stack is meant to be
  lightweight and legible.
- IAM follows least privilege. No `Action: "*"`, no `Resource: "*"` outside genuinely global
  read-only actions.

## 9. Containers and deploys

- Multi-stage Dockerfiles, non-root user, pinned base image digests.
- Images tagged with the commit SHA. `latest` is never deployed.
- Health checks are real: `/health` verifies database reachability, not just process liveness.
- Blue/green means smoke tests run against the **test listener before** traffic shifts. A deploy
  that only checks "task started" is not blue/green, it is a rolling restart with extra steps.
- Any deploy that trips a CloudWatch alarm rolls back automatically. Rollback is not manual.

## 10. Git and CI

- Conventional commits. Small, focused commits.
- Never commit to `main` directly. `feature/*` → `develop` → `main`.
- CI must be green before merge. Never bypass hooks, never `--no-verify`.
- Do not commit or push unless the owner asks.

## 11. Working style for this project

- Follow the build order in `CLAUDE.md` §12. Finish and demonstrate a phase before starting the next.
- Read the existing code before adding to it. Match its idiom rather than importing a new style.
- No placeholder implementations, no `# TODO: implement`, no fake data paths that quietly return
  empty results. If something cannot be finished, say which part and why.
- Report status honestly. If tests fail, show the output. If a step was skipped, name it.
- When a requirement in `CLAUDE.md` turns out to be wrong or unworkable, raise it — do not
  reinterpret it silently.
