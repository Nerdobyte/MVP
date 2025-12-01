# app.py (cleaned & fixed)
import os
import uuid
from datetime import datetime
from io import BytesIO
import json
import tempfile

import streamlit as st
import pandas as pd
import plotly.express as px
import qrcode

# Firebase admin
import firebase_admin
from firebase_admin import credentials, db

# Auto load .env (local testing)
from dotenv import load_dotenv
load_dotenv()

# ---------------------------
# Helpers: refresh + QR + voter id
# ---------------------------
def trigger_refresh():
    """Trigger a Streamlit rerun by changing a dummy query param."""
    params = dict(st.query_params)
    params["refresh"] = [str(uuid.uuid4())]
    st.query_params = params

def render_qr(url):
    qr = qrcode.QRCode(box_size=5, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---------------------------
# Stable anonymous voter ID (query param + session_state)
# ---------------------------
if "voter_id" not in st.session_state:
    # try to read vid from URL query params
    vid = None
    params = dict(st.query_params)
    if "vid" in params:
        v = params.get("vid")
        # st.query_params values can be list or string depending on assignment
        vid = v[0] if isinstance(v, (list, tuple)) else v

    if vid:
        st.session_state["voter_id"] = vid
    else:
        new_id = str(uuid.uuid4())
        st.session_state["voter_id"] = new_id
        params["vid"] = [new_id]
        st.query_params = params  # write back to URL so reloads preserve the id

VOTER_ID = st.session_state["voter_id"]

# ---------------------------
# Config / secrets (Streamlit Cloud friendly)
# ---------------------------
if "active_tab" not in st.session_state:
    st.session_state.active_tab = 0

# When deploying on Streamlit Cloud, store FIREBASE credentials in st.secrets
# Locally you can fall back to environment vars or a local file.
try:
    firebase_json = st.secrets["FIREBASE"]["CREDENTIALS_JSON"]
    firebase_db_url = st.secrets["FIREBASE"]["DATABASE_URL"]
except Exception:
    # Local fallback (for development)
    firebase_json = os.getenv("FIREBASE_CREDENTIALS_JSON")  # optional
    firebase_db_url = os.getenv("FIREBASE_DATABASE_URL")

STREAMLIT_APP_URL = os.getenv("STREAMLIT_APP_URL", "http://localhost:8501")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "3"))

SECTION_MAPPING = {
    "Segmentation": "section1",
    "Clustering": "section2",
    "Visualization": "section3",
    "Integration": "section4",
    "Domain_detection": "section5",
    "Upscaling": "section6",
    "Annotation": "section7"
}
DEFAULT_SECTIONS = {sid: {"name": name, "tool_ids": {}} for name, sid in SECTION_MAPPING.items()}

# ---------------------------
# Firebase helpers (init using secret JSON string)
# ---------------------------
def init_firebase():
    if firebase_admin._apps:
        return
    if not firebase_json or not firebase_db_url:
        st.error("Firebase credentials or DB URL not configured.")
        st.stop()

    # firebase_json may be either a JSON string or a dict
    if isinstance(firebase_json, dict):
        firebase_json_str = json.dumps(firebase_json)
    else:
        firebase_json_str = firebase_json

    # write temp file and init
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as f:
        f.write(firebase_json_str)
        f.flush()
        cred = credentials.Certificate(f.name)
        firebase_admin.initialize_app(cred, {"databaseURL": firebase_db_url})

def get_db_ref(path="/"):
    return db.reference(path)

def increment_counter_atomic(path, delta):
    ref = get_db_ref(path)
    def transaction_func(current):
        if current is None:
            return delta
        return int(current) + int(delta)
    ref.transaction(transaction_func)

def create_tool_entry(name, tags, section_ids):
    root = get_db_ref("/")
    new_id = str(uuid.uuid4())
    tool_data = {
        "name": name,
        "tags": tags,
        "sections": section_ids,
        "upvotes": 0,
        "downvotes": 0,
        "created_at": datetime.utcnow().isoformat()
    }
    root.child("tools").child(new_id).set(tool_data)
    for s in section_ids:
        root.child("sections").child(s).child("tool_ids").update({new_id: True})
    return new_id

# Comments helpers
def add_comment(tool_id, text, comment_type):
    comment_id = str(uuid.uuid4())
    comment_data = {
        "text": text,
        "type": comment_type,
        "timestamp": datetime.utcnow().isoformat()
    }
    get_db_ref(f"/comments/{tool_id}/{comment_id}").set(comment_data)

def fetch_comments(tool_id):
    return get_db_ref(f"/comments/{tool_id}").get() or {}

# ---------------------------
# Initial seed (CSV) and initial votes
# ---------------------------
INITIAL_VOTES = {
    "ADEPT": 6, "banksy": 127, "BASS": 30, "BayesSpace": 160, "Baysor": 1,
    "Bento": 84, "BoReMi": 4, "CellAgentChat": 33, "Cellcharter": 11, "Cellpose": 1976,
    "CellProfiler": 1066, "CellSymphony": 0, "COMMOT": 125, "DeepST": 86, "DR.SC": 6,
    "Giotto": 17, "GPSA": 32, "GraphST": 138, "GROVER": 1, "HEIST": 0,
    "InSituPy": 28, "iSCALE": 39, "KBC": 1, "LeGO-3D": 0, "LIANA": 224,
    "LLOT": 0, "MagNet": 11, "MISTy": 71, "MOFA": 361, "MOSAIK": 3,
    "MuSpAn": 17, "Niche-DE": 23, "Nicheformer": 123, "PASTE": 96, "PASTE2": 41,
    "PersiST": 16, "PHD-MS": 1, "PRECAST": 12, "scGPT": 114, "scVI": 1503,
    "Segger": 10, "SemST": 4, "SPAC": 9, "SPACEL": 60, "SpaGCN": 0,
    "SPIRAL": 16, "SpOOx": 10, "ST-Align": 91, "STAGATE": 138, "STAligner": 47,
    "stardist": 1130, "stLearn": 0, "STtools": 12, "Tangram": 337, "TensionMap": 8,
    "Thor": 24, "TopoVelo": 13, "VoxelEmbed": 0, "Cell2Spatial": 2
}

def seed_defaults_from_excel(excel_path="tools.csv"):
    root = get_db_ref("/")
    if not root.child("sections").get():
        root.child("sections").set(DEFAULT_SECTIONS)
    if root.child("tools").get():
        return
    try:
        df = pd.read_csv(excel_path)
    except FileNotFoundError:
        # No CSV — skip local seeding
        st.info("No tools.csv found locally; skipping CSV seed.")
        return

    tools_obj = {}
    for _, row in df.iterrows():
        tool_name = row["Tool name"].strip()
        section_ids = [SECTION_MAPPING[col] for col in SECTION_MAPPING if row.get(col, 0) == 1]
        if not section_ids:
            continue
        tags = [DEFAULT_SECTIONS[sid]["name"] for sid in section_ids]
        tid = str(uuid.uuid4())
        upvotes = INITIAL_VOTES.get(tool_name, 0)
        tools_obj[tid] = {
            "name": tool_name,
            "tags": tags,
            "sections": section_ids,
            "upvotes": upvotes,
            "downvotes": 0,
            "created_at": datetime.utcnow().isoformat()
        }
        for sec_id in section_ids:
            root.child("sections").child(sec_id).child("tool_ids").update({tid: True})
    root.child("tools").set(tools_obj)
    st.success(f"Seeded {len(tools_obj)} tools from CSV with initial votes")

# ---------------------------
# UI helpers
# ---------------------------
def compute_score(tool):
    return int(tool.get("upvotes", 0)) - int(tool.get("downvotes", 0))

def tools_df_from_db(tools_dict, sections_dict=None):
    rows = []
    for tid, t in tools_dict.items():
        if sections_dict:
            section_names = [sections_dict.get(sid, {}).get("name", sid) for sid in t.get("sections", [])]
        else:
            section_names = t.get("sections", [])
        rows.append({
            "tool_id": tid,
            "name": t.get("name"),
            "tags": ", ".join(t.get("tags", [])),
            "sections": ", ".join(section_names),
            "upvotes": int(t.get("upvotes", 0)),
            "downvotes": int(t.get("downvotes", 0)),
            "score": compute_score(t),
            "created_at": t.get("created_at", "")
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["score", "upvotes"], ascending=[False, False])
    return df

# Single, definitive render_tool_row (includes vote-history check + comments preview)
def render_tool_row(tool_row, section_id=None, context=""):
    unique_key = f"{context}_{tool_row['tool_id']}_{section_id}"
    up_key, down_key, score_key = f"up_{unique_key}", f"down_{unique_key}", f"score_{unique_key}"

    if score_key not in st.session_state:
        st.session_state[score_key] = tool_row['score']

    c_name, c_tags, c_score, c_up, c_down = st.columns([3, 2, 1, 1, 1])
    c_name.write(f"**{tool_row['name']}**")
    c_tags.write(tool_row['tags'])
    c_score.write(st.session_state[score_key])

    vote_ref = get_db_ref(f"/votes/{tool_row['tool_id']}/{VOTER_ID}")
    if vote_ref.get():
        c_up.button("👍", disabled=True, key=up_key + "_disabled")
        c_down.button("👎", disabled=True, key=down_key + "_disabled")
    else:
        if c_up.button("👍", key=up_key):
            increment_counter_atomic(f"/tools/{tool_row['tool_id']}/upvotes", 1)
            vote_ref.set({"type": "up", "timestamp": datetime.utcnow().isoformat()})
            st.session_state[score_key] += 1
        if c_down.button("👎", key=down_key):
            increment_counter_atomic(f"/tools/{tool_row['tool_id']}/downvotes", 1)
            vote_ref.set({"type": "down", "timestamp": datetime.utcnow().isoformat()})
            st.session_state[score_key] -= 1

# ---------------------------
# App start: page config, consent, firebase init + seed
# ---------------------------
st.set_page_config(page_title="Spatial Multiomics Voting Platform", layout="wide")
st.write(f"Your voter ID: {VOTER_ID}")  # optional: remove in production

# Consent modal / page
if "consent_given" not in st.session_state:
    st.session_state.consent_given = False

if not st.session_state.consent_given:
    st.markdown("""
    # Privacy Notice

    This privacy notice explains how the University of Oxford processes your personal data when you use the research voting tool available on this website.  
    The Data Controller is the University of Oxford.  
    Contact: data.protection@admin.ox.ac.uk.

    ## What data we collect

    When you access the website, we automatically collect the following technical data:

    - IP address (stored as a pseudonymised, non-reversible hash)
    - Browser fingerprint (also pseudonymised)
    - Timestamp and basic device metadata (e.g., browser type)

    If you choose to submit feedback, we may also collect your email address.

    ## Purpose and lawful basis

    We process this data to:

    - ensure the security and integrity of the voting system (fraud prevention)
    - understand usage patterns (analytics)
    - improve the research platform

    Our lawful basis is: Article 6(1)(e) – task carried out in the public interest (research).  
    For optional contact details where you submit feedback: Article 6(1)(a) – consent.

    ## How long we keep your data

    Technical identifiers (IP hash, browser hash, logs) are kept for 24 hours for fraud-prevention and rate-limiting. Feedback emails (if provided) are stored for up to 12 months and then securely deleted.

    ## Who has access to your data

    Access is restricted to members of the University research team. Data is stored on Google Firebase (Google Cloud) acting as a data processor.

    ## International transfers

    Data is stored and processed using Google Firebase, a third-party data processor. Google may transfer and store data in any country where it or its subprocessors operate. As such, data collected via this tool may be transmitted outside the United Kingdom / European Economic Area.

    The University relies on Google’s data-processing agreement and the UK / EU Standard Contractual Clauses (or equivalent approved transfer frameworks) to ensure an adequate level of data protection in accordance with UK GDPR.

    ## Your rights

    Under UK GDPR you have the right to request access to any personal data we hold about you, to request that it is corrected or deleted, to object to its processing, and to lodge a complaint with the UK Information Commissioner’s Office (ICO). Because the data we collect is limited and pseudonymised, we may not be able to identify you from it, but we will respond to any rights request as far as possible.

    ## Contact

    In the first instance, if you have any questions or concerns about how your personal data is used, please contact (nadia.fernandes@kennedy.ox.ac.uk).

    If you want to exercise any of the rights described above or are dissatisfied with the way we have used your information, you should contact the University’s Information Compliance Team at data.protection@admin.ox.ac.uk. The same email address may be used to contact the University’s Data Protection Officer. We will seek to deal with your request without undue delay, and in any event in accordance with the requirements of the GDPR. Please note that we may keep a record of your communications to help us resolve any issues which you raise.

    If you remain dissatisfied, you have the right to lodge a complaint with the ICO at https://ico.org.uk/concerns/.
    """)
    if st.button("✅ I Agree"):
        st.session_state.consent_given = True
        params = dict(st.query_params)
        params["consent"] = ["yes"]
        st.query_params = params
    st.stop()

st.title("Spatial Multiomics Voting Platform — Live @ Conference")
st.markdown("#### 🧠 Presented at [BSI Congress 2025](https://www.bsicongress.com)")

# Init firebase & optionally seed
try:
    init_firebase()
except Exception as e:
    st.error(f"Firebase init failed: {e}")
    st.stop()

seed_defaults_from_excel("tools.csv")

# Fetch helpers
def list_all_tools_dict():
    init_firebase()
    return get_db_ref("/tools").get() or {}

def list_all_sections_dict():
    init_firebase()
    return get_db_ref("/sections").get() or {}

# Cached fetch
@st.cache_data(ttl=30)
def fetch_tools():
    return list_all_tools_dict()

@st.cache_data(ttl=30)
def fetch_sections():
    return list_all_sections_dict()

tools_dict = fetch_tools()
sections_dict = fetch_sections()
tools_df = tools_df_from_db(tools_dict, sections_dict)

# Sidebar
with st.sidebar:
    st.header("Share:")
    st.image(render_qr(STREAMLIT_APP_URL), width=160)
    poll_interval = st.number_input("Auto-refresh interval (sec)", min_value=1, max_value=600, value=POLL_INTERVAL_SECONDS)

# Auto-refresh
from streamlit_autorefresh import st_autorefresh
st_autorefresh(interval=poll_interval*1000, key="refresh_counter")

# ---------------------------
# Tabs (anchor to last used tab)
# ---------------------------
tabs = st.tabs([
    "Dashboard", "Tag Explorer", "Overall Leaderboard", "Suggest Tool", "Manage Tools", "Comments"
])

# Anchor to last-used tab (prevents jump)
with tabs[st.session_state.active_tab]:
    pass

# 0: Dashboard
with tabs[0]:
    st.session_state.active_tab = 0
    st.header("Live Dashboard — Sections & Leaderboards")
    st.caption(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    left_col, right_col = st.columns([3, 1])
    with left_col:
        for sec_id, sec in sections_dict.items():
            st.subheader(sec.get("name", sec_id))
            tool_ids = sec.get("tool_ids", {}) or {}
            sub_df = tools_df[tools_df["tool_id"].isin(tool_ids.keys())].copy()
            if sub_df.empty:
                st.write("No tools yet in this section.")
                continue
            top10_df = sub_df.sort_values("score", ascending=False).head(10)
            fig = px.bar(top10_df, x="score", y="name", orientation="h", text="score", height=300)
            fig.update_layout(showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            with st.expander("All tools in this section", expanded=False):
                for _, row in sub_df.sort_values("score", ascending=False).iterrows():
                    render_tool_row(row, section_id=sec_id, context="dashboard")
    with right_col:
        st.subheader("Top 5 Overall")
        top5 = tools_df.head(5)
        for _, r in top5.iterrows():
            st.write(f"**{r['name']}** — {r['score']} pts ({r['upvotes']}↑ / {r['downvotes']}↓)")

# 1: Tag Explorer
with tabs[1]:
    st.session_state.active_tab = 1
    st.header("Explore tools by tags")
    all_tags = sorted({tg for t in tools_dict.values() for tg in t.get("tags", [])})
    selected_tags = st.multiselect("Filter by tags", all_tags)
    filtered = {tid: t for tid, t in tools_dict.items() if set(selected_tags).issubset(set(t.get("tags", [])))} if selected_tags else tools_dict
    df_filtered = tools_df_from_db(filtered, sections_dict)
    st.write(f"Found {len(df_filtered)} tools")
    for _, row in df_filtered.iterrows():
        render_tool_row(row, context="tag")

# 2: Overall Leaderboard
with tabs[2]:
    st.session_state.active_tab = 2
    st.header("Overall Leaderboard")
    if not tools_df.empty:
        fig = px.bar(tools_df.head(25), x="score", y="name", orientation="h", text="score", height=600)
        fig.update_layout(showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(tools_df[["name", "tags", "upvotes", "downvotes", "score"]].reset_index(drop=True), height=400)
        for _, row in tools_df.head(25).iterrows():
            render_tool_row(row, context="overall")

# 3: Suggest Tool
with tabs[3]:
    st.session_state.active_tab = 3
    st.header("Suggest a new tool")
    existing_tool_names = [t["name"] for t in tools_dict.values()]
    with st.form("suggest_form"):
        tool_name = st.text_input("Tool name (type new, must be unique)")
        all_section_options = {sid: info.get("name", sid) for sid, info in sections_dict.items()}
        selected_sections = st.multiselect("Sections (choose one or more)", options=list(all_section_options.keys()), format_func=lambda x: all_section_options[x])
        tags = [all_section_options[sid] for sid in selected_sections]
        st.info(f"Tags will be automatically set from sections: {', '.join(tags)}")
        submit = st.form_submit_button("Add tool")
        if submit:
            name_stripped = tool_name.strip()
            if not name_stripped:
                st.warning("Enter a tool name")
            elif name_stripped.lower() in map(str.lower, existing_tool_names):
                st.error("This tool already exists!")
            elif not selected_sections:
                st.warning("Select at least one section")
            else:
                create_tool_entry(name_stripped, tags, selected_sections)
                st.success(f"Added tool: {name_stripped}")
                trigger_refresh()

# 4: Manage Tools
with tabs[4]:
    st.session_state.active_tab = 4
    st.header("Manage Existing Tools")
    tool_options = {tid: t["name"] for tid, t in tools_dict.items()}
    selected_tool_id = st.selectbox("Select a tool to edit", options=list(tool_options.keys()), format_func=lambda x: tool_options[x])
    if selected_tool_id:
        tool = tools_dict[selected_tool_id]
        new_name = st.text_input("Tool Name", value=tool["name"])
        all_section_options = {sid: info.get("name", sid) for sid, info in sections_dict.items()}
        current_sections = tool.get("sections", [])
        new_sections = st.multiselect("Sections (choose one or more)", options=list(all_section_options.keys()), default=current_sections, format_func=lambda x: all_section_options[x])
        new_tags = ", ".join([all_section_options[sid] for sid in new_sections])
        st.text_input("Tags (auto-updated from sections)", value=new_tags, key="manage_tool_tags", disabled=True)
        if st.button("Save changes"):
            tool_ref = get_db_ref(f"/tools/{selected_tool_id}")
            tool_ref.update({
                "name": new_name.strip(),
                "tags": [t.strip() for t in new_tags.split(",") if t.strip()],
                "sections": new_sections
            })
            for sec_id in sections_dict.keys():
                sec_tool_ids_ref = get_db_ref(f"/sections/{sec_id}/tool_ids")
                sec_tool_ids = sec_tool_ids_ref.get() or {}
                if sec_id not in new_sections and selected_tool_id in sec_tool_ids:
                    sec_tool_ids_ref.child(selected_tool_id).delete()
                elif sec_id in new_sections and selected_tool_id not in sec_tool_ids:
                    sec_tool_ids_ref.update({selected_tool_id: True})
            st.success("Updated tool")
            trigger_refresh()

# 5: Comments
with tabs[5]:
    st.session_state.active_tab = 5
    st.header("Audience Comments")
    tool_options = {tid: t["name"] for tid, t in tools_dict.items()}
    selected_tool_id = st.selectbox("Select a tool", options=list(tool_options.keys()), format_func=lambda x: tool_options[x])
    if selected_tool_id:
        tool = tools_dict[selected_tool_id]
        comments = fetch_comments(selected_tool_id)
        st.markdown("**Comments:**")
        scroll_height = 200
        if comments:
            comment_html = ""
            for cid, c in sorted(comments.items(), key=lambda x: x[1]["timestamp"], reverse=True):
                emoji = {"pro": "👍", "con": "👎", "neutral": "💬"}.get(c["type"], "💬")
                comment_html += f"<p style='margin:2px 0'>{emoji} {c['text']} — <small>{c['timestamp'][:16]}</small></p>"
            st.markdown(f"<div style='height:{scroll_height}px; overflow-y:auto; border:1px solid #ccc; padding:5px;'>{comment_html}</div>", unsafe_allow_html=True)
        else:
            st.info("No comments yet.")
        with st.expander("Leave a comment"):
            new_comment = st.text_area(f"Comment for {tool['name']}", key=f"comment_input_{selected_tool_id}", height=50)
            comment_type = st.radio("Type", ["pro", "con", "neutral"], index=2, horizontal=True, key=f"comment_type_{selected_tool_id}")
            if st.button("Submit", key=f"submit_comment_{selected_tool_id}"):
                if new_comment.strip():
                    add_comment(selected_tool_id, new_comment.strip(), comment_type)
                    st.success("Comment added!")
                    trigger_refresh()
                else:
                    st.warning("Enter a comment before submitting.")
