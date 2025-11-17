"""
AWS TagSense - AI-Powered Cloud Tagging Assistant

A production-grade Streamlit application for scanning untagged AWS resources
and getting AI-powered guidance on tagging strategy and compliance.
"""

import os
import streamlit as st
from PIL import Image
import base64
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

# Load environment variables
load_dotenv()

# Initialize logging
from tagger_core.logging_config import setup_logging
from config import get_config

config = get_config()
setup_logging(
    level=config.app.log_level.value,
    format_type=config.app.log_format
)

# Import modules
from llm_backends import get_llm_factory
from memory import ConversationManager, AWSContextTracker
from tagger_core.ec2_scanner import EC2Scanner
from tagger_core.lambda_scanner import LambdaScanner
from prompts.system_prompts import get_system_prompt
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# Session State Initialization
# ============================================================================

def init_session_state():
    """Initialize Streamlit session state."""
    if "conversation_manager" not in st.session_state:
        st.session_state.conversation_manager = ConversationManager(
            max_history=config.app.conversation_history_length
        )

    if "context_tracker" not in st.session_state:
        st.session_state.context_tracker = AWSContextTracker()

    if "llm_factory" not in st.session_state:
        try:
            st.session_state.llm_factory = get_llm_factory(
                primary_backend=config.llm.primary_backend.value,
                fallback_backend=config.llm.fallback_backend.value if config.llm.fallback_backend else None,
                enable_cache=config.llm.enable_cache,
                model=config.llm.openai_model if config.llm.primary_backend.value == "openai" else config.llm.anthropic_model,
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens
            )
            logger.info(f"LLM factory initialized: {config.llm.primary_backend.value}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM factory: {e}")
            st.session_state.llm_factory = None

    if "last_scan_result" not in st.session_state:
        st.session_state.last_scan_result = None


# ============================================================================
# Helper Functions
# ============================================================================

def get_base64_image(image_path: str) -> str:
    """Convert image to base64 for HTML embedding."""
    with open(image_path, "rb") as img_file:
        encoded = base64.b64encode(img_file.read()).decode()
    return f"data:image/png;base64,{encoded}"


def render_scan_results(scan_result, resource_type: str):
    """Render scan results in a consistent format.

    Args:
        scan_result: ScanResult object
        resource_type: Human-readable resource type name
    """
    st.markdown(f"### 📊 {resource_type} Scan Results")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Resources", scan_result.total_resources)
    with col2:
        st.metric("Untagged", len(scan_result.untagged_resources))
    with col3:
        st.metric("Tagged", len(scan_result.tagged_resources))
    with col4:
        compliance = scan_result.tagging_compliance_rate
        st.metric("Compliance", f"{compliance:.1f}%")

    if scan_result.untagged_resources:
        st.warning(f"⚠️ Found {len(scan_result.untagged_resources)} untagged {resource_type.lower()} resource(s)")

        # Show table of untagged resources
        with st.expander("View Untagged Resources"):
            untagged_data = [
                {
                    "Resource ID": r.resource_id,
                    "State": r.state,
                    "Region": r.region,
                    **{f"Info: {k}": str(v)[:50] for k, v in list(r.metadata.items())[:3]}
                }
                for r in scan_result.untagged_resources[:20]  # Limit to 20 for performance
            ]
            st.dataframe(untagged_data, use_container_width=True)

            if len(scan_result.untagged_resources) > 20:
                st.caption(f"... and {len(scan_result.untagged_resources) - 20} more")

    else:
        st.success(f"✅ All {resource_type.lower()} resources are tagged!")


def get_ai_insight(scan_result, resource_type: str, use_case: str = "tagging_guidance"):
    """Get AI-powered insights about scan results.

    Args:
        scan_result: ScanResult object
        resource_type: Resource type name
        use_case: Prompt template use case

    Returns:
        AI-generated insight text
    """
    if not st.session_state.llm_factory:
        return "❌ LLM backend not available. Please check your API key configuration."

    try:
        # Get system prompt for the use case
        system_prompt = get_system_prompt(use_case)

        # Build context from scan results
        context_parts = []
        context_parts.append(f"**Scan Summary:**")
        context_parts.append(f"- Resource Type: {resource_type}")
        context_parts.append(f"- Region: {scan_result.region}")
        context_parts.append(f"- Total Resources: {scan_result.total_resources}")
        context_parts.append(f"- Untagged: {len(scan_result.untagged_resources)}")
        context_parts.append(f"- Tagging Compliance: {scan_result.tagging_compliance_rate:.1f}%")

        if scan_result.untagged_resources:
            context_parts.append(f"\n**Sample Untagged Resources:**")
            for resource in scan_result.untagged_resources[:5]:
                context_parts.append(f"- {resource.resource_id} (State: {resource.state})")

        context = "\n".join(context_parts)

        # Create prompt
        prompt = (
            f"I've scanned {resource_type} resources in AWS and found {len(scan_result.untagged_resources)} "
            f"untagged out of {scan_result.total_resources} total. Please provide:\n\n"
            "1. **Risk Assessment**: What are the top 3 risks of these untagged resources?\n"
            "2. **Quick Wins**: 2-3 immediate actions to improve tagging?\n"
            "3. **Recommended Tags**: Essential tags we should implement?"
        )

        # Generate response with fallback support
        response = st.session_state.llm_factory.generate_simple(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
            use_fallback=True
        )

        # Track in conversation memory
        st.session_state.conversation_manager.add_turn("user", prompt)
        st.session_state.conversation_manager.add_turn("assistant", response)

        return response

    except Exception as e:
        logger.error(f"Error generating AI insight: {e}", exc_info=True)
        return f"❌ Error generating insight: {str(e)}"


# ============================================================================
# Main Application
# ============================================================================

def main():
    """Main application entry point."""

    # Initialize session state
    init_session_state()

    # Page configuration
    st.set_page_config(
        page_title="AWS TagSense",
        page_icon="☁️",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Load assets
    try:
        aws_logo = Image.open("assets/aws_cloudfront_icon.png")
        image_base64 = get_base64_image("assets/aws_cloudfront_icon.png")
    except Exception as e:
        logger.warning(f"Could not load logo: {e}")
        aws_logo = None
        image_base64 = None

    # ========================================================================
    # Header
    # ========================================================================

    col1, col2 = st.columns([1, 12])
    with col1:
        if aws_logo:
            st.image(aws_logo, width=50)
    with col2:
        st.markdown(
            "<h1 style='padding-top: 10px; margin-bottom: 0;'>AWS TagSense</h1>",
            unsafe_allow_html=True
        )
        st.markdown(
            "<p style='font-size: 14px; color: gray;'>🚀 AI-Powered Cloud Governance  |  "
            f"v1.0 | {config.llm.primary_backend.value.title()} Backend</p>",
            unsafe_allow_html=True
        )

    # ========================================================================
    # Sidebar Configuration
    # ========================================================================

    if image_base64:
        st.sidebar.markdown(
            f"""
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                <img src="{image_base64}" width="20" height="20">
                <span style="font-weight: 600; font-size: 20px;">AWS Configuration</span>
            </div>
            """,
            unsafe_allow_html=True
        )
    else:
        st.sidebar.markdown("### ⚙️ AWS Configuration")

    # AWS region selection
    st.sidebar.markdown("#### 🌎 Region")
    region = st.sidebar.selectbox(
        "Region",
        config.aws.regions,
        index=0 if config.aws.default_region not in config.aws.regions else config.aws.regions.index(config.aws.default_region),
        label_visibility="collapsed"
    )

    # AWS profile
    st.sidebar.markdown("#### 🧩 Profile")
    profile = st.sidebar.text_input(
        "Profile",
        value=config.aws.profile,
        label_visibility="collapsed"
    )

    st.sidebar.markdown("---")

    # Resource type selection
    st.sidebar.markdown("#### 🔍 Resource Scanner")
    resource_type = st.sidebar.radio(
        "Select Resource Type",
        ["EC2 Instances", "Lambda Functions"],
        label_visibility="collapsed"
    )

    # Scan button
    scan_button = st.sidebar.button(
        f"🔎 Scan Untagged {resource_type}",
        use_container_width=True,
        type="primary"
    )

    # Display context summary in sidebar
    if len(st.session_state.context_tracker) > 0:
        st.sidebar.markdown("---")
        st.sidebar.markdown("#### 📊 Session Summary")
        stats = st.session_state.context_tracker.get_statistics()
        st.sidebar.caption(f"Scans: {stats['total_scans']}")
        if stats['totals']['total_resources'] > 0:
            st.sidebar.caption(
                f"Compliance: {stats['totals']['tagging_compliance_pct']:.1f}%"
            )

    # Clear history button
    if st.sidebar.button("🗑️ Clear History"):
        st.session_state.conversation_manager.clear()
        st.session_state.context_tracker.clear()
        st.session_state.last_scan_result = None
        st.rerun()

    # ========================================================================
    # Main Content Area
    # ========================================================================

    # Perform scan if button clicked
    if scan_button:
        with st.spinner(f"🔍 Scanning {resource_type} in {region}..."):
            try:
                # Initialize appropriate scanner
                if resource_type == "EC2 Instances":
                    scanner = EC2Scanner(region=region, profile=profile)
                else:
                    scanner = LambdaScanner(region=region, profile=profile)

                # Perform scan
                scan_result = scanner.scan()
                st.session_state.last_scan_result = scan_result

                # Record in context tracker
                st.session_state.context_tracker.record_scan(
                    region=region,
                    profile=profile,
                    resource_type=resource_type,
                    total_resources=scan_result.total_resources,
                    untagged_resources=len(scan_result.untagged_resources),
                    resource_ids=[r.resource_id for r in scan_result.untagged_resources[:10]]
                )

                logger.info(
                    f"Scan completed: {resource_type} in {region}",
                    extra={
                        "region": region,
                        "resource_type": resource_type,
                        "total": scan_result.total_resources,
                        "untagged": len(scan_result.untagged_resources)
                    }
                )

            except Exception as e:
                logger.error(f"Scan failed: {str(e)}", exc_info=True)
                st.error(f"❌ Scan failed: {str(e)}")
                st.session_state.last_scan_result = None

    # Display scan results
    if st.session_state.last_scan_result:
        render_scan_results(st.session_state.last_scan_result, resource_type)

        # AI Insights
        if len(st.session_state.last_scan_result.untagged_resources) > 0:
            st.markdown("---")
            st.markdown("### 🤖 AI Assistant Insights")

            insight_type = st.selectbox(
                "What type of analysis would you like?",
                [
                    ("Tagging Guidance", "tagging_guidance"),
                    ("Compliance Check", "compliance_check"),
                    ("Cost Analysis", "cost_analysis"),
                    ("Remediation Plan", "remediation")
                ],
                format_func=lambda x: x[0]
            )

            if st.button("Generate AI Insight", type="secondary"):
                with st.spinner("🤖 Analyzing with AI..."):
                    insight = get_ai_insight(
                        st.session_state.last_scan_result,
                        resource_type,
                        use_case=insight_type[1]
                    )

                    if insight.startswith("❌"):
                        st.error(insight)
                    else:
                        st.success("✅ AI Analysis Complete")
                        st.markdown(insight)

    # ========================================================================
    # Chat Interface
    # ========================================================================

    st.markdown("---")
    st.markdown("### 💬 Chat with AI Assistant")
    st.caption("Ask about AWS tagging best practices, compliance, cost optimization, and more.")

    # Display conversation history
    if len(st.session_state.conversation_manager) > 0:
        with st.expander("📜 Conversation History", expanded=False):
            for turn in st.session_state.conversation_manager.get_history():
                role_emoji = "👤" if turn.role == "user" else "🤖"
                st.markdown(f"**{role_emoji} {turn.role.title()}:**")
                st.markdown(turn.content)
                st.markdown("---")

    # Chat input
    user_message = st.text_area(
        "Your Question",
        placeholder="e.g., What are the essential tags for HIPAA compliance?",
        height=100,
        label_visibility="collapsed"
    )

    col1, col2 = st.columns([1, 5])
    with col1:
        send_button = st.button("Send Message", type="primary", use_container_width=True)

    if send_button and user_message.strip():
        with st.spinner("🤖 Thinking..."):
            try:
                # Get AWS context if available
                aws_context = ""
                if st.session_state.context_tracker and len(st.session_state.context_tracker) > 0:
                    aws_context = st.session_state.context_tracker.get_context_for_prompt()

                # Generate response
                system_prompt = get_system_prompt("general")
                response = st.session_state.llm_factory.generate_simple(
                    prompt=user_message,
                    system_prompt=system_prompt,
                    context=aws_context,
                    use_fallback=True
                )

                # Add to conversation history
                st.session_state.conversation_manager.add_turn("user", user_message)
                st.session_state.conversation_manager.add_turn("assistant", response)

                # Display response
                st.success("✅ AI Response:")
                st.markdown(response)

            except Exception as e:
                logger.error(f"Chat error: {str(e)}", exc_info=True)
                st.error(f"❌ Error: {str(e)}")

    # ========================================================================
    # Footer
    # ========================================================================

    st.markdown("---")
    st.caption(
        "🛡️ AWS TagSense | Powered by AI | "
        f"Using {config.llm.primary_backend.value.title()} "
        f"{'with ' + config.llm.fallback_backend.value.title() + ' fallback' if config.llm.fallback_backend else ''}"
    )


if __name__ == "__main__":
    main()
