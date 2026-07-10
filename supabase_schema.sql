-- revenue-squad — Supabase `pipeline` table (the --crm supabase backend).
--
-- RLS stance
-- ----------
-- revenue-squad reads and writes this table ONLY from the CLI, using the Supabase
-- service_role key. The service_role key BYPASSES Row Level Security entirely, so we
-- deliberately do NOT define any anon/authenticated policies here: there is no browser
-- or public client path to this data. Exposing anon writes would let anyone holding the
-- public anon key edit your pipeline. Keep the service_role key secret — server/CLI env
-- only, never shipped to a frontend. If you later add a client-facing path, enable RLS
-- explicitly (`alter table public.pipeline enable row level security;`) and add scoped
-- policies at that point.
--
-- Setup
-- -----
-- Paste this whole file into the Supabase SQL editor (Project -> SQL Editor -> New query
-- -> paste -> Run), then run `squad supabase-init` to verify the table responds.

create table if not exists public.pipeline (
    id              bigint generated always as identity primary key,
    company         text not null,
    contact         text,
    email           text,
    email_evidence  text,
    phone           text,
    city            text,
    website         text,
    status          text,
    vertical        text,
    service_line    text,
    batch           text,
    lead_score      numeric,
    score_rationale text,
    deal_value      numeric,
    day_1_sent      date,
    day_3_sent      date,
    day_7_sent      date,
    follow_up_due   date,
    replied         boolean default false,
    reply_date      date,
    call_booked     boolean default false,
    call_date       date,
    notes           text,
    blocked         boolean default false
);

-- Dedupe key: one row per (company, email), case-insensitive. Mirrors the CSV/Notion
-- backends' Python-side (Company, Email) dedupe and backs the ilike company lookups.
create unique index if not exists pipeline_company_email_key
    on public.pipeline (lower(company), lower(email));
