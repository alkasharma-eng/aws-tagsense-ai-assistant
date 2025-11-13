from dotenv import load_dotenv
import os
from tagger_core.auto_tagger import get_ec2_client, find_untagged_instances
import streamlit as st
from PIL import Image
import base64

def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        encoded = base64.b64encode(img_file.read()).decode()
    return f"data:image/png;base64,{encoded}"

# Convert image to base64
image_base64 = get_base64_image("assets/aws_cloudfront_icon.png")

# --- Load secrets ---
load_dotenv()
openai_key = os.getenv("OPENAI_API_KEY")

# Load image
aws_logo = Image.open("assets/aws_cloudfront_icon.png")

# --- Look and feel: Make better use of the horizontal space ---
st.set_page_config(layout="wide")

# --- UI Title ---
# Display in top row with title
col1, col2 = st.columns([1, 12])
with col1:
    st.image(aws_logo, width=50)  # Adjust width as needed
with col2:
    st.markdown(
        "<h1 style='padding-top: 10px; margin-bottom: 0;'>AWS TagSense</h1>",
        unsafe_allow_html=True
    )
    st.markdown(
        "<p style='font-size: 14px; color: gray;'>🧪 <em>Prototype – v0.1</em></p>",
        unsafe_allow_html=True
    )
    # st.markdown("## AWS TagSense")
    # st.caption("#### 🧪 Prototype – v0.1")

# --- Sidebar Config ---
# config_icon = Image.open("assets/aws_cloudfront_icon.png")
# Display in sidebar before the header
# st.sidebar.image(config_icon, width=24)
# st.sidebar.markdown("# AWS Config")

# Display icon + text inline
st.sidebar.markdown(
    f"""
    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
        <img src="{image_base64}" width="20" height="20">
        <span style="font-weight: 600; font-size: 20px;">AWS Config</span>
    </div>
    """,
    unsafe_allow_html=True
)
# st.divider()
st.sidebar.markdown("#### 🌎 Region:")
region = st.sidebar.text_input(label="Hidden Label", value="us-west-2", label_visibility="collapsed")
# region = st.sidebar.text_input("Region:", value="us-west-2")
st.sidebar.markdown("#### 🧩 Profile Name:")
profile = st.sidebar.text_input(label="Hidden Label", value="auto-tagger-dev", label_visibility="collapsed")

# --- Sidebar Button: EC2 Scanner ---
if st.sidebar.button("Scan Untagged EC2"):
    with st.spinner("🔍 Connecting to AWS..."):
        try:
            ec2 = get_ec2_client(region, profile)
            # untagged = find_untagged_instances(ec2)
            # Simulated EC2 instances for testing AI prompt
            untagged = [
                {"InstanceId": "i-1234567890abcdef0", "State": "running"},
                {"InstanceId": "i-abcdef1234567890", "State": "stopped"},
            ]

            if not untagged:
                st.success("✅ No untagged EC2 instances found.")
            else:
                st.warning(f"⚠️ Found {len(untagged)} untagged instance(s):")
                st.dataframe(untagged)

                # 🔍 AI Insight
                st.markdown("### 🤖 AI Assistant Insight")
                from tagger_core.llm_client import ask_gpt

                instance_count = len(untagged)
                sample_ids = ", ".join(i["InstanceId"] for i in untagged[:5])
                context = f"There are {instance_count} untagged EC2 instances in region {region}. Sample IDs: {sample_ids}."
                prompt = (
                    "As a cloud infrastructure and compliance assistant, explain why untagged EC2 instances are a problem "
                    "in terms of security, cost, and auditability. Then, recommend a first action to improve tagging hygiene."
                )

                gpt_insight = ask_gpt(prompt, context)
                # st.write(gpt_insight)

                if "⚠️ Error" in gpt_insight:
                    st.error(gpt_insight)
                else:
                    st.success("Here’s what the assistant recommends:")
                    st.markdown(gpt_insight)

        except Exception as e:
            st.error(f"❌ Error: {e}")

# --- Divider ---
st.markdown("---")

# --- AI Prompt UI ---
st.subheader("💬 Ask TagSense AI Assist")
st.markdown("##### 🧠 What do you want help with?")
user_prompt = st.text_area(label="Hidden Label",
                           placeholder="e.g. Why is tagging important?",
                           label_visibility="collapsed",
                           key="prompt_input")
# user_prompt = st.text_area("What do you want help with?", placeholder="e.g. Why is tagging important?")

if st.button("Ask GPT"):
    st.session_state.run_gpt = True

# Process after click (only runs once)
if st.session_state.get("run_gpt", False):
    with st.spinner("🤖 Thinking..."):
        from tagger_core.llm_client import ask_gpt
        answer = ask_gpt(st.session_state.prompt_input)
        # st.markdown("**AI Assistant Response:**")
        st.success("✅ AI Assistant Response")
        st.write(answer)
    st.session_state.run_gpt = False
