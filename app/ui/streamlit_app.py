"""Minimal Streamlit chat UI."""
import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="D2C Analyst", page_icon="🏪", layout="wide")
st.title("D2C Analyst")
st.caption("Ask anything about your Shopify, Meta Ads, and Shiprocket data.")

merchant_id = st.sidebar.text_input("Merchant ID", value="demo")

if "history" not in st.session_state:
    st.session_state.history = []
if "messages" not in st.session_state:
    st.session_state.messages = []

# Render conversation history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("issues"):
            st.warning(f"Citation issues: {msg['issues']}")
        if msg.get("tool_calls"):
            with st.expander(f"Tool calls ({len(msg['tool_calls'])})"):
                for tc in msg["tool_calls"]:
                    st.json(tc)

# Chat input
if question := st.chat_input("What's my contribution margin by SKU this week?"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                resp = requests.post(
                    f"{API_URL}/chat",
                    json={
                        "question": question,
                        "merchant_id": merchant_id,
                        "history": st.session_state.history,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()

                answer = data["answer"]
                st.markdown(answer)

                if not data["all_citations_valid"]:
                    st.warning(f"⚠ Citation issues: {data['issues']}")

                if data["tool_calls"]:
                    with st.expander(f"Tool calls ({len(data['tool_calls'])})"):
                        for tc in data["tool_calls"]:
                            st.json(tc)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "issues": data.get("issues"),
                    "tool_calls": data.get("tool_calls"),
                })

                # Update conversation history for multi-turn
                st.session_state.history.append({"role": "user", "content": question})
                st.session_state.history.append({"role": "assistant", "content": answer})

            except Exception as e:
                st.error(f"Error: {e}")

# Sidebar: agent runs
st.sidebar.markdown("---")
st.sidebar.subheader("Agent Runs")
if st.sidebar.button("Refresh runs"):
    try:
        runs_resp = requests.get(f"{API_URL}/runs?merchant_id={merchant_id}", timeout=10)
        for run in runs_resp.json():
            icon = "✅" if run["status"] == "completed" else "❌" if run["status"] == "failed" else "⏳"
            label = f"{icon} {run['agent_name']} — {run['proposal_count']} proposals"
            if st.sidebar.button(label, key=run["id"]):
                st.session_state.selected_run = run["id"]
    except Exception as e:
        st.sidebar.error(f"Failed to load runs: {e}")
