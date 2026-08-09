-- 0002_rls.sql — row level security
--
-- This is the primary access control for the whole product. The backend sets
-- `role authenticated` plus request.jwt.claims per request, so these policies
-- apply to every user-facing read. Application checks are a second layer.

-- ------------------------------------------------------------ helper functions
-- security definer so evaluating a policy on profiles does not recurse into
-- the profiles policy.
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1 from public.profiles
        where id = auth.uid() and role = 'admin' and is_active
    );
$$;

create or replace function public.has_department(p_department uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select public.is_admin() or exists (
        select 1
        from public.user_departments ud
        join public.profiles p on p.id = ud.user_id
        where ud.user_id = auth.uid()
          and ud.department_id = p_department
          and p.is_active
    );
$$;

revoke all on function public.is_admin() from public;
revoke all on function public.has_department(uuid) from public;
grant execute on function public.is_admin() to authenticated;
grant execute on function public.has_department(uuid) to authenticated;

-- ------------------------------------------------------------------- enable
alter table public.departments      enable row level security;
alter table public.profiles         enable row level security;
alter table public.user_departments enable row level security;
alter table public.documents        enable row level security;
alter table public.document_chunks  enable row level security;
alter table public.conversations    enable row level security;
alter table public.messages         enable row level security;
alter table public.ingestion_jobs   enable row level security;
alter table public.audit_log        enable row level security;

-- ----------------------------------------------------------------- policies
drop policy if exists departments_select on public.departments;
create policy departments_select on public.departments
    for select using (public.has_department(id));

drop policy if exists departments_admin on public.departments;
create policy departments_admin on public.departments
    for all using (public.is_admin()) with check (public.is_admin());

drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles
    for select using (id = auth.uid() or public.is_admin());

drop policy if exists profiles_admin on public.profiles;
create policy profiles_admin on public.profiles
    for all using (public.is_admin()) with check (public.is_admin());

drop policy if exists user_departments_select on public.user_departments;
create policy user_departments_select on public.user_departments
    for select using (user_id = auth.uid() or public.is_admin());

drop policy if exists user_departments_admin on public.user_departments;
create policy user_departments_admin on public.user_departments
    for all using (public.is_admin()) with check (public.is_admin());

drop policy if exists documents_select on public.documents;
create policy documents_select on public.documents
    for select using (public.has_department(department_id));

drop policy if exists documents_admin on public.documents;
create policy documents_admin on public.documents
    for all using (public.is_admin()) with check (public.is_admin());

-- The single most important policy in the product: a chunk is unreachable
-- unless the caller holds the department it belongs to.
drop policy if exists chunks_select on public.document_chunks;
create policy chunks_select on public.document_chunks
    for select using (public.has_department(department_id));

drop policy if exists chunks_admin on public.document_chunks;
create policy chunks_admin on public.document_chunks
    for all using (public.is_admin()) with check (public.is_admin());

drop policy if exists conversations_own on public.conversations;
create policy conversations_own on public.conversations
    for all
    using (user_id = auth.uid() and public.has_department(department_id))
    with check (user_id = auth.uid() and public.has_department(department_id));

drop policy if exists messages_own on public.messages;
create policy messages_own on public.messages
    for all
    using (exists (select 1 from public.conversations c
                   where c.id = conversation_id and c.user_id = auth.uid()))
    with check (exists (select 1 from public.conversations c
                        where c.id = conversation_id and c.user_id = auth.uid()));

drop policy if exists jobs_admin on public.ingestion_jobs;
create policy jobs_admin on public.ingestion_jobs
    for all using (public.is_admin()) with check (public.is_admin());

drop policy if exists audit_admin on public.audit_log;
create policy audit_admin on public.audit_log
    for select using (public.is_admin());

-- -------------------------------------------------------------------- grants
grant usage on schema public to authenticated;
grant select on public.departments, public.profiles, public.user_departments,
                 public.documents, public.document_chunks, public.audit_log,
                 public.ingestion_jobs
    to authenticated;
grant select, insert, update, delete on public.conversations, public.messages
    to authenticated;
