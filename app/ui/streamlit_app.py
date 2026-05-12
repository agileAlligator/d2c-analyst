"""Minimal Streamlit chat UI."""
import os
import re

import requests
import streamlit as st


def _clean_answer(text: str) -> str:
    """Strip <cite ref="...">value</cite> tags — keep just the value."""
    return re.sub(r'<cite ref="[^"]*">([^<]*)</cite>', r'\1', text)

API_URL = os.getenv("API_URL", "http://localhost:10001")

st.set_page_config(page_title="D2C Analyst", page_icon="🏪", layout="wide")
st.title("D2C Analyst")
st.caption("Ask anything about your Shopify, Meta Ads, and Shiprocket data.")

merchant_id = st.sidebar.text_input("Merchant ID", value="demo")

SUGGESTED_QUESTIONS = [
    "What was total revenue in the last 30 days?",
    "What is my contribution margin per order this week?",
    "Which orders had negative contribution margin last month?",
    "Which courier has the highest RTO rate?",
    "What is our RTO rate by courier in the last 30 days?",
    "How much did we spend on Meta Ads in the last 30 days?",
    "What is our CAC in the last 30 days?",
    "What was our total ad spend vs total revenue in the last 14 days? What is the ROAS?",
    "Which Meta campaign spent the most in the last 30 days?",
    "Compare revenue between the last 7 days and the last 30 days.",
    "What is our average order value in the last 30 days?",
    "Show me the top 5 orders by revenue in the last 30 days.",
]

if "history" not in st.session_state:
    st.session_state.history = []
if "messages" not in st.session_state:
    st.session_state.messages = []

# Render conversation history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            if msg.get("citations_valid"):
                st.caption("✓ All numbers verified against source data")
            elif msg.get("issues"):
                st.warning(f"⚠ Some numbers could not be verified: {msg['issues']}")
            r = msg.get("routing") or {}
            if r:
                model = r.get("model", "")
                label = f"{'⚡' if r.get('tier') == 'cheap' else '🧠'} {model}"
                if r.get("escalated"):
                    label += " (escalated)"
                st.caption(f"{label} · {r.get('reason', '')}")
        if msg.get("tool_calls"):
            with st.expander(f"Tool calls ({len(msg['tool_calls'])})"):
                for tc in msg["tool_calls"]:
                    st.json(tc)

# Suggested questions — pill buttons above the chat input
st.markdown("**Try asking:**")
cols = st.columns(3)
selected_suggestion = None
for i, q in enumerate(SUGGESTED_QUESTIONS):
    if cols[i % 3].button(q, key=f"suggest_{i}", use_container_width=True):
        selected_suggestion = q

question = selected_suggestion or st.chat_input("What's my contribution margin by SKU this week?")

if question:
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
                display_answer = _clean_answer(answer)
                st.markdown(display_answer)

                if data["all_citations_valid"]:
                    st.caption("✓ All numbers verified against source data")
                else:
                    st.warning(f"⚠ Some numbers could not be verified: {data['issues']}")

                routing = data.get("routing") or {}
                if routing:
                    model = routing.get("model", "")
                    reason = routing.get("reason", "")
                    escalated = routing.get("escalated", False)
                    label = f"{'⚡' if routing.get('tier') == 'cheap' else '🧠'} {model}"
                    if escalated:
                        label += " (escalated)"
                    st.caption(f"{label} · routed via {reason}")

                if data["tool_calls"]:
                    with st.expander(f"Tool calls ({len(data['tool_calls'])})"):
                        for tc in data["tool_calls"]:
                            st.json(tc)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": display_answer,
                    "citations_valid": data.get("all_citations_valid"),
                    "issues": data.get("issues"),
                    "tool_calls": data.get("tool_calls"),
                    "routing": data.get("routing"),
                })

                # Keep raw answer (with cite tags) in history so the LLM
                # can reference provenance IDs in follow-up turns
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
