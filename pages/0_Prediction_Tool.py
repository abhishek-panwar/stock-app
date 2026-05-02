import streamlit as st
from dotenv import load_dotenv
load_dotenv()

st.set_page_config(
    page_title="Prediction Tool · Stock Analysis",
    page_icon="🛠",
    layout="wide",
    initial_sidebar_state="expanded",
)

from views._shared import inject_css
inject_css()

from views import prediction_tool
prediction_tool.render()
