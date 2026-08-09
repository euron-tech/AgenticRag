-- 0001_init.sql — core schema
-- Forward-only. Never edit an applied migration; add a new one.

create extension if not exists vector;

-- ---------------------------------------------------------------- departments
create table if not exists public.departments (
    id          uuid primary key default gen_random_uuid(),
    name        text        not null,
    slug        text        not null unique,
    description text,
    is_active   boolean     not null default true,
    created_at  timestamptz not null default now()
);

-- ------------------------------------------------------------------ profiles
-- Mirrors auth.users. Rows are created by the backend when an admin issues an
-- account; self-signup is disabled in Supabase Auth settings.
create table if not exists public.profiles (
    id         uuid primary key references auth.users (id) on delete cascade,
    email      text        not null unique,
    full_name  text,
    role       text        not null default 'user' check (role in ('admin', 'user')),
    is_active  boolean     not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.user_departments (
    user_id       uuid not null references public.profiles (id) on delete cascade,
    department_id uuid not null references public.departments (id) on delete cascade,
    granted_by    uuid references public.profiles (id) on delete set null,
    granted_at    timestamptz not null default now(),
    primary key (user_id, department_id)
);

-- ----------------------------------------------------------------- documents
create table if not exists public.documents (
    id              uuid primary key default gen_random_uuid(),
    department_id   uuid        not null references public.departments (id) on delete cascade,
    filename        text        not null,
    storage_path    text        not null,
    mime_type       text        not null,
    size_bytes      bigint      not null,
    checksum_sha256 text        not null,
    status          text        not null default 'pending'
                    check (status in ('pending', 'processing', 'ready', 'failed')),
    error_message   text,
    page_count      integer,
    chunk_count     integer     not null default 0,
    uploaded_by     uuid references public.profiles (id) on delete set null,
    metadata        jsonb       not null default '{}'::jsonb,
    created_at      timestamptz not null default now(),
    processed_at    timestamptz,
    -- the same file uploaded twice into one department is rejected, not re-indexed
    unique (department_id, checksum_sha256)
);

-- department_id is denormalised here so vector search filters without a join
create table if not exists public.document_chunks (
    id            uuid primary key default gen_random_uuid(),
    document_id   uuid    not null references public.documents (id) on delete cascade,
    department_id uuid    not null references public.departments (id) on delete cascade,
    chunk_index   integer not null,
    content       text    not null,
    token_count   integer not null default 0,
    embedding     vector(1536),
    tsv           tsvector generated always as (to_tsvector('english', content)) stored,
    metadata      jsonb   not null default '{}'::jsonb,
    created_at    timestamptz not null default now(),
    unique (document_id, chunk_index)
);

-- ------------------------------------------------------------- conversations
create table if not exists public.conversations (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references public.profiles (id) on delete cascade,
    department_id uuid not null references public.departments (id) on delete cascade,
    title         text not null default 'New conversation',
    summary       text,
    archived      boolean     not null default false,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create table if not exists public.messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references public.conversations (id) on delete cascade,
    role            text not null check (role in ('user', 'assistant')),
    content         text not null,
    citations       jsonb not null default '[]'::jsonb,
    usage           jsonb not null default '{}'::jsonb,
    latency_ms      integer,
    created_at      timestamptz not null default now()
);

-- ----------------------------------------------------------- ingestion jobs
-- Durable queue. heartbeat_at lets a restarted container reclaim stranded jobs.
create table if not exists public.ingestion_jobs (
    id           uuid primary key default gen_random_uuid(),
    document_id  uuid not null references public.documents (id) on delete cascade,
    state        text not null default 'queued'
                 check (state in ('queued', 'running', 'done', 'failed')),
    attempts     integer     not null default 0,
    heartbeat_at timestamptz,
    last_error   text,
    created_at   timestamptz not null default now(),
    started_at   timestamptz,
    finished_at  timestamptz
);

create table if not exists public.audit_log (
    id          uuid primary key default gen_random_uuid(),
    actor_id    uuid references public.profiles (id) on delete set null,
    action      text not null,
    entity_type text,
    entity_id   uuid,
    payload     jsonb not null default '{}'::jsonb,
    ip          text,
    created_at  timestamptz not null default now()
);

-- -------------------------------------------------------------------- indexes
create index if not exists idx_chunks_embedding
    on public.document_chunks using hnsw (embedding vector_cosine_ops);
create index if not exists idx_chunks_tsv
    on public.document_chunks using gin (tsv);
create index if not exists idx_chunks_department
    on public.document_chunks (department_id);
create index if not exists idx_chunks_document
    on public.document_chunks (document_id);
create index if not exists idx_documents_dept_status
    on public.documents (department_id, status);
create index if not exists idx_messages_conversation
    on public.messages (conversation_id, created_at);
create index if not exists idx_conversations_user
    on public.conversations (user_id, updated_at desc);
create index if not exists idx_jobs_state
    on public.ingestion_jobs (state, created_at);
create index if not exists idx_audit_created
    on public.audit_log (created_at desc);
