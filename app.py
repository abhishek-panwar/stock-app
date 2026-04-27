import streamlit as st
from dotenv import load_dotenv
load_dotenv()

st.set_page_config(
    page_title="Stock Analysis",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar nav ───────────────────────────────────────────────────────────────
st.sidebar.title("📊 Stock Analysis")
st.sidebar.markdown("---")

pages = {
    "Today's Best Setups": "🏠",
    "History & Accuracy": "📜",
    "Deep Dive": "🔬",
    "Analysts": "👤",
    "System Evolution": "🧠",
    "Health Dashboard": "🔧",
}

page = st.sidebar.radio(
    "Navigation",
    list(pages.keys()),
    format_func=lambda x: f"{pages[x]}  {x}",
)

st.sidebar.markdown("---")
st.sidebar.caption("All times Pacific Time (Seattle)")

# ── Route to page ─────────────────────────────────────────────────────────────
if page == "Today's Best Setups":
    from pages import main_dashboard
    main_dashboard.render()

elif page == "History & Accuracy":
    from pages import history
    history.render()

elif page == "Deep Dive":
    from pages import deep_dive
    deep_dive.render()

elif page == "Analysts":
    from pages import analysts
    analysts.render()

elif page == "System Evolution":
    from pages import system_evolution
    system_evolution.render()

elif page == "Health Dashboard":
    from pages import health_dashboard
    health_dashboard.render()
