-- SaarthiBhai Phase 2 — Study Intelligence
-- Safe/re-runnable migration. Creates metadata-only sources, reviewed short-question queue,
-- and persistent flashcards/review history. It does NOT ingest or copy textbook/PYQ bodies.

create extension if not exists pgcrypto;

create table if not exists public.study_intelligence_sources (
  id uuid primary key default gen_random_uuid(),
  kind text not null default 'official' check (kind in ('pyq','ncert','current_affairs','official')),
  title text not null,
  url text not null unique,
  domain text not null default '',
  institution text not null default '',
  status text not null default 'official_link' check (status in ('official_link','external_reference','unverified_link')),
  class_name text,
  subject text,
  chapter text,
  published_at text,
  metadata jsonb not null default '{}'::jsonb,
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index if not exists study_intelligence_sources_kind_idx on public.study_intelligence_sources(kind);
create index if not exists study_intelligence_sources_domain_idx on public.study_intelligence_sources(domain);

create table if not exists public.study_question_queue (
  id uuid primary key default gen_random_uuid(),
  uid text not null,
  question_text text not null check (char_length(question_text) <= 1200),
  exam text not null default '',
  class_name text not null default '',
  subject text not null default '',
  topic text not null default '',
  status text not null default 'pending',
  reviewer_note text not null default '',
  submitted_at timestamptz not null default now(),
  reviewed_at timestamptz
);
-- Keep review state transitions safe to retry (pending -> approving -> approved/rejected).
do $$
begin
  if exists (
    select 1 from pg_constraint c
    join pg_class t on t.oid = c.conrelid
    join pg_namespace n on n.oid = t.relnamespace
    where n.nspname = 'public' and t.relname = 'study_question_queue'
      and c.conname = 'study_question_queue_status_check'
  ) then
    alter table public.study_question_queue drop constraint study_question_queue_status_check;
  end if;
  alter table public.study_question_queue
    add constraint study_question_queue_status_check
    check (status in ('pending','approving','approved','rejected'));
end $$;

create index if not exists study_question_queue_status_idx on public.study_question_queue(status, submitted_at);
create index if not exists study_question_queue_uid_idx on public.study_question_queue(uid);

create table if not exists public.study_flashcards (
  id uuid primary key default gen_random_uuid(),
  uid text not null,
  topic text not null default '',
  front text not null check (char_length(front) <= 500),
  back text not null check (char_length(back) <= 1200),
  due_at timestamptz not null default now(),
  interval_days integer not null default 0 check (interval_days >= 0 and interval_days <= 365),
  repetitions integer not null default 0 check (repetitions >= 0),
  ease_factor numeric(3,2) not null default 2.50 check (ease_factor >= 1.30 and ease_factor <= 3.00),
  last_rating text check (last_rating is null or last_rating in ('again','hard','good','easy')),
  last_reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists study_flashcards_uid_due_idx on public.study_flashcards(uid, due_at);

create table if not exists public.study_flashcard_reviews (
  id uuid primary key default gen_random_uuid(),
  card_id uuid not null references public.study_flashcards(id) on delete cascade,
  uid text not null,
  rating text not null check (rating in ('again','hard','good','easy')),
  interval_days integer not null check (interval_days >= 1 and interval_days <= 365),
  reviewed_at timestamptz not null default now()
);
create index if not exists study_flashcard_reviews_uid_idx on public.study_flashcard_reviews(uid, reviewed_at desc);
create index if not exists study_flashcard_reviews_card_idx on public.study_flashcard_reviews(card_id, reviewed_at desc);

-- These endpoints use the server-side Supabase service/secret key. Do not expose that key to a browser.
alter table public.study_intelligence_sources enable row level security;
alter table public.study_question_queue enable row level security;
alter table public.study_flashcards enable row level security;
alter table public.study_flashcard_reviews enable row level security;

drop policy if exists "study_intelligence_sources_service_role_only" on public.study_intelligence_sources;
create policy "study_intelligence_sources_service_role_only" on public.study_intelligence_sources
  for all to service_role using (true) with check (true);
drop policy if exists "study_question_queue_service_role_only" on public.study_question_queue;
create policy "study_question_queue_service_role_only" on public.study_question_queue
  for all to service_role using (true) with check (true);
drop policy if exists "study_flashcards_service_role_only" on public.study_flashcards;
create policy "study_flashcards_service_role_only" on public.study_flashcards
  for all to service_role using (true) with check (true);
drop policy if exists "study_flashcard_reviews_service_role_only" on public.study_flashcard_reviews;
create policy "study_flashcard_reviews_service_role_only" on public.study_flashcard_reviews
  for all to service_role using (true) with check (true);

revoke all on public.study_intelligence_sources from anon, authenticated;
revoke all on public.study_question_queue from anon, authenticated;
revoke all on public.study_flashcards from anon, authenticated;
revoke all on public.study_flashcard_reviews from anon, authenticated;
grant all on public.study_intelligence_sources to service_role;
grant all on public.study_question_queue to service_role;
grant all on public.study_flashcards to service_role;
grant all on public.study_flashcard_reviews to service_role;

comment on table public.study_intelligence_sources is 'Official-source index: titles, URLs and minimal metadata only; do not store copyrighted PDF/textbook/question-paper bodies.';
comment on table public.study_question_queue is 'Short student-submitted questions pending human review; never marked official merely by community approval.';
comment on table public.study_flashcards is 'Student-owned flashcards and due dates for spaced repetition.';
comment on table public.study_flashcard_reviews is 'Audit history for flashcard review ratings and scheduled intervals.';
