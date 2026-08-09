-- 0003_search.sql — hybrid retrieval
--
-- Dense (pgvector cosine) and keyword (tsquery) run over the same department
-- scoped candidate pool and fuse with Reciprocal Rank Fusion. Vector-only
-- retrieval loses exact identifiers — invoice numbers, SKUs, policy codes —
-- which is most of what people actually search company documents for.
--
-- SECURITY INVOKER (the default) is deliberate: RLS on document_chunks applies
-- to the caller, so this function cannot be used to read another department.

create or replace function public.hybrid_search(
    p_department  uuid,
    p_embedding   vector(1536),
    p_query       text,
    p_match_count integer default 8,
    p_pool        integer default 30
)
returns table (
    chunk_id    uuid,
    document_id uuid,
    filename    text,
    chunk_index integer,
    content     text,
    metadata    jsonb,
    score       double precision
)
language sql
stable
as $$
    with dense as (
        select c.id,
               row_number() over (order by c.embedding <=> p_embedding) as rnk
        from public.document_chunks c
        where c.department_id = p_department
          and c.embedding is not null
        order by c.embedding <=> p_embedding
        limit p_pool
    ),
    kw as (
        select c.id,
               row_number() over (order by ts_rank_cd(c.tsv, q.query) desc) as rnk
        from public.document_chunks c,
             websearch_to_tsquery('english', coalesce(p_query, '')) as q(query)
        where c.department_id = p_department
          and c.tsv @@ q.query
        order by ts_rank_cd(c.tsv, q.query) desc
        limit p_pool
    ),
    fused as (
        select coalesce(d.id, k.id) as id,
               coalesce(1.0 / (60 + d.rnk), 0.0)
             + coalesce(1.0 / (60 + k.rnk), 0.0) as score
        from dense d
        full outer join kw k on d.id = k.id
    )
    select c.id,
           c.document_id,
           doc.filename,
           c.chunk_index,
           c.content,
           c.metadata,
           f.score::double precision
    from fused f
    join public.document_chunks c on c.id = f.id
    join public.documents doc on doc.id = c.document_id
    order by f.score desc
    limit p_match_count;
$$;

grant execute on function
    public.hybrid_search(uuid, vector, text, integer, integer)
    to authenticated;

-- keep conversations.updated_at honest so history sorts correctly
create or replace function public.touch_conversation()
returns trigger
language plpgsql
as $$
begin
    update public.conversations
       set updated_at = now()
     where id = new.conversation_id;
    return new;
end;
$$;

drop trigger if exists trg_touch_conversation on public.messages;
create trigger trg_touch_conversation
    after insert on public.messages
    for each row execute function public.touch_conversation();
