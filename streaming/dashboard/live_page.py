"""live_page.py: a small Streamlit page that shows the watch's LIVE stream from Firestore.

WHAT THIS FILE DOES
    The browser view of the stream: is the watch online, is the wearer still or moving and
    since when, how the last 24 h were split between still and moving, every change in that
    time, and any falls in the last 7 days. It refreshes itself every few seconds.
    It is a simple test/demo page for the streaming path. The real caregiver dashboard
    (basestation/dashboard/app.py) should later read the same data through firestore_source.py.

Run (from the project root):
    gcloud auth application-default login          # once, so the page can read Firestore
    streamlit run streaming/dashboard/live_page.py -- --project <gcp-project-id>

Online (Streamlit Cloud): put in the app's Secrets
    gcp_project = "<project id>"
    dashboard_password = "<password>"
    [gcp_service_account]   ...the service-account JSON fields...
"""
from __future__ import annotations

import argparse
import hmac
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import firestore_source as fs  # noqa: E402

REFRESH_S = 5


def secret(name: str, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:          # no secrets file at all
        return default


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--project", default=secret("gcp_project"))
    p.add_argument("--device", default="watch-01")
    return p.parse_known_args(sys.argv[1:])[0]


def password_gate() -> None:
    pw = secret("dashboard_password")
    if not pw or st.session_state.get("authed"):
        return
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, pw):
        st.session_state.authed = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    st.stop()


@st.cache_resource
def db(project: str | None):
    info = secret("gcp_service_account")
    return fs.client(project, dict(info) if info else None)


def ago(t: datetime | None) -> str:
    if not t:
        return "never"
    s = int((datetime.now(timezone.utc) - t).total_seconds())
    return f"{s} s ago" if s < 120 else f"{s // 60} min ago" if s < 7200 else f"{s // 3600} h ago"


def local(t: datetime) -> str:
    return t.astimezone().strftime("%a %H:%M:%S")


st.set_page_config(page_title="SilverGuard live", layout="centered")
password_gate()
a = args()
st.title("SilverGuard: live from the watch")
if not a.project:
    st.error("No Google Cloud project given. Run with `-- --project <id>` or set gcp_project in secrets.")
    st.stop()


@st.fragment(run_every=REFRESH_S)
def live_view():
    try:
        d = fs.live(db(a.project), a.device)
    except Exception as e:  # noqa: BLE001  (show any connection problem on the page)
        st.error(f"Can't read Firestore: {e}")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Watch", "Online" if d["online"] else "Offline", f"seen {ago(d.get('last_seen'))}",
              delta_color="off")
    c2.metric("Now", (d["state"] or "unknown").capitalize(),
              f"since {local(d['since'])}" if d.get("since") else "", delta_color="off")
    c3.metric("Last fall", ago(d.get("last_fall_at")), delta_color="off")
    if not d["online"]:
        st.warning("No message from the watch for over 2.5 minutes: off, out of Wi-Fi, or out of battery.")
    if d["state_mismatch"]:
        st.info(f"The watch's last heartbeat says **{d['heartbeat_state']}**: a change message was lost.")


@st.fragment(run_every=30)
def history_view():
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=24)
    try:
        changes = fs.transitions(db(a.project), a.device, start)
        fall_list = fs.falls(db(a.project), a.device)
    except Exception as e:  # noqa: BLE001
        st.error(f"Can't read Firestore: {e}")
        return

    st.subheader("Last 24 hours")
    split = fs.time_in_state(changes, start, now)
    total = sum(split.values()) or 1
    cols = st.columns(len(split))
    for col, (state, secs) in zip(cols, sorted(split.items())):
        col.metric(state.capitalize(), f"{secs / 3600:.1f} h", f"{100 * secs / total:.0f}%",
                   delta_color="off")

    with st.expander(f"All {len(changes)} changes"):
        for when, state in reversed(changes):
            st.write(f"{local(when)} → **{state}**")

    st.subheader("Falls (last 7 days)")
    if not fall_list:
        st.write("None.")
    for f in fall_list:
        st.write(f"🚨 {local(f['t'])}: score {f.get('confidence')}, peak {f.get('peak_g')} g, "
                 f"status **{f.get('status')}**")


live_view()
history_view()
