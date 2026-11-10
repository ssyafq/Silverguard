-- SilverGuard online database (Supabase / Postgres).
-- Run ONCE: Supabase project → SQL Editor → paste this whole file → Run.
-- Safe to re-run: it only creates what is missing.

create table if not exists events (
  id              uuid primary key,
  t               timestamptz not null,                 -- when the event happened
  type            text not null,                        -- fall | inactivity | ...
  confidence      real,                                 -- model score (null for rule-based events)
  detector        text,                                 -- e.g. "watch forest v0.4", "typed demo"
  status          text not null default 'open'
                  check (status in ('open', 'acknowledged', 'resolved', 'false_alarm')),
  duration_min    real,                                 -- for inactivity events
  snippet         jsonb,                                -- {"t0_s": -5, "hz": 50, "smv": [...]} around the impact
  alerted_at      timestamptz,                          -- caregiver alert sent
  acknowledged_at timestamptz,                          -- caregiver pressed "I'm on it"
  resolved_at     timestamptz
);

create table if not exists checkins (
  id            uuid primary key,
  event_id      uuid references events(id) on delete cascade,
  t             timestamptz not null,
  attempt       int,
  transcript    text,                                   -- what Whisper heard (never the audio)
  reply         text check (reply in ('ok', 'help', 'none')),
  used_fallback boolean,                                -- keyword backup used instead of the LLM
  latency_s     real,                                   -- whole check-in attempt
  llm_s         real                                    -- LLM step only
);

create table if not exists event_steps (
  id       uuid primary key,
  event_id uuid references events(id) on delete cascade,
  t        timestamptz not null,
  text     text not null                                -- one line of the "Event detail" timeline
);

create table if not exists status (                     -- exactly one row (id = 1), updated by the laptop
  id                   int primary key default 1 check (id = 1),
  base_station_seen_at timestamptz,                     -- heartbeat, every ~30 s
  watch_last_data_at   timestamptz,                     -- last sensor sample received
  battery_pct          real                             -- empty until the watch reports battery (Obj 1)
);

create table if not exists activity_minutes (           -- one row per minute, from the detector (later)
  minute  timestamptz primary key,
  label   text check (label in ('sleeping', 'resting', 'light', 'active')),
  enmo_mg real
);

create index if not exists events_t_idx      on events (t desc);
create index if not exists checkins_event_idx on checkins (event_id);
create index if not exists steps_event_idx    on event_steps (event_id);

-- Privacy: row-level security ON with no policies = the public ("anon"/publishable) key can read NOTHING.
-- Only the secret key (kept in .env on the laptop and in the dashboard's Secrets) can read/write.
alter table events           enable row level security;
alter table checkins         enable row level security;
alter table event_steps      enable row level security;
alter table status           enable row level security;
alter table activity_minutes enable row level security;

grant select, insert, update, delete on events, checkins, event_steps, status, activity_minutes to service_role;
