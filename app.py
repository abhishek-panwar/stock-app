import streamlit as st
from dotenv import load_dotenv
load_dotenv()

st.set_page_config(
    page_title="Stock Analysis",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global styles ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #0f172a !important;
    border-right: 1px solid #1e293b;
}

/* Force all text in sidebar to be light */
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] small {
    color: #cbd5e1 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 { color: #f8fafc !important; }

/* Sidebar nav buttons — secondary */
[data-testid="stSidebar"] [data-testid="stButton"] > button {
    width: 100%;
    text-align: left;
    background: transparent !important;
    border: 1px solid #1e293b !important;
    color: #e2e8f0 !important;
    font-size: 14px;
    font-weight: 500;
    padding: 9px 14px;
    border-radius: 8px;
    margin-bottom: 3px;
    transition: all 0.15s ease;
}
[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {
    background: #1e293b !important;
    border-color: #334155 !important;
    color: #f1f5f9 !important;
    transform: none;
    box-shadow: none;
}
/* Active nav button (primary type) */
[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {
    background: #1d4ed8 !important;
    border-color: #2563eb !important;
    color: #ffffff !important;
    font-weight: 600;
}
[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"]:hover {
    background: #1e40af !important;
}

/* ── Expanders ── */
[data-testid="stExpander"] {
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
    margin-bottom: 6px;
    background: #ffffff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
[data-testid="stExpander"]:hover {
    border-color: #cbd5e1 !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
[data-testid="stExpander"] summary {
    font-size: 13.5px !important;
    font-weight: 500 !important;
    color: #1e293b !important;
    padding: 10px 14px;
}

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 12px 16px;
}
[data-testid="stMetricValue"] { font-size: 1.35rem !important; font-weight: 700 !important; }
[data-testid="stMetricLabel"] { font-size: 12px !important; color: #64748b !important; }

/* ── Typography ── */
h1 { font-size: 1.55rem !important; font-weight: 700 !important; color: #0f172a !important; margin-bottom: 2px !important; }
h2 { font-size: 1.25rem !important; font-weight: 700 !important; color: #1e293b !important; }
h3 { font-size: 1.05rem !important; font-weight: 600 !important; color: #1e293b !important; }

/* ── Main content buttons ── */
[data-testid="stMain"] [data-testid="stButton"] > button {
    border-radius: 8px;
    font-weight: 500;
    font-size: 13px;
    transition: all 0.15s;
}
[data-testid="stMain"] [data-testid="stButton"] > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 3px 8px rgba(0,0,0,0.12);
}

/* ── Alerts ── */
[data-testid="stInfo"]    { border-radius: 8px; border-left: 4px solid #3b82f6; }
[data-testid="stWarning"] { border-radius: 8px; border-left: 4px solid #f59e0b; }
[data-testid="stSuccess"] { border-radius: 8px; border-left: 4px solid #22c55e; }

/* ── Selectbox / slider labels ── */
[data-testid="stSelectbox"] label,
[data-testid="stSlider"] label { font-size: 12px !important; color: #64748b !important; }

/* ── Caption text ── */
[data-testid="stCaptionContainer"] p { color: #64748b !important; font-size: 12px !important; }

/* ── Force left alignment everywhere in main content ── */
[data-testid="stMain"] p,
[data-testid="stMain"] li,
[data-testid="stMain"] span,
[data-testid="stMain"] div,
[data-testid="stMain"] label,
[data-testid="stMain"] .stMarkdown,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] p {
    text-align: left !important;
}
</style>
""", unsafe_allow_html=True)

# ── Sidebar nav ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        "<div style='padding:8px 4px 4px;font-size:18px;font-weight:700;color:#f8fafc'>📊 Stock Analysis</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<hr style='border:none;border-top:1px solid #1e293b;margin:8px 0 10px'>", unsafe_allow_html=True)

    pages = [
        ("Open Predictions",       "🏠"),
        ("Closed Predictions",     "📜"),
        ("Optimizations",         "🧠"),
        ("Deep Dive",             "🔬"),
        ("Analysts",              "👤"),
        ("System Evolution",      "⚙️"),
        ("Health Dashboard",      "🔧"),
        ("Deleted Predictions",   "🗑️"),
    ]

    if "page" not in st.session_state:
        st.session_state.page = pages[0][0]

    for name, emoji in pages:
        active = st.session_state.page == name
        if st.button(
            f"{emoji}  {name}",
            key=f"nav_{name}",
            use_container_width=True,
            type="primary" if active else "secondary",
        ):
            st.session_state.page = name
            st.rerun()

    st.markdown("<hr style='border:none;border-top:1px solid #1e293b;margin:10px 0 8px'>", unsafe_allow_html=True)
    st.markdown("<div style='font-size:11px;color:#64748b;padding:0 4px'>⏰ All times Pacific (Seattle)</div>", unsafe_allow_html=True)

page = st.session_state.page

# ── Route ─────────────────────────────────────────────────────────────────────
if page == "Open Predictions":
    from views import main_dashboard;  main_dashboard.render()
elif page == "Closed Predictions":
    from views import history;         history.render()
elif page == "Deleted Predictions":
    from views import deleted_predictions; deleted_predictions.render()
elif page == "Deep Dive":
    from views import deep_dive;       deep_dive.render()
elif page == "Analysts":
    from views import analysts;        analysts.render()
elif page == "Optimizations":
    from views import optimizations; optimizations.render()
elif page == "System Evolution":
    from views import system_evolution; system_evolution.render()
elif page == "Health Dashboard":
    from views import health_dashboard; health_dashboard.render()
