-- PitWall licence API — D1 (SQLite) schema.
--   npx wrangler d1 execute pitwall --remote --file schema.sql
-- Same profile columns as the old Supabase table, so the billing logic
-- carries over unchanged.

create table if not exists users (
  id text primary key,                    -- random UUID
  email text not null unique,             -- lower-cased
  password_hash text not null,            -- PBKDF2-SHA256, base64
  password_salt text not null,            -- base64
  password_iterations integer not null,
  created_at integer not null             -- unix seconds
);

create table if not exists profiles (
  id text primary key references users(id) on delete cascade,
  email text not null,
  subscription_status text not null default 'inactive',  -- inactive | active | past_due | canceled
  stripe_customer_id text unique,
  stripe_subscription_id text,
  current_period_end integer,             -- unix seconds
  billing_synced_at integer
);

-- Refresh tokens are stored hashed and rotated on every use.
create table if not exists refresh_tokens (
  token_hash text primary key,            -- SHA-256 hex of the token
  user_id text not null references users(id) on delete cascade,
  expires_at integer not null
);
create index if not exists refresh_tokens_user on refresh_tokens (user_id);

-- Failed sign-ins per email, for a short lockout against password guessing.
create table if not exists login_failures (
  email text primary key,
  count integer not null,
  first_at integer not null
);
