# Agentic RAG for Company — Project Charter

Department-scoped document intelligence platform. Admins upload documents per department;
users with access to that department chat with those documents and get cited, grounded answers.

**Status: SPEC ONLY — no code written yet. Awaiting owner approval of this document.**

@.claude/rules/engineering-rules.md

---

## 1. Locked decisions

These were decided by the project owner. Do not silently change them.

| Area | Decision |
|---|---|
| Backend | FastAPI (Python 3.12) |
| Frontend | Streamlit — **one app**, role-gated admin vs user surface |
| Agent framework | LangGraph |
| LLM | OpenAI `gpt-4o-mini` (config-driven, upgradable per department) |
| Embeddings | OpenAI `text-embedding-3-small` — **1536 dims, baked into schema** |
| Database / vectors / storage / auth | Supabase (Postgres + pgvector + Storage + Auth) |
| Cloud | AWS, **`us-east-1` only** |
| Compute | ECR + ECS Fargate |
| Deployment | **CodeDeploy ECS blue/green**, ALB with two target groups, alarm-triggered rollback |
| Environments | `dev` and `prod` |
| IaC | Terraform — simple and lightweight, plus a one-command destroyer |
| Secrets | AWS Secrets Manager |
| Observability | CloudWatch (logs, metrics, alarms, dashboards) |
| CI/CD | GitHub Actions, OIDC to AWS (no long-lived AWS keys) |
| Doc parsing | Pure-Python parsers only; image-only PDFs are **failed loudly**, never silently indexed |

### Assumptions to overturn now if wrong

1. **Two Supabase projects**, one per environment. Dev and prod never share a database.
2. **One AWS account**, environments separated by naming, tagging, and Terraform workspace/dir.
3. **No custom domain yet.** Dev runs HTTP on the ALB DNS name. Prod blue/green is built
   ready for HTTPS but needs a domain + ACM certificate before it should carry real traffic.
4. Max upload size **50 MB** per file.
5. Ingestion runs **in-process** in the API container (see §5.3). No SQS/worker service yet.

---

## 2. Functional requirements

### 2.1 Admin console
- Create users: email + password, assign role (`admin` | `user`), assign one or more departments.
- Deactivate/reactivate users; revoke department access.
- Create, rename, and archive departments.
- Upload documents into a department. Supported: PDF, DOCX, XLSX/XLS, CSV, PPTX, TXT, MD, JSON, HTML.
- See per-document ingestion status: `pending → processing → ready | failed`, with the failure
  reason in plain language, page/chunk counts, and a re-process button.
- Delete a document — removes storage object, rows, and all its chunks.
- Chat with any department's documents (admins are not department-restricted).
- View an audit log of who did what.

### 2.2 User app
- Log in with credentials issued by an admin.
- See only the departments they were granted. Department is an explicit selector, not a guess.
- Chat with that department's documents.
- Full conversation history, persisted and resumable; follow-up questions resolve against
  earlier turns ("summarise it", "what about last year's?").
- Every answer shows citations: document name, page/sheet/section, and the exact snippet used.
- Rename, archive, and delete their own conversations.

### 2.3 Non-negotiable behaviours
- A user must never retrieve a chunk from a department they lack access to. Enforced in
  **Postgres RLS**, not only in application code.
- No citation → no claim. If retrieval is weak, the answer is "I couldn't find this in the
  department's documents", not a guess.
- Ingestion failures are visible in the admin console. A document that yielded no text is
  `failed`, never `ready`.

---

## 3. Repository layout

```
agenticRAGforcompany/
├─ CLAUDE.md
├─ README.md
├─ Makefile                       # every operational command lives here
├─ docker-compose.yml             # local dev: api + ui
├─ .claude/
│  ├─ rules/engineering-rules.md
│  └─ skills/{infra-ops,rag-pipeline}/SKILL.md
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ api/v1/{health,auth,departments,documents,chat,admin}.py
│  │  ├─ core/{config,security,logging,metrics,deps,errors}.py
│  │  ├─ db/{client,schemas,repositories}/
│  │  ├─ ingestion/{loaders,chunking,embedding,pipeline,worker}.py
│  │  ├─ retrieval/{hybrid,rerank,fusion}.py
│  │  ├─ agent/{graph,state,nodes,tools,prompts}.py
│  │  └─ services/
│  ├─ tests/{unit,integration,fixtures}/
│  ├─ Dockerfile
│  └─ pyproject.toml
├─ frontend/
│  ├─ app.py                      # login + router
│  ├─ pages/{1_chat,2_history,90_admin_users,91_admin_departments,92_admin_documents,93_admin_audit}.py
│  ├─ lib/{api_client,auth,state,render}.py
│  ├─ Dockerfile
│  └─ pyproject.toml
├─ infra/terraform/
│  ├─ bootstrap/                  # S3 state bucket + DynamoDB lock table
│  ├─ modules/{network,ecr,alb,ecs_service,codedeploy,secrets,observability,iam}/
│  └─ envs/{dev,prod}/{main.tf,variables.tf,terraform.tfvars,backend.tf}
├─ supabase/migrations/           # ordered, forward-only SQL
├─ scripts/{bootstrap.sh,deploy.sh,destroy.sh,seed.py,smoke_test.py}
└─ .github/workflows/{ci,cd-dev,cd-prod,terraform-plan,destroy}.yml
```

---

## 4. Data model (Supabase Postgres)

All tables carry RLS. `department_id` is denormalised onto `document_chunks` so the vector
search filters without a join.

| Table | Purpose / key columns |
|---|---|
| `departments` | `id, name, slug, description, is_active, created_at` |
| `profiles` | `id → auth.users, email, full_name, role, is_active, created_at` |
| `user_departments` | `user_id, department_id, granted_by, granted_at` (PK on the pair) |
| `documents` | `id, department_id, filename, storage_path, mime_type, size_bytes, checksum_sha256, status, error_message, page_count, chunk_count, uploaded_by, created_at, processed_at, metadata jsonb` |
| `document_chunks` | `id, document_id, department_id, chunk_index, content, token_count, embedding vector(1536), tsv tsvector, metadata jsonb` |
| `conversations` | `id, user_id, department_id, title, summary, created_at, updated_at, archived` |
| `messages` | `id, conversation_id, role, content, citations jsonb, usage jsonb, latency_ms, created_at` |
| `ingestion_jobs` | `id, document_id, state, attempts, heartbeat_at, last_error, started_at, finished_at` |
| `audit_log` | `id, actor_id, action, entity_type, entity_id, payload jsonb, ip, created_at` |

**Indexes**
- `document_chunks.embedding` — HNSW, `vector_cosine_ops`
- `document_chunks.tsv` — GIN, for keyword/BM25-style search
- `document_chunks(department_id)`, `documents(department_id, status)`, `messages(conversation_id, created_at)`

**Chunk metadata** carries whatever makes a citation clickable and precise:
`{page, sheet, heading_path, row_range, section, source_type}`.

`checksum_sha256` deduplicates re-uploads of an identical file within a department.

---

## 5. Ingestion pipeline

### 5.1 Flow
`admin upload → validate (type, size, checksum) → Supabase Storage
→ documents row (pending) → ingestion_jobs row → worker picks up
→ parse → normalise → chunk → embed (batched) → insert chunks → status=ready`

Storage path: `documents/{department_slug}/{document_id}/{filename}`.

### 5.2 Parsers and chunking
| Type | Parser | Chunking |
|---|---|---|
| PDF | `pypdf`, `pdfplumber` for tables | ~800 tokens, 120 overlap, page number retained |
| DOCX | `python-docx` | heading-path aware, same token target |
| XLSX/CSV | `openpyxl` / `pandas` | row groups with the header row repeated per chunk; sheet name in metadata |
| PPTX | `python-pptx` | one chunk per slide, slide title retained |
| JSON | stdlib | path-aware flattening, grouped by top-level key |
| TXT/MD/HTML | native / `markdownify` | recursive splitter on structure boundaries |

A PDF that yields under a configured character-per-page floor is marked
`failed` with `error_message = "No extractable text — likely a scanned/image PDF. Re-upload a
text-based version or run OCR first."` It is never indexed empty.

### 5.3 Worker model (deliberately simple)
Ingestion runs as a background task inside the API container, coordinated through the
`ingestion_jobs` table with a heartbeat column. On startup, the API sweeps jobs whose heartbeat
is stale and re-queues them, so an ECS task replacement mid-ingest does not strand a document.
Retries: 3 attempts with backoff, then terminal `failed`.

*Escape hatch, if throughput demands it later:* split into a dedicated ECS worker service fed by
SQS. The `ingestion_jobs` abstraction is designed so this swap does not touch the API surface.

---

## 6. Retrieval and the agent

### 6.1 Hybrid retrieval
Dense (pgvector cosine) **and** keyword (`tsquery`) run in parallel, fused with Reciprocal Rank
Fusion, then trimmed to top-k. Every query is hard-filtered by `department_id` at the SQL level.
Dense-only retrieval loses exact identifiers — invoice numbers, SKUs, policy codes — which is
precisely what people search company documents for.

### 6.2 LangGraph state machine
This is the "agentic decision based on input" requirement. Nodes:

1. **`guard`** — verify department access; screen for prompt injection in the user turn.
2. **`route`** — classify the input: `chitchat` · `document_qa` · `catalog_query`
   ("what documents do we have?") · `summarize_document` · `out_of_scope`.
   Conditional edges here are what make the system agentic rather than a fixed chain.
3. **`rewrite`** — history-aware query rewriting so follow-ups resolve pronouns and ellipsis.
4. **`retrieve`** — hybrid search, department-scoped.
5. **`grade`** — score chunk relevance. Weak result set → loop back to `rewrite` with a
   broadened query, **max 2 iterations**, then fall through to `no_answer`.
6. **`generate`** — answer with inline citation markers bound to chunk ids.
7. **`verify`** — groundedness check on the draft. Unsupported claims → one regeneration, then
   degrade to an honest "not found".
8. **`persist`** — write the message, citations, token usage, and latency.

### 6.3 Conversation memory
Recent turns verbatim + a rolling summary for long threads, both stored in Postgres.
The graph checkpointer is Postgres-backed so a container restart never loses a conversation.

---

## 7. Auth model

- Supabase Auth, email + password. **Self-signup is disabled** — admins issue accounts.
- Admin user creation goes through the FastAPI backend using the service-role key.
  The service-role key exists only in Secrets Manager and only in the backend task. It is
  never in the Streamlit container, never in the browser, never in git.
- Streamlit holds the user's JWT in session state and sends it as `Authorization: Bearer`.
- FastAPI verifies the JWT, loads the profile, and enforces role + department on every request.
- RLS provides defence in depth: even a backend bug cannot return another department's chunks.

---

## 8. AWS infrastructure (Terraform, `us-east-1`)

| Component | dev | prod |
|---|---|---|
| VPC | 2 AZs, public subnets, **no NAT** (tasks get public IPs) | 2 AZs, private subnets + NAT |
| ALB | HTTP :80 | HTTPS :443 (needs domain + ACM), HTTP redirect |
| ECS | Fargate, 1 task/service, low CPU/mem | Fargate, 2 tasks/service, autoscaling on CPU + request count |
| Services | `api`, `ui` | `api`, `ui` |
| ECR | 2 repos, lifecycle keeps last 10 images | same |
| CodeDeploy | blue/green, 2 target groups + test listener per service | same, with canary bake time |
| Logs retention | 30 days | 90 days |

Dev skips the NAT gateway on purpose — it is the single largest idle cost in a small
environment and dev has no inbound requirement for private subnets.

**Secrets Manager** holds: `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_DB_URL`, `JWT_SECRET`. Injected into tasks via
`secrets.valueFrom` — never as plaintext environment variables, never baked into an image.

**Terraform state**: S3 bucket with versioning + DynamoDB lock table, created once by
`infra/terraform/bootstrap`. Everything else is `envs/dev` and `envs/prod`.

**Tagging** on every resource: `Project`, `Environment`, `ManagedBy=terraform`, `Owner`.
The destroyer relies on these.

---

## 9. Observability (CloudWatch)

- **Logs** — structured JSON, one line per event, with `request_id`, `trace_id`, `user_id`,
  `department_id`, `route`. Log groups `/ecs/{env}/{service}`. No secrets, no document content,
  no full user questions at INFO.
- **Metrics** — emitted via EMF: ingestion duration and outcome, chunks per document,
  retrieval latency, agent node latency, token usage and estimated cost per request,
  citation count, `no_answer` rate.
- **Alarms** → SNS: ALB 5xx rate, target health, p99 latency, ingestion failure rate,
  unhealthy task count, OpenAI error rate. Prod alarms are wired into CodeDeploy as
  automatic rollback triggers.
- **Dashboard** per environment: request rate, latency percentiles, error rate, ingestion
  queue depth, token spend.
- **Tracing** — ADOT sidecar, enabled in prod, toggleable in dev via a Terraform variable.

---

## 10. CI/CD

Branching: `feature/*` → `develop` (auto-deploys dev) → `main` (deploys prod behind approval).

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | PR to any branch | ruff lint + format check, mypy, pytest + coverage gate, docker build, Trivy scan, `terraform fmt/validate`, `terraform plan` on dev |
| `cd-dev.yml` | push to `develop` | build + push both images (tag = commit SHA), `terraform apply` dev, CodeDeploy blue/green, smoke tests against the **test listener before traffic shifts**, auto-rollback on alarm |
| `cd-prod.yml` | push to `main` | same, gated by GitHub Environment `production` with required reviewer, canary bake, auto-rollback |
| `destroy.yml` | manual dispatch | tears down an environment; requires typing the environment name; prod additionally requires the `production` environment approval |

AWS access is via **GitHub OIDC role assumption**. No AWS access keys in repository secrets.
Images are tagged with the commit SHA — never redeployed from `latest`.

---

## 11. Operational commands

Every one of these must exist and work before the project is considered done.

```
make bootstrap ENV=dev        # one-time: terraform state bucket + lock table
make up ENV=dev               # terraform apply
make deploy ENV=dev           # build, push, blue/green release
make logs ENV=dev SVC=api     # tail CloudWatch
make destroy ENV=dev          # tear down everything for that env
make local                    # docker-compose up, api + ui
make test                     # full test suite
make migrate                  # apply supabase migrations
```

`make destroy` is the resource destroyer the owner asked for. It refuses to run against
`prod` unless `CONFIRM=DESTROY-PROD` is passed, and it prints the full resource list before
acting.

---

## 12. Build order

Each phase ends in something demonstrable. No phase starts before the previous one runs.

1. **Foundation** — repo scaffold, Makefile, docker-compose, config, structured logging, `/health`.
2. **Data layer** — Supabase migrations, RLS policies, pgvector + HNSW + GIN indexes, seed script.
3. **Auth** — Supabase Auth wiring, JWT verification, role and department dependencies, admin user CRUD.
4. **Ingestion** — upload endpoint, all parsers, chunking, embedding, job table with retries and status.
5. **Retrieval** — hybrid search with RRF, department filtering, citation assembly.
6. **Agent** — LangGraph nodes and conditional edges, grading loop, groundedness verification, memory.
7. **Frontend** — login, chat with citation cards, history, then the four admin pages.
8. **Infrastructure** — Terraform bootstrap, modules, dev environment, ALB, ECS, secrets, destroyer.
9. **Pipeline** — CI, dev CD with blue/green, smoke tests, then prod with approval gate.
10. **Observability** — EMF metrics, alarms, dashboards, rollback triggers, ADOT.
11. **Hardening** — load check, cost review, runbook, README.

---

## 13. Open items for the owner

Answer these before or during Phase 8 — they block a real prod deployment, not the build.

1. **Domain name** for prod HTTPS. Without one, prod ALB serves HTTP only, which is not
   acceptable for credentials in transit.
2. **AWS account id** and whether dev and prod share it.
3. **Supabase projects** — confirm two separate projects, and provide their URLs.
4. **Alarm destination** — the email or Slack webhook for the SNS topic.
5. **Monthly budget ceiling**, so a Budgets alarm can be set alongside the infrastructure.
6. **Document retention** — do deleted documents purge immediately, or soft-delete for N days?
