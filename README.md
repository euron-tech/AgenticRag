# Agentic RAG for Company

Department-scoped document intelligence. Admins upload documents per department;
members with access to that department chat with them and get answers that cite
the exact page, sheet range, slide, or section they came from.

Requirements and design decisions live in [`CLAUDE.md`](CLAUDE.md).
Engineering rules live in [`.claude/rules/engineering-rules.md`](.claude/rules/engineering-rules.md).

---

## What it does

- **Any common document type** — PDF, DOCX, XLSX, CSV, PPTX, JSON, HTML, TXT, MD.
- **Department isolation enforced in Postgres.** Row level security is the primary
  control, not an application `if` statement. A member cannot read another
  department's chunks even if the API has a bug.
- **Hybrid retrieval.** Dense vectors *and* keyword search, fused with Reciprocal
  Rank Fusion. Vector-only search loses exact identifiers — invoice numbers,
  SKUs, policy codes — which is most of what people search company documents for.
- **An agent that decides.** A LangGraph state machine routes each message,
  rewrites follow-ups against history, grades what it retrieved, broadens and
  retries when the result is weak, and verifies the draft is grounded before
  answering.
- **Citations on every claim.** Assembled from the retrieval result, never from
  what the model says about its sources.
- **Loud ingestion failures.** A scanned PDF that yields no text is marked
  `failed` with a reason an admin can act on — never indexed as an empty document.

## Architecture

```
Streamlit (one app, role-gated)          FastAPI                    Supabase
  ├─ Chat + history          ──JWT──►      ├─ auth / documents / chat  ├─ Postgres + pgvector
  └─ Admin: users,                         ├─ ingestion worker         ├─ Storage
     departments,                          └─ LangGraph agent          └─ Auth
     documents, audit                              │
                                                   └──► OpenAI (gpt-4o-mini,
                                                        text-embedding-3-small)

AWS: ECR → ECS Fargate → ALB, CodeDeploy blue/green, Secrets Manager, CloudWatch
     Terraform, us-east-1, dev + prod
```

## Repository layout

| Path | What is in it |
|---|---|
| `backend/` | FastAPI: API, ingestion, retrieval, LangGraph agent, tests |
| `frontend/` | Streamlit: chat, history, four admin pages |
| `supabase/migrations/` | Schema, RLS policies, hybrid search function |
| `infra/terraform/` | `bootstrap`, `modules`, `stack`, `envs/dev`, `envs/prod` |
| `.github/workflows/` | CI, dev CD, prod CD, plan, destroy |
| `manage.py` | Every operational command |

---

## Running it locally

```bash
cp .env.example .env          # then fill it in — see "What you need to provide"
python manage.py migrate      # apply the schema to your Supabase project
python manage.py seed --email you@company.com
python manage.py local        # docker compose: API on :8000, UI on :8501
```

Open <http://localhost:8501> and sign in with the account you seeded.

Without Docker:

```bash
pip install -r backend/requirements.txt && uvicorn app.main:app --reload  # from backend/
pip install -r frontend/requirements.txt && streamlit run app.py          # from frontend/
```

## Deploying to AWS

```bash
python manage.py bootstrap                      # once per account: state bucket + lock table
python manage.py up --env dev --alert-email you@company.com --github-repo owner/repo
python manage.py secrets --env dev              # write the secret values
python manage.py deploy --env dev               # blue/green release
python manage.py status --env dev
```

`up` runs in three phases because a task definition needs an image and an image
needs a registry: it creates the ECR repositories, builds and pushes, then
applies everything else.

### What a release actually does

1. Builds both images, tags them with the commit SHA, pushes to ECR. `latest` is
   never deployed.
2. Registers a new task definition revision with the new image.
3. Starts a CodeDeploy blue/green deployment. The green task set comes up and
   **holds** — no production traffic has moved.
4. Smoke tests green through the dedicated test listener.
5. Only if that passes, calls `continue-deployment` to shift traffic.
6. If a CloudWatch alarm fires during the bake window, CodeDeploy rolls back on
   its own. Rollback is not a manual step.

A failed smoke test stops the deployment and blue keeps serving.

### Tearing it down

```bash
python manage.py destroy --env dev
python manage.py destroy --env prod --confirm DESTROY-PROD
```

Prints the full destroy plan, then requires you to type the environment name.
The Terraform state bucket is deliberately left behind — deleting it would
orphan every other stack.

Documents in Supabase are **not** touched by `destroy`. Only AWS resources go.

---

## What you need to provide

### 1. Supabase — one project per environment

Create two projects (dev and prod) at <https://supabase.com>. From each:

| Value | Where to find it |
|---|---|
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_ANON_KEY` | Project Settings → API → `anon` `public` |
| `SUPABASE_SERVICE_ROLE_KEY` | Project Settings → API → `service_role` (**secret**) |
| `SUPABASE_JWT_SECRET` | Project Settings → API → JWT Settings → JWT Secret |
| `SUPABASE_DB_URL` | Project Settings → Database → Connection string → URI |

Two settings to change in the Supabase dashboard:

- **Authentication → Providers → Email → disable "Enable sign ups".**
  Accounts are issued by an administrator; self-signup would let anyone in.
- **Database → Extensions → enable `vector`.** The migration also creates it,
  but enabling it first avoids a permissions surprise.

Use the **session pooler** connection string (port 5432), not the transaction
pooler (6543).

### 2. OpenAI

An API key with access to `gpt-4o-mini` and `text-embedding-3-small`.
Put it in `.env` as `OPENAI_API_KEY` and in Secrets Manager via
`python manage.py secrets`. Do not paste it into a chat or a commit.

### 3. AWS

- An account, and credentials on your machine (`aws configure` or `aws sso login`).
- Decide whether dev and prod share the account. They can.
- An email address for CloudWatch alarms — you must click the confirmation link
  AWS sends, or the topic delivers nothing.

### 4. GitHub

After `manage.py up --env dev`, it prints a deploy role ARN. Set these
repository secrets:

| Secret | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | the `github_deploy_role_arn` output from dev |
| `AWS_DEPLOY_ROLE_ARN_PROD` | the same output from prod |

Then create two GitHub Environments: `dev`, and `production` with a required
reviewer. The prod deploy pauses for that approval.

Branching: `feature/*` → `develop` (deploys dev) → `main` (deploys prod).

### 5. For prod, before real traffic

- **A domain name and an ACM certificate.** Without them the ALB serves plain
  HTTP, and users would be sending passwords in the clear. This is the one item
  that genuinely blocks a production launch.
- After the first prod apply, read the `egress_cidrs` output and set
  `api_ingress_cidrs` to it, so the API listener stops accepting connections
  from the whole internet.

---

## Things worth knowing

**The API listener is public.** The ALB exposes the UI on `:80` and the API on
`:8080`. The Streamlit container calls the API over that listener. An internal
load balancer would be tidier but costs a second ALB and does not work cleanly
in dev, where tasks sit in public subnets. Every route except `/health` requires
a valid JWT. In prod, narrow `api_ingress_cidrs` to the NAT gateway's address.

**Dev has no NAT gateway.** Tasks run in public subnets with public IPs. This is
the single largest idle cost in a small environment and dev does not need it.

**Ingestion runs inside the API container**, coordinated through the
`ingestion_jobs` table with a heartbeat, so a task replaced mid-ingest has its
job reclaimed rather than stranding a document in `processing`. If throughput
outgrows this, the same table can be fed from SQS by a dedicated worker without
touching the API.

**Changing the embedding model is not a config edit.** The dimension is baked
into `vector(1536)`. A different model needs a migration and a full re-index.

**Chunk sizes are in characters, not tokens.** A character budget needs no
tokeniser download at container start; roughly four characters per token.

## Testing

```bash
python manage.py test                    # unit tests + lint
TEST_DB_URL=postgresql://... pytest backend/tests/integration -v
```

The integration suite proves cross-department isolation against a real database.
It is skipped unless `TEST_DB_URL` is set, and it must point at a scratch
database — never dev or prod.
