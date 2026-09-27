"""Explicit local single-user display identity, not a Telegram login."""
import streamlit as st

def session_actor():
    key='osbb_session_actor'
    if not str(st.session_state.get(key) or '').strip():
        st.session_state[key]='San'
    actor=st.sidebar.text_input('Кто вносит записи',key=key)
    return actor.strip() or 'San'
