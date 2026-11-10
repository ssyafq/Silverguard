"""SilverGuard caregiver dashboard (Obj 2), built from the 'Caregiver dashboard' PNG design.

Run locally (from the project folder):
    .venv\\Scripts\\streamlit run basestation/dashboard/app.py

Uses built-in sample data until Supabase keys are set in .streamlit/secrets.toml
(locally) or in the Streamlit Cloud app's Secrets (online).
"""

import base64
import hmac
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:               # so "import basestation..." works on Streamlit Cloud too
    sys.path.insert(0, str(ROOT))

import plotly.graph_objects as go
import streamlit as st
import yaml

from basestation.dashboard import metrics as M
from basestation.dashboard.data import SampleSource, SupabaseSource

st.set_page_config(page_title="SilverGuard", page_icon="🛡️", layout="wide", initial_sidebar_state="collapsed")

COLORS = {"sleeping": "#2E3A78", "resting": "#B8CEC6", "light": "#5FA898", "active": "#135C4A", "flagged": "#E0702A"}
BRAND, INK2, FAINT = "#17695A", "#5C5B54", "#C9C6BF"
PLOT_CONFIG = {"displayModeBar": False}


# ── settings & secrets ──────────────────────────────────────────────────────

@st.cache_data
def load_config() -> tuple[dict, float]:
    cfg = yaml.safe_load((ROOT / "basestation" / "config.yaml").read_text(encoding="utf-8"))
    return cfg["dashboard"], float(cfg["fall"]["impact_threshold_g"])


CFG, THRESHOLD_G = load_config()
OFFSET = float(CFG["utc_offset_hours"])
ELDER = escape(CFG["elder_name"])


def secret(name: str, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:                       # no secrets file at all
        return default


def password_gate() -> None:
    pw = secret("dashboard_password")
    if not pw or st.session_state.get("authed"):
        return
    st.markdown("### SilverGuard")
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, pw):
        st.session_state.authed = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    st.stop()


@st.cache_resource
def live_source(url: str, key: str) -> SupabaseSource:
    return SupabaseSource(url, key)


@st.cache_data(ttl=5, show_spinner=False)
def load_live(url: str, key: str) -> dict:
    return live_source(url, key).load()


def tlocal(t: datetime, fmt: str = "%H:%M") -> str:
    return M.local(t, OFFSET).strftime(fmt)


def html(s: str) -> None:
    st.html(s)


# ── sections ────────────────────────────────────────────────────────────────

def icon(paths: str, color: str, size: int = 24, cls: str = "banner-icon") -> str:
    """Line icon as an <img> (st.html strips inline <svg>, but keeps images)."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')
    return f'<img class="{cls}" width="{size}" height="{size}" alt="" src="data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}">'


_PULSE = '<path d="M3 12h4l2-5 4 10 2-5h6"/>'
_TRIANGLE = ('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'
             '<path d="M12 9v4"/><path d="M12 17h.01"/>')
LOGO = icon(_PULSE, "#FFFFFF", 22, cls="")
WARN_ALERT, WARN_BAD = icon(_TRIANGLE, "#8A2E0C"), icon(_TRIANGLE, "#9B1C14")
CHECK_ICON = icon('<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>', "#17695A")
CHART_FONT = dict(family="IBM Plex Sans, Arial, sans-serif", size=12, color=INK2)


def render_header(conn: dict | None, is_sample: bool) -> None:
    chips = ['<span class="chip sample">Sample data</span>' if is_sample else '<span class="chip">Live data</span>']
    if conn is None:
        chips.append('<span class="chip bad"><span class="dot bad"></span>Database unreachable</span>')
    else:
        if conn["base_ok"]:
            chips.append('<span class="chip"><span class="dot"></span>Base station online</span>')
        else:
            age = M.fmt_age(conn["base_age_s"]) if conn["base_age_s"] is not None else "never connected"
            chips.append(f'<span class="chip bad"><span class="dot bad"></span>Base station offline · {age}</span>')
        if conn["watch_state"] == "ok":
            chips.append(f'<span class="chip"><span class="dot"></span>Watch connected · data {M.fmt_age(conn["watch_age_s"])}</span>')
        elif conn["watch_state"] == "stale":
            chips.append(f'<span class="chip bad"><span class="dot bad"></span>Watch: no data · {M.fmt_age(conn["watch_age_s"])}</span>')
        else:
            chips.append('<span class="chip"><span class="dot grey"></span>Watch not connected yet</span>')
        batt = conn["battery_pct"]
        chips.append(f'<span class="chip" title="The watch does not report battery yet (needs Obj 1)">'
                     f'Battery {f"{batt:.0f}%" if batt is not None else "—"}</span>')
    html(f"""<div class="sg-header">
      <div class="sg-brand"><div class="sg-logo">{LOGO}</div>
        <div><div class="sg-title">SilverGuard</div>
        <div class="sg-sub">Monitoring {ELDER} · Caregiver: {escape(CFG['caregiver_name'])}</div></div></div>
      <div class="sg-chips">{''.join(chips)}</div></div>""")


def call_button(key: str) -> None:
    phone = secret("elder_phone")
    st.link_button(f"Call {CFG['elder_name']}", f"tel:{phone or ''}", disabled=not phone, key=key,
                   help=None if phone else "Add elder_phone to the dashboard secrets to enable this",
                   width="stretch")


def render_connection_banners(conn: dict) -> None:
    """The 'can't see the person' states. A dead link must never look like 'all fine'."""
    if not conn["base_ok"]:
        since = f"since {tlocal(conn['base_seen_at'])} ({M.fmt_age(conn['base_age_s'])})" if conn["base_seen_at"] else "yet"
        with st.container(key="banner_bad"):
            c1, c2 = st.columns([6, 1.4], vertical_alignment="center")
            c1.html(f"""<div class="banner bad">{WARN_BAD}<div>
              <div class="banner-title">Can't see {ELDER}: no data from the base station {since}</div>
              <div class="banner-text">The laptop at home may be off, asleep or offline. Until it is back,
              falls can't be detected or checked. Please call {ELDER} or check the base station.</div></div></div>""")
            with c2:
                call_button("call_btn_bad")
    elif conn["watch_state"] == "stale":
        with st.container(key="banner_watch"):
            html(f"""<div class="banner bad">{WARN_BAD}<div>
              <div class="banner-title">Can't see {ELDER}'s movement: the watch stopped sending data {M.fmt_age(conn['watch_age_s'])}</div>
              <div class="banner-text">It may be off the wrist, out of battery or out of range.
              Falls can't be detected until it reconnects.</div></div></div>""")


def render_event_banner(source, data: dict) -> None:
    e = M.active_alert(data["events"])
    if e is None:
        return
    title = M.event_title(e)
    when = tlocal(e["t"])
    if e["status"] == "acknowledged":
        with st.container(key="banner_handling"):
            c1, c2 = st.columns([6, 1.4], vertical_alignment="center")
            c1.html(f"""<div class="banner handling">{CHECK_ICON}<div>
              <div class="banner-title">You're handling the {title.lower()} at {when}</div>
              <div class="banner-text">Acknowledged at {tlocal(e['acknowledged_at'], '%H:%M:%S')}.
              Mark it resolved when {ELDER} is safe.</div></div></div>""")
            if c2.button("Mark resolved", key="resolve_banner", width="stretch"):
                source.resolve(e["id"])
                refresh()
        return

    cs = M.checkins_for(e["id"], data["checkins"])
    reply = cs[-1]["reply"] if cs else None
    if reply == "help":
        headline, said = "asked for help", f"They said {escape(M.what_they_said(e, data['checkins']))}. "
    elif reply == "none":
        headline, said = "no reply to check-in", f"Asked {'twice' if len(cs) == 2 else f'{len(cs)}×'}, no answer. "
    else:
        headline, said = "checking in now", ""
    alerted = (f"You were alerted at {tlocal(e['alerted_at'], '%H:%M:%S')} "
               f"({M.fmt_age((datetime.now(timezone.utc) - e['alerted_at']).total_seconds())}). Not acknowledged yet."
               if e.get("alerted_at") else "The base station is talking to them now.")
    with st.container(key="banner_alert"):
        c1, c2, c3 = st.columns([6, 1.1, 1.4], vertical_alignment="center")
        c1.html(f"""<div class="banner alert">{WARN_ALERT}<div>
          <div class="banner-title">{title} at {when}: {headline}</div>
          <div class="banner-text">{said}{alerted}</div></div></div>""")
        if c2.button("I'm on it", key="ack_btn", width="stretch"):
            source.acknowledge(e["id"])
            refresh()
        with c3:
            call_button("call_btn_alert")


def card(key: str, label: str, value: str, sub: str, word: bool = False) -> None:
    with st.container(key=key):
        html(f"""<div class="card-label">{label}</div>
          <div class="card-value{' word' if word else ''}">{value}</div>
          <div class="card-sub">{sub}</div>""")


def render_cards(data: dict, now: datetime) -> None:
    act, events = data["activity"], data["events"]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        rn = M.right_now(act, now)
        alert = M.active_alert(events)
        if rn:
            label, since = rn
            after = " · after fall event" if alert and alert["type"] == "fall" and since >= alert["t"] - timedelta(minutes=2) else ""
            card("card_now", "Right now", M.LABEL_NAMES[label], f"Since {tlocal(since)}{after}", word=True)
        else:
            card("card_now", "Right now", "No data", "No activity data in the last 5 min", word=True)
    with c2:
        a = M.active_24h(act, now)
        if a["has_data"]:
            card("card_active", "Active, last 24 h", M.fmt_hm(a["total"]),
                 f"Active {M.fmt_hm(a['active'])} · light {M.fmt_hm(a['light'])}")
        else:
            card("card_active", "Active, last 24 h", "—", "No activity data yet")
    with c3:
        s = M.sleep_last_night(act, now, OFFSET)
        if s:
            card("card_sleep", "Sleep, last night", M.fmt_hm(s["total_min"]),
                 f"{tlocal(s['start'])} – {tlocal(s['end'])} · {s['wakeups']} wake-up{'s' if s['wakeups'] != 1 else ''}")
        else:
            card("card_sleep", "Sleep, last night", "—", "No sleep data")
    with c4:
        c = M.week_counts(events, now)
        parts = [(c["open"] + c["acknowledged"], "open"), (c["false_alarm"], "false alarm"), (c["resolved"], "resolved")]
        sub = " · ".join(f"{n} {w}" for n, w in parts if n) or "None this week"
        card("card_events", "Events this week", str(sum(c.values())), sub)


def activity_figure(runs, events, start, end) -> go.Figure:
    to_local = lambda t: M.local(t, OFFSET).replace(tzinfo=None)  # noqa: E731
    fig = go.Figure()

    def bars(spans, color, name, hover):
        fig.add_bar(
            y=["a"] * len(spans), orientation="h",
            x=[(b - a).total_seconds() * 1000 for a, b in spans], base=[to_local(a) for a, _ in spans],
            marker=dict(color=color, line=dict(color="#FFFFFF", width=2)), name=name,
            customdata=hover, hovertemplate="%{customdata}<extra></extra>")

    for label in M.LABELS:
        spans = [(a, b) for a, b, lab in runs if lab == label]
        if spans:
            bars(spans, COLORS[label], M.LABEL_NAMES[label],
                 [f"<b>{M.LABEL_NAMES[label]}</b><br>{tlocal(a)} – {tlocal(b)} · {M.fmt_hm((b - a).total_seconds() / 60)}"
                  for a, b in spans])

    flagged, hover = [], []
    for e in events:
        if e["type"] == "inactivity" and e.get("duration_min"):
            a, b = e["t"] - timedelta(minutes=e["duration_min"]), e["t"]
        else:
            a, b = e["t"] - timedelta(minutes=5), e["t"] + timedelta(minutes=5)   # 10 min wide so it's visible
        if b > start and a < end:
            flagged.append((max(a, start), min(b, end)))
            hover.append(f"<b>{M.event_title(e)}</b><br>{tlocal(e['t'])} · {M.outcome(e)[0]}")
    if flagged:
        bars(flagged, COLORS["flagged"], "Flagged event", hover)

    # Labels every 6 h on round hours (fits a phone), skipping any too close to the edges.
    first = to_local(start).replace(minute=0, second=0) + timedelta(hours=1)
    ticks = [first + timedelta(hours=h) for h in range(0, 25)
             if (first + timedelta(hours=h)).hour % 6 == 0
             and to_local(start) + timedelta(minutes=40) < first + timedelta(hours=h) < to_local(end) - timedelta(minutes=40)]
    fig.update_layout(
        barmode="overlay", bargap=0, height=80, margin=dict(l=0, r=0, t=0, b=24), showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=CHART_FONT,
        xaxis=dict(type="date", range=[to_local(start), to_local(end)], showgrid=False, zeroline=False,
                   tickvals=ticks, ticktext=[f"{t:%H:%M}" for t in ticks], ticks="",
                   tickfont=dict(family="IBM Plex Mono, monospace", size=11, color=INK2)),
        yaxis=dict(visible=False, fixedrange=True),
        hoverlabel=dict(bgcolor="white", bordercolor="#E7E4DF", font=dict(family="IBM Plex Sans", size=12, color="#1F1F1C")),
    )
    fig.update_xaxes(fixedrange=True)
    return fig


def render_activity(data: dict, now: datetime) -> None:
    start = now - timedelta(hours=24)
    runs = M.activity_runs(data["activity"], start, now)
    with st.container(key="card_activity"):
        html("""<div class="section-head"><div class="section-title">Activity, last 24 hours</div>
          <div class="section-note">Worked out every minute from the watch's data</div></div>""")
        if not runs:
            html('<div class="empty">No activity data yet. This fills in once the sensor and detector are connected.</div>')
            return
        st.plotly_chart(activity_figure(runs, data["events"], start, now), config=PLOT_CONFIG, width="stretch")
        legend = "".join(f'<span><span class="swatch" style="background:{COLORS[k]}"></span>{name}</span>'
                         for k, name in [*M.LABEL_NAMES.items(), ("flagged", "Flagged event")])
        html(f'<div class="legend">{legend}</div>')
        with st.expander("Show as table"):
            st.dataframe(
                [{"From": tlocal(a), "To": tlocal(b), "State": M.LABEL_NAMES[lab],
                  "Duration": M.fmt_hm((b - a).total_seconds() / 60)} for a, b, lab in runs],
                hide_index=True, width="stretch")


def render_event_log(data: dict, now: datetime) -> None:
    events = sorted(data["events"], key=lambda e: e["t"], reverse=True)[:8]
    with st.container(key="card_log"):
        html('<div class="section-title">Event log</div>')
        if not events:
            html('<div class="empty">No events in the last 7 days.</div>')
            return
        rows = []
        for e in events:
            text, kind = M.outcome(e)
            conf = f"{e['confidence']:.2f}" if e.get("confidence") is not None else "—"
            rows.append(f"""<tr class="{'open' if e['status'] == 'open' else ''}">
              <td class="mono when">{M.fmt_event_time(e['t'], now, OFFSET)}</td>
              <td class="event">{escape(M.event_title(e))}</td>
              <td class="mono conf">{conf}</td>
              <td class="said">{escape(M.what_they_said(e, data['checkins']))}</td>
              <td><span class="pill {kind}">{escape(text)}</span></td></tr>""")
        html(f"""<div class="evlog-wrap"><table class="evlog"><thead><tr><th>Time</th><th>Event</th><th class="conf">Model confidence</th>
          <th>What they said</th><th>Outcome</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
          <div class="footnote">“What they said” is the speech-to-text of the check-in reply. The LLM only sorts it
          into ok / help / no reply; fixed rules decide whether to alert the caregiver.</div>""")


def snippet_figure(snippet: dict) -> go.Figure:
    smv = snippet["smv"]
    t = [snippet["t0_s"] + i / snippet["hz"] for i in range(len(smv))]
    fig = go.Figure(go.Scatter(x=t, y=smv, mode="lines", line=dict(color=BRAND, width=2),
                               hovertemplate="%{x:+.2f} s · %{y:.2f} g<extra></extra>"))
    fig.add_hline(y=THRESHOLD_G, line=dict(color=COLORS["flagged"], width=1.5, dash="dash"),
                  annotation_text=f"{THRESHOLD_G:g} g threshold", annotation_position="top left",
                  annotation_font=dict(size=11, color=INK2, family="IBM Plex Sans"))
    fig.add_hline(y=1, line=dict(color=FAINT, width=1, dash="dot"), annotation_text="1 g",
                  annotation_position="bottom left", annotation_font=dict(size=11, color=INK2))
    fig.add_vline(x=0, line=dict(color=FAINT, width=1, dash="dot"))
    fig.update_layout(
        height=200, margin=dict(l=0, r=0, t=8, b=22), showlegend=False, hovermode="x",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=CHART_FONT,
        xaxis=dict(range=[t[0], t[-1]], tickvals=[-5, 0, 5], ticktext=["−5 s", "0", "+5 s"], showgrid=False,
                   zeroline=False, fixedrange=True, showspikes=True, spikemode="across", spikethickness=1,
                   spikecolor=FAINT, spikedash="solid", tickfont=dict(family="IBM Plex Mono", size=11, color=INK2)),
        yaxis=dict(visible=False, range=[0, max(3.6, max(smv) + 0.3)], fixedrange=True),
        hoverlabel=dict(bgcolor="white", bordercolor="#E7E4DF", font=dict(family="IBM Plex Mono", size=12)),
    )
    return fig


def render_event_detail(source, data: dict, now: datetime) -> None:
    events = sorted(data["events"], key=lambda e: e["t"], reverse=True)[:8]
    with st.container(key="card_detail"):
        if not events:
            html('<div class="section-title">Event detail</div><div class="empty">Nothing to show yet.</div>')
            return
        by_id = {e["id"]: e for e in events}
        alert = M.active_alert(events)
        default = (alert or events[0])["id"]
        ids = list(by_id)
        chosen = st.selectbox("Show details for", ids, index=ids.index(default), key="detail_pick",
                              format_func=lambda i: f"{M.fmt_event_time(by_id[i]['t'], now, OFFSET)} · {M.event_title(by_id[i])}")
        e = by_id.get(chosen, by_id[default])
        sn = e.get("snippet")
        note = f"{len(sn['smv']) / sn['hz']:.0f} s raw snippet sent with the event" if sn else "No movement snippet for this event"
        html(f"""<div class="section-title">Event {tlocal(e['t'])} · {escape(M.event_title(e))}</div>
          <div class="section-note">{note}</div>""")
        if sn:
            st.plotly_chart(snippet_figure(sn), config=PLOT_CONFIG, width="stretch", key=f"snip_{e['id']}")
            html('<div class="explain">Acceleration magnitude (SMV) around the event. A real fall usually shows a dip '
                 '(falling), a spike (impact), then little movement.</div>')
        steps = M.steps_for(e["id"], data["steps"])
        if steps:
            html('<div class="steps">' + "".join(
                f'<div class="time">{tlocal(s["t"], "%H:%M:%S")}</div><div>{escape(s["text"])}</div>' for s in steps)
                + "</div>")
        if e["status"] in ("open", "acknowledged") and (alert is None or e["id"] != alert["id"]):
            if st.button("Mark resolved", key=f"resolve_{e['id']}"):
                source.resolve(e["id"])
                refresh()


def render_health(data: dict, now: datetime, is_sample: bool) -> None:
    h = M.health(data["events"], data["checkins"], data["activity"], now)
    llm = f"median · LLM step {h['llm_s']:.1f} s" if h["llm_s"] is not None else "median, last 7 days"
    tiles = [
        ("False alarms per day", f"{h['false_alarms_per_day']:.2f}", "falls where they said they're OK"),
        ("Fall → caregiver alerted", M.fmt_dur(h["fall_to_alert_s"]), llm),
        ("Watch uptime", f"{h['uptime_pct']:.1f}%" if h["uptime_pct"] is not None else "—", "minutes with sensor data"),
        ("Avg battery life", "—", "needs battery data from the watch"),
        ("Caregiver response time", M.fmt_dur(h["caregiver_response_s"]), "alert → “I'm on it”, median"),
    ]
    with st.container(key="card_health"):
        html(f"""<div class="section-head"><div class="section-title">System health, last 7 days</div>
          <div class="section-note">Evaluation numbers{' (sample data)' if is_sample else ', measured on the live system'}</div></div>
          <div class="tiles">{''.join(f'<div class="tile"><div class="tile-label">{a}</div><div class="tile-value">{b}</div><div class="tile-sub">{c}</div></div>' for a, b, c in tiles)}</div>""")


def refresh() -> None:
    load_live.clear()
    st.rerun()


# ── page ────────────────────────────────────────────────────────────────────

st.markdown(f"<style>{(Path(__file__).with_name('style.css')).read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True)
password_gate()

URL, KEY = secret("SUPABASE_URL"), secret("SUPABASE_SECRET_KEY")
with st.sidebar:
    st.markdown("**Data source**")
    use_sample = st.toggle("Use sample data", value=not (URL and KEY), disabled=not (URL and KEY),
                           help=None if (URL and KEY) else "Add Supabase keys to the secrets to use live data")
    if use_sample:
        st.markdown("**Demo switches** (sample data only)")
        sim_base = st.checkbox("Base station offline")
        sim_watch = st.checkbox("Watch disconnected")
        if st.button("Reset sample data"):
            st.session_state.pop("sample_source", None)


@st.fragment(run_every=CFG["refresh_s"])
def body() -> None:
    now = datetime.now(timezone.utc)
    if use_sample:
        if "sample_source" not in st.session_state:
            st.session_state.sample_source = SampleSource(OFFSET)
        source = st.session_state.sample_source
        source.simulate(sim_base, sim_watch)
        data = source.load()
    else:
        source = live_source(URL, KEY)
        try:
            data = load_live(URL, KEY)
        except Exception as err:            # never show stale numbers as if they were fine
            render_header(None, False)
            st.error(f"Can't reach the database, so this page can't show {CFG['elder_name']}'s status. "
                     f"Details: {err}")
            return

    conn = M.connection_state(data["status"], now, CFG["base_station_stale_s"], CFG["watch_stale_s"])
    render_header(conn, source.is_sample)
    render_connection_banners(conn)
    render_event_banner(source, data)
    render_cards(data, now)
    render_activity(data, now)
    left, right = st.columns([1.62, 1], gap="medium")
    with left:
        render_event_log(data, now)
    with right:
        render_event_detail(source, data, now)
    render_health(data, now, source.is_sample)
    html('<div class="disclaimer">SilverGuard is a student prototype. It is not a medical device or a certified '
         'fall detector, and it never contacts emergency services.</div>')


body()
