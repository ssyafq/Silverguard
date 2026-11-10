# SilverGuard caregiver dashboard: putting it online

The dashboard works straight away with **sample data**. These steps connect it to real data and put it online. Do them in order; each one can be tested on its own.

```
HOME LAPTOP (base station)                         ONLINE
voice check-in ── events, check-ins, heartbeat ──► Supabase (database) ◄── Streamlit dashboard
      └──────── alert ──► Telegram bot ──► caregiver's phone
```

## 0. Run it locally (sample data)
```
.venv\Scripts\streamlit run basestation/dashboard/app.py
```
Open http://localhost:8501. The sidebar (») has demo switches for the "base station offline" and "watch disconnected" states.

## 1. Database: Supabase (free)
1. Sign up at supabase.com and create a **new project**. Pick the Singapore region if it's offered, and save the database password somewhere safe.
2. **SQL Editor** → paste all of `basestation/sync/schema.sql` → **Run**.
3. **Project Settings → API keys**: copy the **Project URL** and a **secret key** (not the publishable/anon one).
4. Add them to `.env` on the laptop:
   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_SECRET_KEY=...
   ```
5. Add the same two lines to `.streamlit/secrets.toml` (copy `.streamlit/secrets.toml.example`).
6. Test: run `python -m basestation.voice.demo --typed`, type `fall`, reply `help me`. The rows should appear in Supabase → **Table Editor**, and in the dashboard (switch off "Use sample data" in the sidebar).

## 2. Alerts: Telegram bot (free)
Follow the steps at the top of `basestation/alerts/telegram.py`. In short: create a bot with @BotFather, put the token in `.env`, message the bot, run `--find-chat-id`, put the id in `.env`, then run `--test`.

## 3. Put the dashboard online: Streamlit Community Cloud (free)
1. Push the project to a **private** GitHub repo. `.gitignore` already keeps out `.env`, `secrets.toml`, recordings and the big datasets. Check `git status` before the first commit anyway.
2. Go to share.streamlit.io → **Create app** → choose the repo, branch `main`, main file `basestation/dashboard/app.py`.
3. **Advanced settings → Secrets**: paste your `secrets.toml` contents, including a `dashboard_password`.
4. After deploying, make the app **private** (only invited viewers) and paste its link into `config.yaml` → `dashboard.url`, so Telegram alerts link to it.

Free-tier notes: the app **sleeps after ~12 h with no visitors** (the first visit then takes ~30 s), so open it before a demo. Alerts don't depend on it, because they go straight from the laptop to Telegram.

## Where each secret lives
| Secret | Laptop `.env` | Dashboard secrets |
|---|---|---|
| Claude API key | yes | **never** |
| Supabase URL + secret key | yes | yes |
| Telegram token + chat id | yes | no |
| Dashboard password, elder's phone | no | yes |

## Privacy
Only text goes online (events, transcripts, status). Audio never leaves the laptop. Use placeholder names in demos. Row-level security is on, so the public key can't read anything.
