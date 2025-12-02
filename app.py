# app.py
import os
import uuid
from datetime import datetime
from io import BytesIO

import streamlit as st
import pandas as pd
import plotly.express as px
import qrcode

# Firebase admin
import firebase_admin
from firebase_admin import credentials, initialize_app, db

# Auto load .env
from dotenv import load_dotenv
load_dotenv()

import json
import tempfile

# ---------------------------
# Anonymous voter ID per browser
# ---------------------------
import streamlit.components.v1 as components

def get_browser_voter_id():
    voter_id = st.session_state.get("voter_id")
    components.html("""
    <script>
        const voter = localStorage.getItem("voter_id") || crypto.randomUUID();
        localStorage.setItem("voter_id", voter);
        window.parent.postMessage({type: "SET_VOTER_ID", voter}, "*");
    </script>
    """, height=0)
    return voter_id

if "voter_id" not in st.session_state:
    st.session_state["voter_id"] = str(uuid.uuid4())  # fallback

VOTER_ID = get_browser_voter_id()

# ---------------------------
# Config / Constants
# ---------------------------

import json
import streamlit as st

# Use secrets instead of local env vars
try:
    firebase_json_str = st.secrets["FIREBASE"]["CREDENTIALS_JSON"]
    firebase_creds = json.loads(firebase_json_str)
    firebase_db_url = st.secrets["FIREBASE"]["DATABASE_URL"]
except KeyError:
    st.error("Firebase secrets not found! Make sure you configured them in Streamlit Cloud.")
    st.stop()

STREAMLIT_APP_URL = "https://spatial-mvp.streamlit.app/"
POLL_INTERVAL_SECONDS = int(st.secrets.get("POLL_INTERVAL_SECONDS", 600))

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
# Page refresh helper
# ---------------------------
def trigger_refresh():
    """
    Triggers a Streamlit page rerun by updating a dummy query param.
    """
    params = dict(st.query_params)
    params["refresh"] = [str(uuid.uuid4())]  # dummy param to trigger rerun
    st.query_params = params

# ---------------------------
# Firebase helpers
# ---------------------------

def init_firebase():
    # Avoid re-initializing if already done
    if firebase_admin._apps:
        return

    try:
        # Load secrets
        firebase_json = st.secrets["FIREBASE"]["CREDENTIALS_JSON"]
        firebase_db_url = st.secrets["FIREBASE"]["DATABASE_URL"]
    except KeyError:
        st.error("Firebase secrets not found! Make sure they are configured in Streamlit Cloud.")
        st.stop()

    # If the secret is a dictionary, convert to JSON string
    if isinstance(firebase_json, dict):
        firebase_json_str = json.dumps(firebase_json)
    else:
        firebase_json_str = firebase_json

    # Write to a temporary file and initialize
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as f:
        f.write(firebase_json_str)
        f.flush()  # make sure all data is written
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

# Add a comment
def add_comment(tool_id, text, comment_type):
    comment_id = str(uuid.uuid4())
    comment_data = {
        "text": text,
        "type": comment_type,
        "timestamp": datetime.utcnow().isoformat()
    }
    get_db_ref(f"/comments/{tool_id}/{comment_id}").set(comment_data)

# Fetch comments
def fetch_comments(tool_id):
    return get_db_ref(f"/comments/{tool_id}").get() or {}

# ---------------------------
# Seed Firebase from CSV
# ---------------------------

INITIAL_VOTES = {
    "ADEPT": 6,
    "banksy": 127,
    "BASS": 30,
    "BayesSpace": 160,
    "Baysor": 1,
    "Bento": 84,
    "BoReMi": 4,
    "CellAgentChat": 33,
    "Cellcharter": 11,
    "Cellpose": 1976,
    "CellProfiler": 1066,
    "CellSymphony": 0,
    "COMMOT": 125,
    "DeepST": 86,
    "DR.SC": 6,
    "Giotto": 17,
    "GPSA": 32,
    "GraphST": 138,
    "GROVER": 1,
    "HEIST": 0,
    "InSituPy": 28,
    "iSCALE": 39,
    "KBC": 1,
    "LeGO-3D": 0,
    "LIANA": 224,
    "LLOT": 0,
    "MagNet": 11,
    "MISTy": 71,
    "MOFA": 361,
    "MOSAIK": 3,
    "MuSpAn": 17,
    "Niche-DE": 23,
    "Nicheformer": 123,
    "PASTE": 96,
    "PASTE2": 41,
    "PersiST": 16,
    "PHD-MS": 1,
    "PRECAST": 12,
    "scGPT": 114,
    "scVI": 1503,
    "Segger": 10,
    "SemST": 4,
    "SPAC": 9,
    "SPACEL": 60,
    "SpaGCN": 0,
    "SPIRAL": 16,
    "SpOOx": 10,
    "ST-Align": 91,
    "STAGATE": 138,
    "STAligner": 47,
    "stardist": 1130,
    "stLearn": 0,
    "STtools": 12,
    "Tangram": 337,
    "TensionMap": 8,
    "Thor": 24,
    "TopoVelo": 13,
    "VoxelEmbed": 0,
    "Cell2Spatial": 2
}


def seed_defaults_from_excel(excel_path="tools.csv"):
    """
    Reads tools CSV and populates Firebase if empty.
    Tags for each tool are automatically set as section names.
    """
    root = get_db_ref("/")
    # Seed sections if missing
    if not root.child("sections").get():
        root.child("sections").set(DEFAULT_SECTIONS)

    # Skip seeding tools if already present
    if root.child("tools").get():
        return

    df = pd.read_csv(excel_path)
    tools_obj = {}
    for _, row in df.iterrows():
        tool_name = row["Tool name"].strip()
        section_ids = [SECTION_MAPPING[col] for col in SECTION_MAPPING if row.get(col, 0) == 1]
        if not section_ids:
            continue
        tags = [DEFAULT_SECTIONS[sid]["name"] for sid in section_ids]

        tid = str(uuid.uuid4())
        
        # Set initial upvotes from INITIAL_VOTES, default to 0 if not listed
        upvotes = INITIAL_VOTES.get(tool_name, 0)

        tools_obj[tid] = {
            "name": tool_name,
            "tags": tags,
            "sections": section_ids,
            "upvotes": upvotes,
            "downvotes": 0,
            "created_at": datetime.utcnow().isoformat()
        }
        # Assign tool to sections
        for sec_id in section_ids:
            root.child("sections").child(sec_id).child("tool_ids").update({tid: True})
    root.child("tools").set(tools_obj)
    st.success(f"Seeded {len(tools_obj)} tools from CSV with initial votes")


# ---------------------------
# UI Helpers
# ---------------------------
def compute_score(tool):
    return int(tool.get("upvotes",0)) - int(tool.get("downvotes",0))

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
            "upvotes": int(t.get("upvotes",0)),
            "downvotes": int(t.get("downvotes",0)),
            "score": compute_score(t),
            "created_at": t.get("created_at","")
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["score","upvotes"], ascending=[False,False])
    return df

def render_qr(url):
    qr = qrcode.QRCode(box_size=5, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def render_tool_row(tool_row, section_id=None, context=""):
    unique_key = f"{context}_{tool_row['tool_id']}_{section_id}"
    up_key, down_key, score_key = f"up_{unique_key}", f"down_{unique_key}", f"score_{unique_key}"

    if score_key not in st.session_state:
        st.session_state[score_key] = tool_row['score']

    # Voting row
    c_name, c_tags, c_score, c_up, c_down = st.columns([3,2,1,1,1])
    c_name.write(f"**{tool_row['name']}**")
    c_tags.write(tool_row['tags'])
    c_score.write(st.session_state[score_key])

    vote_ref = get_db_ref(f"/votes/{tool_row['tool_id']}/{VOTER_ID}")
    if vote_ref.get():  # already voted
        c_up.button("👍", disabled=True, key=up_key+"_disabled")
        c_down.button("👎", disabled=True, key=down_key+"_disabled")
    else:
        if c_up.button("👍", key=up_key):
            increment_counter_atomic(f"/tools/{tool_row['tool_id']}/upvotes", 1)
            vote_ref.set({"type":"up", "timestamp": datetime.utcnow().isoformat()})
            st.session_state[score_key] += 1
        if c_down.button("👎", key=down_key):
            increment_counter_atomic(f"/tools/{tool_row['tool_id']}/downvotes", 1)
            vote_ref.set({"type":"down", "timestamp": datetime.utcnow().isoformat()})
            st.session_state[score_key] -= 1

    # --- Comments section ---
    comments = fetch_comments(tool_row['tool_id'])
    st.markdown("**Comments:**")

    # Scrollable container
    scroll_height = 200  # pixels
    if comments:
        comment_html = ""
        # sort newest first
        for cid, c in sorted(comments.items(), key=lambda x: x[1]["timestamp"], reverse=True):
            emoji = {"pro":"👍", "con":"👎", "neutral":"💬"}.get(c["type"], "💬")
            comment_html += f"<p style='margin:2px 0'>{emoji} {c['text']} — <small>{c['timestamp'][:16]}</small></p>"

        st.markdown(
            f"<div style='height:{scroll_height}px; overflow-y:auto; border:1px solid #ccc; padding:5px;'>{comment_html}</div>",
            unsafe_allow_html=True
        )
    else:
        st.info("No comments yet.")

    # Input for new comment
    with st.expander("Leave a comment"):
        new_comment = st.text_area(f"Comment for {tool_row['name']}", key=f"comment_input_{tool_row['tool_id']}", height=50)
        comment_type = st.radio(
            "Type",
            ["pro", "con", "neutral"],
            index=2,
            horizontal=True,
            key=f"comment_type_{tool_row['tool_id']}"
        )
        if st.button("Submit", key=f"submit_comment_{tool_row['tool_id']}"):
            if new_comment.strip():
                add_comment(tool_row['tool_id'], new_comment.strip(), comment_type)
                st.success("Comment added!")
                st.experimental_rerun()
            else:
                st.warning("Enter a comment before submitting.")

# ---------------------------
# Streamlit Page Setup
# ---------------------------
st.set_page_config(page_title="Spatial Multiomics Voting Platform", layout="wide")

# --- Consent ---
# Ensure consent state exists
if "consent_given" not in st.session_state:
    st.session_state.consent_given = False

# Container for consent UI
consent_container = st.container()

if not st.session_state.consent_given:
    with consent_container:
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
        
        if st.button("✅ I Agree (Double-Click)"):
            # Mark consent in session_state
            st.session_state.consent_given = True

            # Trigger a page rerun using query_params (replaces experimental_rerun)
            params = dict(st.query_params)
            params["consent"] = ["yes"]  # or any dummy param to trigger rerun
            st.query_params = params  # assignment triggers rerun automatically

    st.stop()  # block everything else until consent is given


st.title("Spatial Multiomics Voting Platform — Live @ Conference")
st.markdown("#### 🧠 Presented at [BSI Congress 2025](https://www.bsicongress.com)")

# --- Firebase init & seeding ---
try:
    init_firebase()
except Exception as e:
    st.error(f"Firebase init failed: {e}")
    st.stop()

seed_defaults_from_excel("tools.csv")

# ---------------------------
# Firebase fetch helpers
# ---------------------------
def list_all_tools_dict():
    init_firebase()
    return get_db_ref("/tools").get() or {}

def list_all_sections_dict():
    init_firebase()
    return get_db_ref("/sections").get() or {}


# --- Cached fetch ---
@st.cache_data(ttl=60)
def fetch_tools():
    return list_all_tools_dict()

@st.cache_data(ttl=60)
def fetch_sections():
    return list_all_sections_dict()

tools_dict = fetch_tools()
sections_dict = fetch_sections()
tools_df = tools_df_from_db(tools_dict, sections_dict)

# --- Sidebar ---
with st.sidebar:
    st.header("Share:")
    st.image(render_qr(STREAMLIT_APP_URL), width=160)
    #st.write(f"App URL: {STREAMLIT_APP_URL}")
    st.markdown("---")

    # --- Leave a note to Dev ---
    st.subheader("💬 Leave a note to the Dev")

    st.markdown('<p style="margin-bottom:0; font-weight:bold; font-size:16px;">Leave a reaction:</p>', unsafe_allow_html=True)
    reaction_ref = get_db_ref("/dev_notes")

    reaction_buttons = ["🔥", "👍", "😵‍💫", "😐", "👎"]
    reaction_labels = ["Love it!", "Like it", "Confused", "Could be better", "Dislike"]

    cols = st.columns(len(reaction_buttons))
    for idx, emoji in enumerate(reaction_buttons):
        # split each slot into button + label horizontally
        btn_col, lbl_col = st.columns([1, 2])
        if btn_col.button(emoji, key=f"reaction_{idx}"):
            reaction_ref.push({
                "vibe": reaction_labels[idx],
                "note": None,
                "timestamp": datetime.now().isoformat()
            })
            st.toast(f"{reaction_labels[idx]} sent! 🚀", icon="💬")
        
        # add small top margin to align with button
        lbl_col.markdown(
            f'<div style="margin-top:0.35em">{reaction_labels[idx]}</div>',
            unsafe_allow_html=True
        )

    # --- Idea / suggestion ---
    st.markdown('<p style="margin-bottom:0;font-weight:bold;">I\'ve got a suggestion!</p>', unsafe_allow_html=True)

    idea_note = st.text_area(
        "", 
        placeholder="This is actually kinda fire... one thing I’d change is...", 
        height=100,
        key="idea_note_input"  # assign a session_state key
    )

    if st.button("Send note! 🚀"):
        if idea_note.strip():
            reaction_ref.push({
                "vibe": None,
                "note": idea_note.strip(),
                "timestamp": datetime.now().isoformat()
            })
            st.toast("Note sent! 🚀", icon="💬")
            # reset the textbox
            st.session_state["idea_note_input"] = ""
        else:
            st.warning("Type something before sending!")

    st.markdown("---")

    poll_interval = st.number_input("Auto-refresh interval (sec)", min_value=1, max_value=600, value=POLL_INTERVAL_SECONDS)
    st.markdown("---")
    #st.write("Admin:")
    #if st.button("Reset DB to defaults"):
    #    root = get_db_ref("/")
    #    root.child("sections").delete()
    #    root.child("tools").delete()
    #    seed_defaults_from_excel("tools.csv")
    #    st.success("Reset DB and reseeded defaults.")

# --- Auto-refresh ---
from streamlit_autorefresh import st_autorefresh
st_autorefresh(interval=poll_interval*1000, key="refresh_counter")

# Fetch latest data
tools_dict = list_all_tools_dict()
sections_dict = list_all_sections_dict()
tools_df = tools_df_from_db(tools_dict, sections_dict)

# ---------------------------
# Voting Helper
# ---------------------------
def render_tool_row(tool_row, section_id=None, context=""):
    """
    Render a single tool row with voting buttons that auto-update score
    without full page refresh.
    """
    unique_key = f"{context}_{tool_row['tool_id']}_{section_id}"

    # Ensure session_state keys exist
    up_key = f"up_{unique_key}"
    down_key = f"down_{unique_key}"
    score_key = f"score_{unique_key}"

    if score_key not in st.session_state:
        st.session_state[score_key] = compute_score(tool_row)

    c_name, c_tags, c_score, c_up, c_down = st.columns([3,2,1,1,1])
    c_name.write(f"**{tool_row['name']}**")
    c_tags.write(tool_row['tags'])
    c_score.write(st.session_state[score_key])

    if c_up.button("👍", key=up_key):
        increment_counter_atomic(f"/tools/{tool_row['tool_id']}/upvotes", 1)
        st.session_state[score_key] += 1

    if c_down.button("👎", key=down_key):
        increment_counter_atomic(f"/tools/{tool_row['tool_id']}/downvotes", 1)
        st.session_state[score_key] -= 1

# ---------------------------
# Tabs
# ---------------------------
# --- Persisted Tabs Fix ---
TAB_NAMES = [
    "Dashboard",
    "Tag Explorer",
    "Overall Leaderboard",
    "Suggest Tool",
    "Manage Tools",
    "Write a tool review!"
]

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Dashboard"

selected_tab = st.radio(
    "Navigation",
    TAB_NAMES,
    index=TAB_NAMES.index(st.session_state.active_tab),
    horizontal=True,
    key="tab_selector"
)

st.session_state.active_tab = selected_tab

# Dashboard Tab
if selected_tab == "Dashboard":
    st.header("Live Dashboard — Sections & Leaderboards")
    st.caption(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    left_col, right_col = st.columns([3,1])
    
    with left_col:
        for sec_id, sec in sections_dict.items():
            st.subheader(sec.get("name", sec_id))
            tool_ids = sec.get("tool_ids", {}) or {}
            if not tool_ids:
                st.write("No tools yet in this section.")
                continue

            sub_df = tools_df[tools_df["tool_id"].isin(tool_ids.keys())].copy()
            if sub_df.empty:
                st.write("No tools yet in this section.")
                continue

            # Top 10 bar chart only
            top10_df = sub_df.sort_values("score", ascending=False).head(10)
            fig = px.bar(
                top10_df,
                x="score",
                y="name",
                orientation="h",
                text="score",
                labels={"score":"Score","name":"Tool"},
                width=800,
                height=300
            )
            fig.update_layout(showlegend=False, margin=dict(l=10,r=10,t=10,b=10))
            st.plotly_chart(fig, use_container_width=True)

            # Scrollable list for all tools
            with st.expander("All tools in this section", expanded=False):
                for _, row in sub_df.sort_values("score", ascending=False).iterrows():
                    render_tool_row(row, section_id=sec_id, context="dashboard")
    
    with right_col:
        st.subheader("Top 5 Overall")
        top5 = tools_df.head(5)
        for _, r in top5.iterrows():
            st.write(f"**{r['name']}** — {r['score']} pts ({r['upvotes']}↑ / {r['downvotes']}↓)")
        
        st.markdown("### Recently added")
        recent = tools_df.sort_values("created_at", ascending=False).head(5)
        for _, r in recent.iterrows():
            st.write(f"{r['name']} — {r['tags']}")


# Tag Explorer
if selected_tab == "Tag Explorer":
    st.header("Explore tools by tags")
    all_tags = sorted({tg for t in tools_dict.values() for tg in t.get("tags", [])})
    selected_tags = st.multiselect("Filter by tags", all_tags)
    if selected_tags:
        filtered = {tid: t for tid, t in tools_dict.items() if set(selected_tags).issubset(set(t.get("tags", [])))}
    else:
        filtered = tools_dict
    df_filtered = tools_df_from_db(filtered)
    st.write(f"Found {len(df_filtered)} tools")
    for _, row in df_filtered.iterrows():
        render_tool_row(row, context="tag")

# Overall Leaderboard
if selected_tab == "Overall Leaderboard":
    st.header("Overall Leaderboard")
    if not tools_df.empty:
        fig = px.bar(tools_df.head(25), x="score", y="name", orientation="h", text="score")
        fig.update_layout(showlegend=False, margin=dict(l=10,r=10,t=10,b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(tools_df[["name","tags","upvotes","downvotes","score"]].reset_index(drop=True), height=400)
        st.write("Quick vote for top tools:")
        for _, row in tools_df.head(25).iterrows():
            render_tool_row(row, context="overall")

# Suggest Tool tab
if selected_tab == "Suggest Tool":
    st.header("Suggest a new tool")
    st.write("Propose a new tool and associate it with sections and tags.")

    # Existing tool names (case-insensitive)
    existing_tool_names = [t['name'] for t in tools_dict.values()]

    with st.form("suggest_form"):
        # Text input for tool name
        tool_name = st.text_input(
            "Tool name (type new, must be unique)",
            placeholder="Start typing a new tool..."
        )

        # Sections multi-select
        all_section_options = {sid: info.get("name", sid) for sid, info in sections_dict.items()}
        selected_sections = st.multiselect(
            "Sections (choose one or more)",
            options=list(all_section_options.keys()),
            format_func=lambda x: all_section_options[x]
        )

        # Tags input
        # Auto-derive tags from selected sections
        tags = [all_section_options[sid] for sid in selected_sections]
        st.info(f"Tags will be automatically set from sections: {', '.join(tags)}")

        submit = st.form_submit_button("Add tool")

        if submit:
            name_stripped = tool_name.strip()
            
            # Validate tool name
            if not name_stripped:
                st.warning("Enter a tool name")
            elif name_stripped.lower() in map(str.lower, existing_tool_names):
                st.error("This tool already exists! Please choose a new name or edit it in Manage Tools tab.")
            elif not selected_sections:
                st.warning("Select at least one section")
            else:
                # Parse tags
                tags = [all_section_options[sid] for sid in selected_sections]
                # Create tool
                create_tool_entry(name_stripped, tags, selected_sections)
                st.success(f"Added tool: {name_stripped}")
                # Optionally clear form fields
                trigger_refresh()
                
# Manage Tools tab
if selected_tab == "Manage Tools":  # new tab after Suggest Tool
    st.header("Manage Existing Tools")
    st.write("Edit tool name, tags, or associated sections.")

    # Build list of tool options
    tool_options = {tid: t["name"] for tid, t in tools_dict.items()}
    selected_tool_id = st.selectbox("Select a tool to edit", options=list(tool_options.keys()), format_func=lambda x: tool_options[x])

    if selected_tool_id:
        tool = tools_dict[selected_tool_id]

        # Editable fields
        new_name = st.text_input("Tool Name", value=tool["name"])
        #new_tags = st.text_input("Tags (comma-separated)", value=", ".join(tool.get("tags", [])))

        # Section multi-select
        all_section_options = {sid: info.get("name", sid) for sid, info in sections_dict.items()}
        current_sections = tool.get("sections", [])

        new_sections = st.multiselect(
            "Sections (choose one or more)",
            options=list(all_section_options.keys()),
            default=current_sections,
            format_func=lambda x: all_section_options[x]
        )

        # Dynamically update tags based on selected sections
        new_tags = ", ".join([all_section_options[sid] for sid in new_sections])
        st.text_input("Tags (auto-updated from sections)", value=new_tags, key="manage_tool_tags", disabled=True)

        if st.button("Save changes"):
            # 1. Update name and tags
            tool_ref = get_db_ref(f"/tools/{selected_tool_id}")
            tool_ref.update({
                "name": new_name.strip(),
                "tags": [t.strip() for t in new_tags.split(",") if t.strip()],
                "sections": new_sections
            })

            # 2. Update sections: remove tool from sections it no longer belongs to
            for sec_id in sections_dict.keys():
                sec_tool_ids_ref = get_db_ref(f"/sections/{sec_id}/tool_ids")
                sec_tool_ids = sec_tool_ids_ref.get() or {}
                if sec_id not in new_sections and selected_tool_id in sec_tool_ids:
                    sec_tool_ids_ref.child(selected_tool_id).delete()
                elif sec_id in new_sections and selected_tool_id not in sec_tool_ids:
                    sec_tool_ids_ref.update({selected_tool_id: True})

            st.success(f"Updated tool: {new_name}")
            trigger_refresh()

# Comments Tab
if selected_tab == "Write a tool review!":
    st.header("Audience Comments")
    
    # Select a tool
    tool_options = {tid: t["name"] for tid, t in tools_dict.items()}
    selected_tool_id = st.selectbox("Select a tool", options=list(tool_options.keys()), format_func=lambda x: tool_options[x])

    if selected_tool_id:
        tool = tools_dict[selected_tool_id]
        
        # Show existing comments
        comments = fetch_comments(selected_tool_id)
        st.markdown("**Comments:**")
        scroll_height = 200
        if comments:
            comment_html = ""
            for cid, c in sorted(comments.items(), key=lambda x: x[1]["timestamp"], reverse=True):
                emoji = {"pro":"👍", "con":"👎", "neutral":"💬"}.get(c["type"], "💬")
                comment_html += f"<p style='margin:2px 0'>{emoji} {c['text']} — <small>{c['timestamp'][:16]}</small></p>"

            st.markdown(
                f"<div style='height:{scroll_height}px; overflow-y:auto; border:1px solid #ccc; padding:5px;'>{comment_html}</div>",
                unsafe_allow_html=True
            )
        else:
            st.info("No comments yet.")

        # Add new comment
        with st.expander("Leave a comment"):
            new_comment = st.text_area(f"Comment for {tool['name']}", key=f"comment_input_{selected_tool_id}", height=50)
            comment_type = st.radio(
                "Type",
                ["pro", "con", "neutral"],
                index=2,
                horizontal=True,
                key=f"comment_type_{selected_tool_id}"
            )
            if st.button("Submit", key=f"submit_comment_{selected_tool_id}"):
                if new_comment.strip():
                    add_comment(selected_tool_id, new_comment.strip(), comment_type)
                    st.success("Comment added!")
                    trigger_refresh()
                else:
                    st.warning("Enter a comment before submitting.")
