-- F1 Race Engineer — Supabase schema
-- Run once in the Supabase SQL editor (or `supabase db push`).

-- ---- profiles: one row per auth user ------------------------------------
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  display_name text,
  subscription_status text not null default 'inactive',  -- inactive | active | past_due | canceled
  stripe_customer_id text unique,
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "read own profile"
  on public.profiles for select using (auth.uid() = id);
create policy "update own profile"
  on public.profiles for update using (auth.uid() = id) with check (auth.uid() = id);

-- Clients may only change display_name; subscription fields are
-- service-role-only (the Stripe webhook).
revoke update on public.profiles from authenticated;
grant update (display_name) on public.profiles to authenticated;

create or replace function public.handle_new_user()
returns trigger security definer set search_path = public as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email);
  return new;
end $$ language plpgsql;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---- sessions: one row per on-track session ------------------------------
create table public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  track_id int,
  track_name text,
  session_type int,
  started_at timestamptz not null default now(),
  laps_count int not null default 0,
  best_lap_ms int,
  best_s1_ms int,
  best_s2_ms int,
  best_s3_ms int,
  consistency_ms int,
  weakest_sector int,
  recurring jsonb,
  created_at timestamptz not null default now()
);

alter table public.sessions enable row level security;
create policy "own sessions select" on public.sessions for select using (auth.uid() = user_id);
create policy "own sessions insert" on public.sessions for insert with check (auth.uid() = user_id);
create policy "own sessions delete" on public.sessions for delete using (auth.uid() = user_id);
create index sessions_user_idx on public.sessions (user_id, started_at desc);

-- ---- laps: per-lap detail -------------------------------------------------
create table public.laps (
  id bigint generated always as identity primary key,
  session_id uuid not null references public.sessions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  lap_num int not null,
  lap_time_ms int not null,
  s1_ms int,
  s2_ms int,
  s3_ms int,
  invalid boolean not null default false,
  wear_pct real,
  fuel_kg real,
  yellow boolean not null default false,
  is_pb boolean not null default false
);

alter table public.laps enable row level security;
create policy "own laps select" on public.laps for select using (auth.uid() = user_id);
create policy "own laps insert" on public.laps for insert with check (auth.uid() = user_id);
create policy "own laps delete" on public.laps for delete using (auth.uid() = user_id);
create index laps_session_idx on public.laps (session_id, lap_num);
