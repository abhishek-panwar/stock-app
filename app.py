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
/* Sidebar */
[data-testid="stSidebar"] {
    background: #0f172a;
    border-right: 1px solid #1e293b;
}
[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2 { color: #f8fafc !important; }

/* Nav buttons */
.nav-btn {
    display: block;
    width: 100%;
    padding: 10px 14px;
    margin: 4px 0;
    border-radius: 8px;
    border: 1px solid transparent;
    background: transparent;
    color: #94a3b8;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    text-align: left;
    transition: all 0.15s ease;
    text-decoration: none;
}
.nav-btn:hover { background: #1e293b; color: #f1f5f9 !important; border-color: #334155; }
.nav-btn.active { background: #1d4ed8; color: #fff !important; border-color: #2563eb; font-weight: 600; }

/* Expanders */
[data-testid="stExpander"] {
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    margin-bottom: 6px;
    background: #fff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
[data-testid="stExpander"]:hover { border-color: #cbd5e1; box-shadow: 0 2px 6px rgba(0,0,0,0.08); }

/* Metrics */
[data-testid="stMetric"] {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 12px 16px;
}
[data-testid="stMetricValue"] { font-size: 1.4rem !important; font-weight: 700; }

/* Page titles */
h1 { font-size: 1.6rem !important; font-weight: 700 !important; color: #0f172a !important; }
h3 { font-size: 1.1rem !important; font-weight: 600 !important; color: #1e293b !important; }

/* Buttons */
[data-testid="stButton"] > button {
    border-radius: 8px;
    font-weight: 500;
    transition: all 0.15s;
}
[data-testid="stButton"] > button:hover { transform: translateY(-1px); box-shadow: 0 3px 8px rgba(0,0,0,0.12); }

/* Info / warning boxes */
[data-testid="stInfo"] { border-radius: 10px; border-left: 4px solid #3b82f6; }
[data-testid="stWarning"] { border-radius: 10px; border-left: 4px solid #f59e0b; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar nav ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Stock Analysis")
    st.markdown("<hr style='border-color:#1e293b;margin:8px 0 12px'>", unsafe_allow_html=True)

    pages = [
        ("Today's Best Setups", "🏠"),
        ("History & Accuracy",  "📜"),
        ("Deep Dive",           "🔬"),
        ("Analysts",            "👤"),
        ("System Evolution",    "🧠"),
        ("Health Dashboard",    "🔧"),
    ]

    if "page" not in st.session_state:
        st.session_state.page = pages[0][0]

    for name, emoji in pages:
        active = st.session_state.page == name
        css_class = "nav-btn active" if active else "nav-btn"
        if st.button(f"{emoji}  {name}", key=f"nav_{name}",
                     use_container_width=True,
                     type="primary" if active else "secondary"):
            st.session_state.page = name
            st.rerun()

    st.markdown("<hr style='border-color:#1e293b;margin:12px 0 8px'>", unsafe_allow_html=True)
    st.caption("⏰ All times Pacific (Seattle)")

page = st.session_state.page

# ── Route to page ─────────────────────────────────────────────────────────────
if page == "Today's Best Setups":
    from views import main_dashboard
    main_dashboard.render()

elif page == "History & Accuracy":
    from views import history
    history.render()

elif page == "Deep Dive":
    from views import deep_dive
    deep_dive.render()

elif page == "Analysts":
    from views import analysts
    analysts.render()

elif page == "System Evolution":
    from views import system_evolution
    system_evolution.render()

elif page == "Health Dashboard":
    from views import health_dashboard
    health_dashboard.render()
