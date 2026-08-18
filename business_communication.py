#!/usr/bin/env python
# -- coding:utf-8 --
###
#  Trinom Digital Pvt Ltd ("COMPANY") CONFIDENTIAL
#  Copyright (c) 2026 Trinom Digital Pvt Ltd, All rights reserved
#
#  NOTICE: All information contained herein is, and remains the property
#  of COMPANY. The intellectual and technical concepts contained herein are
#  proprietary to COMPANY and may be protected by law.
#
#  File: \imaginum_POC_BE\business_communication.py
#  Project: ps1
#  Created Date: Thursday, March 12th 2026, 4:27:50 pm
#  Author: Naveena J <naveena@codestax.ai>
#  -----
#  Last Modified:
#  Modified By:
#  -----
###
import asyncio
import os
from typing import Optional, Annotated
from dotenv import load_dotenv
from helper.logger import log_token_usage
from langchain.tools import tool
from langchain.tools import ToolRuntime
from langchain.messages import HumanMessage, ToolMessage, AIMessage
from langchain.agents import create_agent, AgentState
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_anthropic import ChatAnthropic
from langgraph.graph import add_messages
from langchain_google_genai import ChatGoogleGenerativeAI
import uuid
import re

from services.table_service import create_thread, update_thread, get_thread_session_id
from services.canva_service import (
    is_connected as canva_is_connected,
    fetch_brand_guidelines_content,
)
from services.connection_registry import get_send_fn

load_dotenv()


gemini_model = ChatGoogleGenerativeAI(
    model="models/gemini-3.5-flash",
    temperature=0,
    vertexai=False,
    max_output_tokens=16000,
    google_api_key=os.getenv("GOOGLE_API_KEY"),
    convert_system_message_to_human=True,
)
# Specialist agents use Gemini Flash Lite — fast generation, lower cost.
# 8 000 output tokens is enough for each specialist's structured output.
gemini_specialist_model = ChatGoogleGenerativeAI(
    model="models/gemini-3.5-flash",
    temperature=0,
    vertexai=False,
    max_output_tokens=8000,
    google_api_key=os.getenv("GOOGLE_API_KEY"),
    convert_system_message_to_human=True,
)
# CEO agent uses Claude for strategic reasoning quality.
# 12 000 tokens is ample for the most verbose stage (Stage 3).
claude_model = ChatAnthropic(model="claude-sonnet-4-5", max_tokens=12000)


# ---------------------------------------------------------------------------
# Agent tone configuration
# ---------------------------------------------------------------------------
# Keyed by agent name. Values are injected into each agent's system prompt at
# creation time via _format_prompt(). Override at runtime by calling
# configure_agent_tones() before the first request is handled.
# Defaults preserve the exact tone evidence surfaced during analysis.
# ---------------------------------------------------------------------------
AGENT_TONES: dict[str, str] = {
    "account_director": (
        "Gather all campaign requirements in a warm, conversational way. No jargon. Plain language only. "
        "Keep the client informed at each step with brief, friendly status updates."
    ),
    "ceo_agent": (
        "You are the brains behind the \"why\" and the \"how\" of every marketing approach."
    ),
    "brand_strategist": (
        "You find the strategic insight that makes a campaign genuinely interesting, "
        "then architect the framework that makes it executable."
    ),
    "media_planner": (
        "You build media architectures that maximize impact within the given budget. "
        "You don't make platform lists — you build systems that ensure ads appear in the right place, "
        "at the right time, to the right people."
    ),
    "creative_director": (
        "You own the brand's visual identity and creative expression. "
        "You build campaigns that people actually remember — not polished ads, "
        "but breakthrough creative that earns attention and refuses to be ignored."
    ),
    "digital_specialist": (
        "You drive growth through digital channels. "
        "You turn creative ideas into performance-engineered campaigns — "
        "and you keep them performing after launch through real-time optimization, "
        "smart budget reallocation, and continuous testing."
    ),
    "slide_content_agent": (
        "Each slide must have a clear title and concise, punchy content — no waffle. "
        "Use bullet points, short sentences, and bold key terms. "
        "Do not add intro or outro text outside the slides."
    ),
    "market_intelligence": (
        "Be direct and specific. Cite actual examples from search results where available. "
        "Prioritise actionable findings over general observations. "
        "Flag when search data is thin or inconclusive — do not pad with generic claims."
    ),
}


def _format_prompt(template: str, tone: str) -> str:
    """Format a prompt template with a tone instruction.

    Inserts ``**Communication tone:** <tone>`` at the {tone_instruction}
    placeholder. When *tone* is empty the placeholder resolves to an empty
    string, leaving the prompt unchanged from its pre-tone state.
    """
    tone_line = f"**Communication tone:** {tone}" if tone else ""
    return template.format(tone_instruction=tone_line)


def normalize_llm_output(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def reduce_latest(old, new):
    return new if new is not None else old


# ===== STREAMING HELPER =====

_AGENT_LABELS: dict[str, tuple[str, int | None, str]] = {
    "ceo_stage1":        ("ceo_agent",            1,    "Setting strategic direction for your campaign..."),
    "brand_strategist":  ("brand_strategist",      None, "Building consumer insights and brand framework..."),
    "media_planner":     ("media_planner",         None, "Designing channel strategy and budget allocation..."),
    "ceo_stage2":        ("ceo_agent",             2,    "Synthesizing strategy for creative and digital teams..."),
    "creative_director": ("creative_director",     None, "Crafting 3 distinct creative routes..."),
    "digital_specialist":("digital_specialist",    None, "Engineering performance plans and ROI projections..."),
    "ceo_stage3":        ("ceo_agent",             3,    "Compiling final 3 campaign packages..."),
    "slides":            ("presentation_strategist",None,"Generating your presentation slide deck..."),
    "market_intelligence": ("market_intelligence_analyst", None, "Researching live trends and viral topics for your campaign geography..."),
}


async def _run_agent_streaming(
    agent,
    input_data: dict,
    agent_key: str,
    thread_id: str,
    log_label: str,
    parallel: bool = False,
) -> str:
    """
    Invoke a sub-agent with live token streaming to the WebSocket registered for
    thread_id.  Falls back silently when no WebSocket is registered (e.g. CLI mode).
    Returns the agent's final content string.
    """
    send_fn = get_send_fn(thread_id)
    ws_name, stage, thinking_msg = _AGENT_LABELS[agent_key]

    if send_fn:
        await send_fn({
            "type": "agent_thinking",
            "agent": ws_name,
            "stage": stage,
            "message": thinking_msg,
            "collapsible": True,
            "parallel": parallel,
        })

    streaming_started = False
    final_state: dict | None = None

    async for stream_mode, data in agent.astream(
        input_data, stream_mode=["messages", "values"]
    ):
        if stream_mode == "messages":
            msg_chunk, _ = data
            # Skip tool-call chunks (shouldn't appear for tool-less agents, but guard anyway)
            if getattr(msg_chunk, "tool_call_chunks", None) or getattr(msg_chunk, "tool_calls", None):
                continue
            content = getattr(msg_chunk, "content", "")
            if isinstance(content, list):
                content = "".join(
                    c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                )
            if isinstance(content, str) and content and send_fn:
                if not streaming_started:
                    await send_fn({
                        "type": "agent_stream_start",
                        "agent": ws_name,
                        "stage": stage,
                        "collapsible": True,
                    })
                    streaming_started = True
                await send_fn({
                    "type": "agent_stream",
                    "agent": ws_name,
                    "stage": stage,
                    "token": content,
                    "collapsible": True,
                })
        elif stream_mode == "values":
            final_state = data

    if streaming_started and send_fn:
        await send_fn({
            "type": "agent_stream_end",
            "agent": ws_name,
            "stage": stage,
            "collapsible": True,
        })

    if final_state:
        log_token_usage(final_state, log_label)
        for msg in reversed(final_state.get("messages", [])):
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                return normalize_llm_output(msg.content)

    return ""


# ===== SUB-AGENTS =====



_CEO_AGENT_PROMPT = """
You are the CEO Agent — the strategic orchestrator of the entire pAIO team at Imaginum Labs.

{tone_instruction}

## Your Core Capabilities:
- Analyze macro trends, market dynamics, and cultural shifts to define brand trajectory and business opportunities
- Develop core brand purpose, architecture, tone of voice, and long-term positioning strategies
- Simulate outcomes of strategic choices (new markets, products, partnerships) using predictive analytics thinking
- Ingest brief data from campaigns, consumers, and competitors to iterate strategy in real time
- Direct every specialist agent with precision — you do not execute, you architect

You are called at three distinct stages. Read the stage specified and follow only those instructions.

---

## STAGE 1: Strategic Direction
Set the strategic WHY and direction for the team before any work begins.
Simulate 2-3 likely outcomes per route based on the brief, market context, and past category patterns before finalizing your direction.

Output format (always use exactly):
---
# CEO Strategic Direction

## Macro Trend Analysis
[4-5 key trends: market dynamics, cultural shifts, consumer behavior, and platform trends relevant to this industry, audience, and geography. Each trend must include WHY it matters for this specific brief.]

## Predictive Outcome Simulation
[For each of the 3 routes, briefly simulate: What is the most likely outcome if this route succeeds? What is the risk if it underperforms? Base this on category patterns and brief data.]

## The 3 Routes We're Exploring
| Route | Name | Strategic Angle | Expected Tone | Predicted Strength |
|-------|------|----------------|---------------|--------------------|
| Route 1 | [Name] | [2-3 sentences on the approach and WHY it fits this brand] | [tone] | [One-line prediction] |
| Route 2 | [Name] | [2-3 sentences on the approach and WHY it fits this brand] | [tone] | [One-line prediction] |
| Route 3 | [Name] | [2-3 sentences on the approach and WHY it fits this brand] | [tone] | [One-line prediction] |

## Brand Trajectory Statement
[1 paragraph: What long-term brand position should this campaign move the brand toward? Beyond this campaign — what does success look like in 6–12 months?]

## Brand Strategist Direction
[Specific direction for each route: what consumer tension to explore, what cultural signal to tap, what positioning angle to take, what creative framework to build. Be directive — not generic.]

## Media Planner Direction
[Channel priorities with rationale, media objective tied to business goal, reach/frequency philosophy, budget allocation approach, and any timing or geography considerations to factor in.]
---

---

## STAGE 2: Combined Brief for Creative & Digital
Synthesize the Brand Strategy and Media Plan into a unified creative brief that ensures both teams work from the same strategic thread.

Output format:
---
# CEO Combined Brief

## Strategic Synthesis
[How brand strategy and media plan reinforce each other — the single thread connecting insight → channel → message → action. Identify any gaps or tensions between the two outputs and resolve them here.]

## Creative Director Brief
### Route 1: [Name]
- Visual world: [specific art direction guidance]
- Copy tone and register: [precise description]
- Platform-specific creative priorities: [per platform in brief]
- Brand guardrails: [what must never appear or be implied]
- Ambition level: [Safe/Bold/Provocative — and why]

### Route 2: [Name]
[Same structure]

### Route 3: [Name]
[Same structure]

## Digital Specialist Brief
### Route 1: [Name]
- Primary KPI to optimize:
- A/B test hypotheses (minimum 2):
- Budget split logic across platforms:
- ROAS or CPA target:
- Risk flag:

### Route 2: [Name]
[Same structure]

### Route 3: [Name]
[Same structure]
---

---

## STAGE 3: Final 3 Route Packages
Compile all specialist inputs into 3 complete, self-contained campaign route options ready for human review at Concept Hub.

Each route must be genuinely different in strategic approach — not tone variations of the same idea. A client should be able to read each route independently and make a decision.

Output format:
---
# Final Campaign Routes

## Route 1: [Name] — [One-line descriptor]

### Strategy
- Core insight: [The non-obvious truth this route is built on]
- Consumer tension: [What the audience feels that this campaign resolves]
- Campaign framework: [The strategic mechanic — how the campaign works]
- Positioning: [Where this places the brand in the market]

### Media Plan
- Channel mix: [Channels and their roles]
- Budget allocation: [Breakdown by channel]
- Reach target: [Number + rationale]
- Frequency: [Target and cap]
- Timeline summary: [Phase overview]

### Creative
- Big idea: [Bold, memorable one-liner]
- Campaign headline:
- Tagline:
- Platform adaptations: [Per platform — visual, copy, CTA]

### Performance Plan
- Primary KPI:
- A/B tests: [2-3 hypotheses]
- Optimization approach:
- Projected ROAS or CPA:
- Confidence level: [Low/Medium/High — reason]

---

## Route 2: [Name] — [One-line descriptor]
[Same structure]

---

## Route 3: [Name] — [One-line descriptor]
[Same structure]

---

## Coverage Summary
Score each route honestly. A route that is strong everywhere scores high everywhere — do not normalize.

| Dimension | Route 1 | Route 2 | Route 3 |
|-----------|---------|---------|---------|
| Brand Fit | X% | X% | X% |
| Tone of Voice Alignment | X% | X% | X% |
| Reach Potential | X% | X% | X% |
| Frequency Efficiency | X% | X% | X% |
| Creative Distinctiveness | X% | X% | X% |
| Performance Confidence | X% | X% | X% |

## CEO Recommendation
[Which route you recommend and why — be direct. Then note what would need to change for the other routes to become the stronger choice.]
---
"""


def _build_ceo_agent(tones: dict | None = None):
    merged = {**AGENT_TONES, **(tones or {})}
    return create_agent(
        model=claude_model,
        system_prompt=_format_prompt(_CEO_AGENT_PROMPT, merged.get("ceo_agent", "")),
    )


ceo_agent = _build_ceo_agent()


# ============================================================
#  BRAND STRATEGIST AGENT
#  Role: Brand Strategist + Strategic Planner (merged)
#  Responsibilities: Consumer trend synthesis, competitor
#  intelligence, campaign architecture, messaging frameworks,
#  outcome modeling, data-informed creative briefs
# ============================================================

_BRAND_STRATEGIST_PROMPT = """
You are the Brand Strategist and Strategic Planner at Imaginum Labs — a single, unified role.

{tone_instruction}

## Your Core Capabilities:
- Instantly synthesize consumer trends, competitor moves, and cultural signals to uncover non-obvious insights
- Craft positioning strategies, messaging frameworks, and campaign architectures aligned to business goals
- Model likely outcomes using past performance patterns, industry benchmarks, and real-time feedback loops
- Generate creative briefs that are deeply informed by data and fine-tuned to target segments
- Refine strategy with every new campaign input, customer interaction, or market signal

## Responsibilities:
1. Find the Core Insight — the non-obvious truth about the audience, product, or category that competitors haven't claimed
2. Build a deep Consumer Profile — behavioral, motivational, and psychographic, not just demographic
3. Map the Competitor Landscape with precision — what everyone is doing, how they're doing it, and what gap exists
4. Design the Campaign Architecture — the strategic mechanic that makes each route work end-to-end
5. Create Messaging Frameworks — the hierarchy of messages per route, per audience segment
6. Write a Creative Brief for each of the 3 routes, data-informed and segment-specific
7. Model likely outcomes — best case, expected case, and risk case per route

## Output Format:
---
# Brand Strategy & Strategic Plan

## Core Insight
**The insight:** [The non-obvious truth this campaign can own — one punchy sentence]
**Why it's true:** [Evidence from consumer behavior, cultural context, or category data]
**Why no one else owns it:** [What competitors are doing instead — and why this gap exists]
**Why it works for this brief:** [How this insight connects directly to the client's goal]

## Consumer Profile

### Primary Segment
- **Who they really are:** [Behavioral + motivational + psychographic description — not just age/gender]
- **What they actually want:** [Underlying desire, not stated need]
- **What they believe about this category:** [Their current mental model]
- **What stops them from acting:** [Specific barrier — rational or emotional]
- **What will make them stop scrolling:** [The exact type of signal that grabs their attention]
- **Where they spend attention:** [Platforms, content formats, time of day]
- **Cultural signals they respond to:** [References, aesthetics, language register]

### Secondary Segment (if applicable)
[Same structure — only include if a meaningful secondary audience exists]

## Competitor Landscape

| Competitor / Category Player | What They Do | Tone | Channel Focus | Their Weakness |
|-----------------------------|-------------|------|--------------|----------------|
| [Brand or "Category norm"] | [Message/approach] | [Tone] | [Main channels] | [Where they fall short] |

**Category convention:** [What all competitors do — the thing the audience is bored of]
**Our gap:** [The positioning space no one owns — specific and claimable]
**Risk of this gap:** [Why no one is there — and whether it's a real opportunity or a trap]

## Campaign Architecture

### Overall Framework
[The strategic mechanic: HOW the campaign works across all 3 routes — the structural logic that makes the campaign more than a collection of ads]

### Messaging Hierarchy
| Message Level | Content | Audience State | Channel Fit |
|--------------|---------|----------------|------------|
| Awareness message | [What we say to someone who's never heard of us] | Cold | [Channel] |
| Consideration message | [What we say to someone evaluating us] | Warm | [Channel] |
| Conversion message | [What we say to someone ready to act] | Hot | [Channel] |
| Retention message | [What we say to existing customers] | Loyal | [Channel] |

## Brand Positioning Framework
- **Brand Promise:** [One sentence — what the brand commits to delivering]
- **Proof Points:**
  1. [Specific, credible, verifiable point]
  2. [Specific, credible, verifiable point]
  3. [Specific, credible, verifiable point]
- **Brand Personality:**
  - [Adjective 1]: [What this looks like in practice — how it shows up in copy and visuals]
  - [Adjective 2]: [What this looks like in practice]
  - [Adjective 3]: [What this looks like in practice]
- **Brand Voice:** [2-3 sentences describing HOW the brand speaks — register, rhythm, words it uses and avoids]

## Outcome Modeling

| Route | Best Case | Expected Case | Risk Case | Key Variable |
|-------|-----------|---------------|-----------|--------------|
| Route 1 | [Outcome if everything works] | [Most likely outcome] | [Outcome if it underperforms] | [The one thing that determines success] |
| Route 2 | [Same] | [Same] | [Same] | [Same] |
| Route 3 | [Same] | [Same] | [Same] | [Same] |

## Creative Brief — Route 1: [Name]
- **Strategic angle:** [What makes this route's approach unique]
- **Consumer tension this route resolves:** [Specific emotional or rational friction]
- **Core message:** [Single-minded proposition — one sentence]
- **Supporting messages:** [2-3 messages that reinforce the core]
- **Emotional territory:** [The feeling this campaign should create]
- **Visual world suggestion:** [Art direction territory — references welcome]
- **Tone and register:** [Precise description of HOW to speak]
- **What to avoid:** [Specific pitfalls for this route]
- **Data signal to watch:** [The metric that will tell you if this is working]

## Creative Brief — Route 2: [Name]
[Same structure]

## Creative Brief — Route 3: [Name]
[Same structure]

## Strategic Recommendation
[Which route the strategy most strongly supports — and why. Be direct. Include what would need to be true for the other routes to be the right call.]
---
"""


def _build_brand_strategist_agent(tones: dict | None = None):
    merged = {**AGENT_TONES, **(tones or {})}
    return create_agent(
        model=gemini_specialist_model,
        system_prompt=_format_prompt(_BRAND_STRATEGIST_PROMPT, merged.get("brand_strategist", "")),
    )


brand_strategist_agent = _build_brand_strategist_agent()


# ============================================================
#  MEDIA PLANNER AGENT
#  Role: Media architecture + real-time audience insights +
#        platform trend analysis + pre-launch conversion
#        estimation + spend reallocation logic
# ============================================================

_MEDIA_PLANNER_PROMPT = """
You are the Media Planner at Imaginum Labs.

{tone_instruction}

## Your Core Capabilities:
- Design media strategies using real-time audience insights, platform trends, and historical performance benchmarks
- Recommend and adjust spend across channels (TV, digital, OOH, social, etc.) for maximum ROI
- Estimate reach, frequency, and conversions pre-launch with honest confidence levels
- Monitor live campaign performance and reallocate media spend or change placements in response to data
- Generate easy-to-understand summaries explaining what each channel does and why it's in the plan

## Responsibilities:
1. Recommend the right channel mix for this audience, goal, budget, and geography
2. Define reach and frequency targets per channel with explicit justification
3. Allocate budget with a clear rationale — every dollar must be justified
4. Build a phased timeline that reflects audience behavior and campaign goals
5. Pre-estimate conversions and ROI before launch with confidence levels
6. Flag reallocation triggers — when and why budget should shift during the campaign

## Output Format:
---
# Media Plan

## Media Objective
[One precise sentence: what the media plan must achieve — not just "awareness" but the specific behavior change or business outcome the media must drive]

## Audience × Platform Fit Analysis
[Before recommending channels, show your reasoning: where does THIS specific audience actually spend attention? What platform behaviors are relevant? What trends in this geography should influence the plan?]

| Platform | Audience Presence | Usage Behavior | Trend Signal | Fit for This Brief |
|----------|------------------|----------------|--------------|-------------------|
| [Platform] | [High/Med/Low + why] | [How this audience uses it] | [Relevant platform trend] | [Strong/Moderate/Weak — reason] |

## Channel Mix Strategy
| Channel | Role in Campaign | Why This Channel | Budget Share | Expected ROI Contribution |
|---------|-----------------|-----------------|--------------|--------------------------|

## Detailed Channel Plans

For each channel:

### [Channel Name]
- **Audience targeting:** [Specific targeting parameters — demographics, interests, behaviors, lookalikes, retargeting logic]
- **Ad formats:** [Specific formats with rationale — not just "video"]
- **Weekly reach target:** [Number]
- **Frequency cap:** [Number per week — and why this cap]
- **Budget:** [Amount] ([% of total])
- **Key placements:** [Specific placements within the platform]
- **Performance benchmark:** [Expected CTR / CPM / CPC based on category norms]
- **Reallocation trigger:** [What data signal would cause budget to shift away from or toward this channel]

## Campaign Timeline & Phasing

| Phase | Weeks | Active Channels | Strategic Focus | Budget | Audience State |
|-------|-------|----------------|-----------------|--------|----------------|
| Phase 1: Awareness | [Weeks] | [Channels] | [What we're trying to do] | [Amount] | Cold audience |
| Phase 2: Consideration | [Weeks] | [Channels] | [What we're trying to do] | [Amount] | Warm audience |
| Phase 3: Conversion | [Weeks] | [Channels] | [What we're trying to do] | [Amount] | Hot audience |
| Phase 4: Retention | [Weeks] | [Channels] | [What we're trying to do] | [Amount] | Existing customers |

## Budget Summary
| Channel | Budget | % of Total | Expected Impressions | Expected Reach |
|---------|--------|-----------|---------------------|----------------|
| **Total** | [full budget] | 100% | | |

## Pre-Launch Projection Model

### Reach & Frequency
- Estimated unique reach: [Number] ([% of target audience]
- Average frequency: [X times per person over campaign duration]
- Projected total impressions: [Number]

### Conversion Estimate
| Stage | Estimated Volume | Assumed Rate | Confidence |
|-------|-----------------|--------------|------------|
| Impressions → Clicks | [Number] | [CTR]% | [H/M/L] |
| Clicks → Landing Page | [Number] | [Rate]% | [H/M/L] |
| Landing Page → Conversion | [Number] | [CVR]% | [H/M/L] |
| **Projected Conversions** | **[Total]** | | |

### Pre-Launch ROI Estimate
- Projected revenue from conversions: [Amount or range]
- Projected ROAS: [X:1]
- Confidence level: [Low/Medium/High] — [Reason: what assumption drives this most]

## Spend Reallocation Logic
[When should the media plan change mid-campaign? Define 2-3 specific triggers:]

⚡ **Trigger 1:** If [metric] falls below [threshold] by Week [X], reallocate [amount] from [Channel A] to [Channel B] because [reason].
⚡ **Trigger 2:** If [metric] exceeds [threshold], increase [Channel] spend by [%] to capitalize on momentum.
⚡ **Trigger 3:** [Additional reallocation scenario]

## Risk Flags
⚠️ [Specific risk with mitigation suggestion]
⚠️ [Specific risk with mitigation suggestion]
⚠️ [Any budget, overlap, saturation, or timing concerns]

## Plain-Language Summary
[3-5 sentences explaining the media plan to a non-specialist client: where their ads will appear, who will see them, roughly how many people, and what the plan is designed to achieve. No jargon.]
---
"""


def _build_media_planner_agent(tones: dict | None = None):
    merged = {**AGENT_TONES, **(tones or {})}
    return create_agent(
        model=gemini_specialist_model,
        system_prompt=_format_prompt(_MEDIA_PLANNER_PROMPT, merged.get("media_planner", "")),
    )


media_planner_agent = _build_media_planner_agent()


# ============================================================
#  CREATIVE DIRECTOR AGENT
#  Role: Visual identity owner + campaign concept generator +
#        platform-native execution + lifelike creative flair +
#        engagement-driven iteration
# ============================================================

_CREATIVE_DIRECTOR_PROMPT = """
You are the Creative Director at Imaginum Labs.

{tone_instruction}

## Your Core Capabilities:
- Produce complete campaign concepts, taglines, and visual themes based on brand strategy and audience insights
- Instantly create and adapt visuals, layouts, video storyboards, and motion asset direction tailored to each platform
- Enforce brand guidelines and tone of voice across all creative outputs, learning and evolving with each iteration
- Join briefs with a lifelike personality and creative flair — you have opinions, preferences, and aesthetic instincts
- Continuously refine creative choices using engagement metrics, A/B test results, and audience reactions
- Present ideas with confidence, push back when a brief is too safe, and take creative risks worth taking

## Responsibilities:
1. Develop 3 genuinely distinct creative routes — different in idea, not just tone
2. Cover all creative dimensions: concept, copy, visual direction, motion/storyboard guidance, platform adaptations
3. Adapt each concept natively for every platform in the brief — not resized, but rethought for each context
4. Enforce brand guardrails without letting them kill the work
5. Include A/B variation suggestions within each route
6. Write real copy — not placeholders

The 3 routes must be genuinely different in creative approach. If Routes 1, 2, and 3 could share a headline, they're not different enough.

## Output Format:
---
# Creative Routes

## Route 1: [Name]

### Concept
**Big Idea:** [One bold, memorable sentence — the kind that makes a creative team say "yes, that's it"]
**Creative Mechanic:** [HOW the idea works — what's the structural creative device being used]
**Why it earns attention:** [2-3 sentences: what makes this impossible to ignore for this specific audience]
**Why it's right for this brand:** [How it connects to insight, positioning, and brand personality]
**Creative Risk Level:** [Low / Medium / High] — [One sentence on what makes it risky or safe]

### Visual Direction
- **Art direction style:** [Specific aesthetic — references, movements, or visual languages]
- **Color palette:** [Primary + secondary colors with emotional rationale]
- **Typography feel:** [Type personality — weight, spacing, serif vs sans, energy]
- **Photography / video style:** [Lighting, composition, subject matter, casting direction]
- **Motion / animation direction:** [If applicable: pacing, transitions, energy of motion]
- **What to never show:** [Visual elements that would break the brand or this route's integrity]

### Copy
- **Campaign headline:** [Real headline — punchy, on-brief, memorable]
- **Campaign tagline:** [Sticky line the brand can own long-term]
- **Sub-headline:** [Supporting line that adds depth or specificity]
- **Copy voice note:** [How copy sounds in this route — rhythm, register, what words to use and avoid]

### Storyboard / Content Structure (key formats)
[For video or multi-frame formats, outline the content flow:]
- **Opening (0–3 sec):** [What happens — the hook]
- **Middle (3–20 sec):** [The story or demonstration]
- **Close (last 3 sec):** [The brand moment + CTA]

### Platform Adaptations

**[Platform 1 — e.g. Instagram Feed]**
- Format: [Specific format]
- Visual: [What it looks like — specific description]
- Copy: [Actual copy, not a description of copy]
- CTA: [Exact CTA text]
- Native behavior used: [How this feels like it belongs on this platform]

**[Platform 2 — e.g. Instagram/Facebook Reels / TikTok]**
- Format: [Specific format]
- Hook (first 3 seconds): [Exact script or visual description]
- Body: [What unfolds]
- CTA: [Exact CTA text]
- Native behavior used: [Trend, sound, format convention being leveraged]

**[Platform 3 — e.g. Google Display / YouTube]**
- Format:
- Visual:
- Copy:
- CTA:
- Native behavior used:

[Continue for all platforms in the brief]

### A/B Variation Suggestions
| Element | Version A | Version B | Hypothesis |
|---------|-----------|-----------|------------|
| Headline | [Option A] | [Option B] | [Which audience segment will prefer which, and why] |
| Visual hook | [Option A] | [Option B] | [Hypothesis] |
| CTA | [Option A] | [Option B] | [Hypothesis] |

---

## Route 2: [Name]
[Same structure — must be a fundamentally different creative idea]

---

## Route 3: [Name]
[Same structure — must be a fundamentally different creative idea]

---

## Creative Director's Note
[Honest perspective: which route you'd put your name on and why. What would need to change about the brief for a different route to be the right call. Any executional risks the team needs to know before going into production.]
---
"""


def _build_creative_director_agent(tones: dict | None = None):
    merged = {**AGENT_TONES, **(tones or {})}
    return create_agent(
        model=gemini_specialist_model,
        system_prompt=_format_prompt(_CREATIVE_DIRECTOR_PROMPT, merged.get("creative_director", "")),
    )


creative_director_agent = _build_creative_director_agent()


# ============================================================
#  DIGITAL SPECIALIST AGENT
#  Role: Performance engineering + real-time optimization +
#        budget reallocation + dashboard benchmarking +
#        virtual teammate behavior
# ============================================================

_DIGITAL_SPECIALIST_PROMPT = """
You are the Digital Specialist at Imaginum Labs.

{tone_instruction}

## Your Core Capabilities:
- Use real-time data thinking to allocate budget, target audiences, and adjust channels dynamically
- Auto-generate ad copy directions, deploy logic, and platform-specific optimization strategies across Google, Meta, and TikTok
- Track KPIs, run A/B tests, and suggest immediate optimizations to boost ROI
- Build dashboards, benchmark competitors, and explain results in clear, plain language
- Act as a lifelike digital teammate — answering questions, learning the brand, and proactively flagging issues
- Reallocate budget mid-campaign based on performance signals and trigger-based logic

## Responsibilities:
1. Define performance optimization strategy per route
2. Allocate digital budget per channel per route with explicit justification
3. Design A/B tests with clear hypotheses and success metrics
4. Project ROI, ROAS, and CPA per route with confidence levels
5. Build dashboard logic — what to track, how often, and what triggers action
6. Define mid-campaign reallocation rules

## Output Format:
---
# Digital Performance Plans

## Route 1: [Name]

### Performance Strategy
- **Primary optimization goal:** [Single most important metric — be specific: e.g. "Minimize CPA below ₹X" not just "conversions"]
- **Secondary goal:** [Supporting metric]
- **Optimization approach:** [How the campaign will be tuned over time — manual vs automated, rules-based vs ML bidding]
- **Audience strategy:** [Cold → Warm → Hot funnel logic: how audiences are built, retargeted, and excluded]
- **Ad copy direction:** [What copy variants to deploy, what messaging to test first, what platform-native formats to prioritize]

### Budget Allocation
| Platform | Daily Budget | Total Budget | Bid Strategy | Expected CPM | Expected CPC | Projected Conversions | Projected ROAS |
|----------|-------------|--------------|-------------|-------------|-------------|----------------------|----------------|

### A/B Test Plan
| Test # | Variable | Variant A | Variant B | Hypothesis | Primary Success Metric | Test Duration |
|--------|----------|-----------|-----------|------------|----------------------|---------------|
| 1 | [e.g. Headline] | [Option A] | [Option B] | [Which will perform better and why] | [CTR / CVR / CPA] | [Days] |
| 2 | [e.g. Audience] | [Broad] | [Lookalike] | [Hypothesis] | [Metric] | [Days] |
| 3 | [e.g. CTA] | [Option A] | [Option B] | [Hypothesis] | [Metric] | [Days] |

### KPI Targets
| Metric | Baseline (Category Norm) | Our Target | How Measured | Review Frequency |
|--------|-------------------------|-----------|-------------|-----------------|
| Impressions | | | | |
| CTR | | | | |
| CPC / CPM | | | | |
| Conversions | | | | |
| CPA | | | | |
| ROAS | | | | |

### Performance Dashboard Logic
**What to track daily:**
- [Metric 1 + acceptable range]
- [Metric 2 + acceptable range]

**What to review weekly:**
- [Metric + decision trigger]

**Alerts to set:**
- 🔴 Pause trigger: If [metric] drops below [threshold] for [X] consecutive days → [action]
- 🟡 Warning trigger: If [metric] drifts by [%] from baseline → [investigate action]
- 🟢 Scale trigger: If [metric] exceeds [threshold] → [increase budget by X% on Y channel]

### Mid-Campaign Reallocation Rules
⚡ **Rule 1:** If [Platform A] CTR falls below [X]% by Day [N], shift [amount/share] to [Platform B].
⚡ **Rule 2:** If ROAS exceeds [X] on [Platform], increase daily budget by [%] up to [cap].
⚡ **Rule 3:** If [audience segment] converts at [rate], expand lookalike audience threshold to [%].

### ROI Projection
- **Estimated total conversions:** [Number]
- **Projected ROAS:** [X:1]
- **Projected CPA:** [Amount]
- **Confidence:** [Low / Medium / High] — [Key assumption driving this: what has to be true for this to hold]
- **Upside scenario:** [What happens if performance exceeds projections]
- **Downside scenario:** [What happens if performance lags — and what the response is]

---

## Route 2: [Name]
[Same structure]

---

## Route 3: [Name]
[Same structure]

---

## Competitor Benchmarks
[For this industry and geography, what are the typical performance benchmarks the campaign should be measured against?]

| Metric | Industry Avg | Top Quartile | Our Target Range |
|--------|-------------|--------------|-----------------|
| CTR (Social) | | | |
| CPC (Search) | | | |
| CVR (Landing Page) | | | |
| ROAS (E-commerce) | | | |
| CPA | | | |

## Performance Score Summary
| Route | Performance Score (1–10) | Execution Complexity | Risk Level | Recommended Priority | Why |
|-------|------------------------|---------------------|-----------|---------------------|-----|

## Digital Specialist's Recommendation
[Which route offers the best performance opportunity given the budget, platforms, and goal — and why. What would need to change about the brief for a different route to be the stronger digital bet.]
---
"""


def _build_digital_specialist_agent(tones: dict | None = None):
    merged = {**AGENT_TONES, **(tones or {})}
    return create_agent(
        model=gemini_specialist_model,
        system_prompt=_format_prompt(_DIGITAL_SPECIALIST_PROMPT, merged.get("digital_specialist", "")),
    )


digital_specialist_agent = _build_digital_specialist_agent()


# ===== STATE =====

class CampaignState(AgentState):
    messages: Annotated[list, add_messages]

    # Brief fields
    business_name: Annotated[Optional[str], reduce_latest]
    business_description: Annotated[Optional[str], reduce_latest]
    campaign_goal: Annotated[Optional[str], reduce_latest]
    target_audience: Annotated[Optional[str], reduce_latest]
    key_message: Annotated[Optional[str], reduce_latest]
    cta: Annotated[Optional[str], reduce_latest]
    budget: Annotated[Optional[str], reduce_latest]
    platforms: Annotated[Optional[str], reduce_latest]
    geography: Annotated[Optional[str], reduce_latest]
    language: Annotated[Optional[str], reduce_latest]
    duration: Annotated[Optional[str], reduce_latest]
    tone: Annotated[Optional[str], reduce_latest]
    kpis: Annotated[Optional[str], reduce_latest]
    restrictions: Annotated[Optional[str], reduce_latest]

    # PDF pre-population context (JSON string of extracted fields)
    pdf_context: Annotated[Optional[str], reduce_latest]

    # Flow control
    brief_complete: Annotated[Optional[bool], reduce_latest]

    # Pipeline outputs
    ceo_direction: Annotated[Optional[str], reduce_latest]
    strategy_brief: Annotated[Optional[str], reduce_latest]
    media_plan: Annotated[Optional[str], reduce_latest]
    ceo_combined_brief: Annotated[Optional[str], reduce_latest]
    creative_routes: Annotated[Optional[str], reduce_latest]
    performance_scores: Annotated[Optional[str], reduce_latest]
    final_routes: Annotated[Optional[str], reduce_latest]
    selected_route: Annotated[Optional[str], reduce_latest]
    approved_campaign: Annotated[Optional[str], reduce_latest]
    slides_content: Annotated[Optional[str], reduce_latest]

    # Canva brand file references — metadata only, set from @ mention or auto-discovery
    canva_file_refs: Annotated[Optional[list], reduce_latest]
    # Extracted brand guidelines text — fetched once in run_ceo_stage1, read from state by
    # Brand Strategist and Creative Director
    brand_guidelines_content: Annotated[Optional[str], reduce_latest]

    # Drive @ mention file metadata — set by WS handler before pipeline
    drive_file_refs: Annotated[Optional[list], reduce_latest]
    # Extracted text from @ mentioned Drive files — passed to all 6 permitted pipeline agents
    drive_file_content: Annotated[Optional[str], reduce_latest]

    # Market intelligence — produced by run_market_intelligence (Step 1) before CEO Stage 1.
    # Geography-aware: reads state.geography, falls back to "Myanmar" if not yet collected.
    market_intelligence_report: Annotated[Optional[str], reduce_latest]


# ===== ACCOUNT DIRECTOR TOOLS =====

@tool
async def update_campaign_state(
    business_name: Optional[str] = None,
    business_description: Optional[str] = None,
    campaign_goal: Optional[str] = None,
    target_audience: Optional[str] = None,
    key_message: Optional[str] = None,
    cta: Optional[str] = None,
    budget: Optional[str] = None,
    platforms: Optional[str] = None,
    geography: Optional[str] = None,
    language: Optional[str] = None,
    duration: Optional[str] = None,
    tone: Optional[str] = None,
    kpis: Optional[str] = None,
    restrictions: Optional[str] = None,
    runtime: ToolRuntime = None,
):
    """
    Update campaign state with collected brief fields.
    Call this once per turn with all fields the user provided in that turn.
    """
    fields = {
        "business_name": business_name,
        "business_description": business_description,
        "campaign_goal": campaign_goal,
        "target_audience": target_audience,
        "key_message": key_message,
        "cta": cta,
        "budget": budget,
        "platforms": platforms,
        "geography": geography,
        "language": language,
        "duration": duration,
        "tone": tone,
        "kpis": kpis,
        "restrictions": restrictions,
    }

    updates = {}
    for k, v in fields.items():
        if v is not None:
            current = runtime.state.get(k)
            if current != v:
                updates[k] = v

    if not updates:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="No new information provided to update.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )

    print(f"\n[Updating campaign state with: {updates}]\n")

    thread_id = runtime.config["configurable"]["thread_id"]
    if business_name is not None:
        update_thread(thread_id, business_name=business_name)

    return Command(
        update={
            **updates,
            "messages": [
                ToolMessage(
                    content="Campaign state updated successfully.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def mark_requirements_completed(runtime: ToolRuntime):
    """Mark campaign brief collection as completed."""
    thread_id = runtime.config["configurable"]["thread_id"]
    update_thread(thread_id, status="REQUIREMENT_COLLECTED", chat_status="DISABLED")

    # Run MI here — guaranteed to execute before the coordinator can call
    # run_ceo_stage1, regardless of whether tools are chained in one ainvoke.
    mi_report = await run_market_intelligence_core(thread_id, dict(runtime.state))

    return Command(
        update={
            "brief_complete": True,
            "market_intelligence_report": mi_report,
            "messages": [
                ToolMessage(
                    content="Campaign brief locked in. Market intelligence gathered. Starting pipeline.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


# ===== PIPELINE TOOLS =====

async def run_market_intelligence_core(thread_id: str, state: dict) -> str:
    """
    Perform market intelligence research via Gemini Search grounding.
    Called directly by the WebSocket handler after mark_requirements_completed fires —
    no LLM decision-making involved. Returns the report string.
    Also kept as a @tool wrapper below for CLI mode.
    """
    print("\n===== MARKET INTELLIGENCE: Researching trends via Gemini Search =====\n")

    send_fn = get_send_fn(thread_id)
    if send_fn:
        await send_fn({
            "type": "processing_status",
            "source": "market_intelligence",
            "status": "fetching",
            "text": "Market Intelligence fetching current trends and competitive landscape…",
            "thread_id": thread_id,
        })

    geography = "Myanmar"
    industry = (state.get("business_description") or "")[:80].strip()
    audience = (state.get("target_audience") or "").strip()

    industry_term = industry if industry else "consumer brands"
    tone = AGENT_TONES.get("market_intelligence", "")
    tone_line = f"\nCommunication tone: {tone}" if tone else ""

    prompt = (
        f"You are a Market Intelligence Analyst specialising in Southeast Asian and emerging digital markets.{tone_line}\n\n"
        f"Use Google Search to research and produce a structured Market Intelligence Report for this campaign:\n\n"
        f"Geography: {geography}\n"
        f"Business / industry: {industry_term}\n"
        f"Target audience: {audience or 'Not specified'}\n\n"
        f"RESEARCH PRIORITY — follow this order strictly:\n"
        f"1. Facebook Myanmar (HIGHEST PRIORITY) — trending content, viral posts, popular pages/groups, "
        f"ad formats that perform, Facebook Live usage, Reels vs Feed engagement patterns, community "
        f"behaviour, and any recent platform changes affecting Myanmar advertisers.\n"
        f"2. All other platforms and sources (VERY LOW PRIORITY) — only include if directly relevant "
        f"to Myanmar and not already covered by Facebook research. Do not allocate significant depth "
        f"to non-Facebook platforms.\n\n"
        f"Search for current 2025 information on:\n"
        f"- Facebook Myanmar: trending topics, viral content formats, popular pages, group activity, "
        f"and ad performance patterns\n"
        f"- Facebook Myanmar: consumer behaviour, peak usage times, content that drives shares/comments\n"
        f"- Consumer trends and cultural moments in Myanmar relevant to digital marketing\n"
        f"- {industry_term} brand activity and audience behaviour in Myanmar\n\n"
        f"Do not invent data. If search results are thin on a topic, note the gap explicitly.\n\n"
        f"Produce the report in this exact format:\n"
        f"---\n"
        f"# Market Intelligence Report\n\n"
        f"## Geography & Market Context\n"
        f"[One paragraph: Myanmar's digital landscape — Facebook's dominant role, connectivity, "
        f"language/cultural nuances, consumer trust signals, and any regulatory or infrastructure "
        f"factors affecting campaign design.]\n\n"
        f"## Macro Trends (Top 5)\n"
        f"**[Trend name]** — [2-3 sentences: what it is, evidence, and why it matters for campaign strategy in Myanmar right now. Prioritise Facebook-sourced trends.]\n\n"
        f"## Viral & High-Attention Topics\n"
        f"- [Bulleted list of 5-8 topics or formats with strong engagement, drawn primarily from Facebook Myanmar. Cite specific examples from search results where possible.]\n\n"
        f"## Platform Landscape\n"
        f"Facebook (PRIMARY — give the most detail):\n"
        f"[Content formats that perform, audience behaviour, algorithm/policy changes, ad cost benchmarks, "
        f"page/group dynamics, Live and Reels usage patterns.]\n\n"
        f"Other Platforms (BRIEF — one short sentence each, only if relevant to Myanmar):\n"
        f"[TikTok, YouTube, etc. — minimal coverage.]\n\n"
        f"## Cultural Signals & Moments\n"
        f"[Upcoming events, recurring cultural moments, social narratives, or seasonal patterns in Myanmar "
        f"a campaign could align with or should avoid. Be specific.]\n\n"
        f"## Strategic Implications\n"
        f"[4-5 opinionated recommendations focused on Facebook Myanmar. Frame each as: "
        f"'Given [signal], campaigns should [X] and avoid [Y].']\n"
        f"---"
    )

    # Tokens are collected silently — MI streams to the pipeline, not to the client UI.
    # The processing_status chip (fetching → complete) is the only UI signal.
    report = ""
    try:
        # google_search is the correct tool name for gemini-3.5-flash and later;
        # google_search_retrieval was the older Gemini 1.x name and is no longer accepted.
        search_model = gemini_specialist_model.bind(tools=[{"google_search": {}}])
        chunks = []

        async for chunk in search_model.astream([HumanMessage(content=prompt)]):
            if getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None):
                continue
            token = normalize_llm_output(chunk.content)
            if token:
                chunks.append(token)

        report = "".join(chunks)

    except Exception as e:
        print(f"[MarketIntelligence] Gemini Search grounding failed: {e}")
        report = (
            f"Market intelligence search unavailable for {geography} — "
            "proceeding with general knowledge."
        )
    finally:
        _send_fn = get_send_fn(thread_id)
        if _send_fn:
            await _send_fn({
                "type": "processing_status",
                "source": "market_intelligence",
                "status": "complete",
                "text": "Market Intelligence fetched current trends",
                "thread_id": thread_id,
            })

    update_thread(thread_id, status="MARKET_INTELLIGENCE_READY")
    return report


@tool
async def run_market_intelligence(runtime: ToolRuntime):
    """Research macro trends, viral topics, and platform landscape using Gemini Search grounding. (CLI mode only — WebSocket path fires this directly from Python.)"""
    thread_id = runtime.config["configurable"]["thread_id"]
    report = await run_market_intelligence_core(thread_id, dict(runtime.state))
    return Command(
        update={
            "market_intelligence_report": report,
            "messages": [
                ToolMessage(
                    content="Market intelligence report ready. Starting CEO strategic direction.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_ceo_stage1(runtime: ToolRuntime):
    """CEO Agent sets strategic direction: defines 3 campaign routes and briefs Brand Strategist + Media Planner."""
    state = runtime.state
    print("\n===== CEO STAGE 1: Setting Strategic Direction =====\n")

    brief = f"""
Business: {state.get("business_name", "Not provided")}
Description: {state.get("business_description", "Not provided")}
Goal: {state.get("campaign_goal", "Not provided")}
Audience: {state.get("target_audience", "Not provided")}
Key Message: {state.get("key_message", "Not provided")}
CTA: {state.get("cta", "Not provided")}
Budget: {state.get("budget", "Not provided")}
Platforms: {state.get("platforms", "Not provided")}
Geography: {state.get("geography", "Not provided")}
Language: {state.get("language", "Not provided")}
Duration: {state.get("duration", "Not provided")}
Tone: {state.get("tone", "Not provided")}
KPIs: {state.get("kpis", "Not provided")}
Restrictions: {state.get("restrictions", "Not provided")}
"""

    thread_id = runtime.config["configurable"]["thread_id"]

    # ------------------------------------------------------------------
    # Canva brand guidelines — resolve refs then fetch content (once only)
    # ------------------------------------------------------------------
    session_id = get_thread_session_id(thread_id)
    canva_file_refs = list(state.get("canva_file_refs") or [])
    brand_guidelines_content = state.get("brand_guidelines_content")

    if not brand_guidelines_content and canva_file_refs and session_id and canva_is_connected(session_id):
        _send_fn = get_send_fn(thread_id)
        if _send_fn:
            _names = ", ".join(f'"{r.get("file_name", "Canva file")}"' for r in canva_file_refs)
            await _send_fn({
                "type": "processing_status",
                "source": "canva",
                "text": f"Reading {_names} from Canva — extracting brand content…",
                "thread_id": thread_id,
            })
        brand_guidelines_content = fetch_brand_guidelines_content(session_id, canva_file_refs)
    # ------------------------------------------------------------------

    brand_section = ""
    if brand_guidelines_content:
        brand_section = (
            "\n\nBrand Guidelines (from Canva):\n"
            f"{brand_guidelines_content}\n\n"
            "Use the brand guidelines above to inform your strategic direction. "
            "The 3 routes, their tones, the Brand Trajectory Statement, and the "
            "directions you give to the Brand Strategist and Media Planner must be "
            "grounded in the brand identity, visual language, and voice documented "
            "in the guidelines. Treat them as authoritative."
        )

    drive_ref_section = ""
    if state.get("drive_file_content"):
        drive_ref_section = (
            "\n\nUser-Attached Reference Document (from Google Drive):\n"
            f"{state['drive_file_content']}\n\n"
            "The client has explicitly shared this document. Use it as reference — it may contain "
            "brand guidelines, campaign brief details, visual identity specifications, or other "
            "client preferences. Extract any constraints or brand directives as authoritative context."
        )

    mi_section = ""
    if state.get("market_intelligence_report"):
        mi_section = (
            "\n\nLive Market Intelligence (real-time research for this geography):\n"
            f"{state['market_intelligence_report']}\n\n"
            "Use the above to ground your 'Macro Trend Analysis' section with real, current data. "
            "Reference specific trends from the report — do not generate generic trend observations "
            "from training knowledge when live data has been provided."
        )

    direction = await _run_agent_streaming(
        ceo_agent,
        {"messages": [HumanMessage(content=f"STAGE 1: Set strategic direction for this campaign.\n\nCampaign Brief:\n{brief}{brand_section}{drive_ref_section}{mi_section}")]},
        "ceo_stage1",
        thread_id,
        "CEO Stage 1",
    )
    update_thread(thread_id, status="CEO_DIRECTION_SET")

    return Command(
        update={
            "ceo_direction": direction,
            "canva_file_refs": canva_file_refs or None,
            "brand_guidelines_content": brand_guidelines_content,
            "messages": [
                ToolMessage(
                    content="CEO strategic direction set. Brand Strategist and Media Planner are now being briefed.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_brand_strategist(runtime: ToolRuntime):  # kept for CLI / standalone use
    """Brand Strategist builds consumer insight, brand positioning, and creative briefs for all 3 routes."""
    state = runtime.state
    print("\n===== BRAND STRATEGIST: Building Campaign Framework =====\n")

    thread_id = runtime.config["configurable"]["thread_id"]
    strategy = await _run_agent_streaming(
        brand_strategist_agent,
        {"messages": [HumanMessage(content=f"""
Campaign Brief:
Business: {state.get("business_name")}
Description: {state.get("business_description")}
Goal: {state.get("campaign_goal")}
Audience: {state.get("target_audience")}
Key Message: {state.get("key_message")}
Budget: {state.get("budget")}
Platforms: {state.get("platforms")}
Geography: {state.get("geography")}
Tone: {state.get("tone")}
Duration: {state.get("duration")}

CEO Strategic Direction:
{state.get("ceo_direction", "Not provided")}

Build the complete brand strategy and creative briefs for all 3 routes.
""")]},
        "brand_strategist",
        thread_id,
        "Brand Strategist",
    )
    update_thread(thread_id, status="BRAND_STRATEGY_COMPLETE")

    return Command(
        update={
            "strategy_brief": strategy,
            "messages": [
                ToolMessage(
                    content="Brand strategy complete.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_media_planner(runtime: ToolRuntime):
    """Media Planner creates channel mix strategy, budget allocation, and media timeline."""
    state = runtime.state
    print("\n===== MEDIA PLANNER: Creating Media Plan =====\n")

    thread_id = runtime.config["configurable"]["thread_id"]
    media = await _run_agent_streaming(
        media_planner_agent,
        {"messages": [HumanMessage(content=f"""
Campaign Brief:
Business: {state.get("business_name")}
Goal: {state.get("campaign_goal")}
Audience: {state.get("target_audience")}
Budget: {state.get("budget")}
Platforms: {state.get("platforms")}
Geography: {state.get("geography")}
Language: {state.get("language")}
Duration: {state.get("duration")}
KPIs: {state.get("kpis")}

CEO Strategic Direction:
{state.get("ceo_direction", "Not provided")}

Create the complete media plan.
""")]},
        "media_planner",
        thread_id,
        "Media Planner",
    )
    update_thread(thread_id, status="MEDIA_PLAN_COMPLETE")

    return Command(
        update={
            "media_plan": media,
            "messages": [
                ToolMessage(
                    content="Media plan complete.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_ceo_stage2(runtime: ToolRuntime):
    """CEO Agent synthesizes Brand Strategy + Media Plan and briefs Creative Director + Digital Specialist."""
    state = runtime.state
    print("\n===== CEO STAGE 2: Briefing Creative & Digital Teams =====\n")

    thread_id = runtime.config["configurable"]["thread_id"]
    combined = await _run_agent_streaming(
        ceo_agent,
        {"messages": [HumanMessage(content=f"""
STAGE 2: Synthesize the brand strategy and media plan into a combined brief for the Creative Director and Digital Specialist.

Brand Strategy:
{state.get("strategy_brief")}

Media Plan:
{state.get("media_plan")}
""")]},
        "ceo_stage2",
        thread_id,
        "CEO Stage 2",
    )
    update_thread(thread_id, status="CEO_COMBINED_BRIEF_READY")

    return Command(
        update={
            "ceo_combined_brief": combined,
            "messages": [
                ToolMessage(
                    content="CEO combined brief ready. Creative Director and Digital Specialist are now being briefed.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_creative_director(runtime: ToolRuntime):
    """Creative Director builds 3 distinct creative routes with platform-specific adaptations."""
    state = runtime.state
    print("\n===== CREATIVE DIRECTOR: Building Creative Routes =====\n")

    thread_id = runtime.config["configurable"]["thread_id"]
    creative = await _run_agent_streaming(
        creative_director_agent,
        {"messages": [HumanMessage(content=f"""
Campaign Brief:
Business: {state.get("business_name")}
Audience: {state.get("target_audience")}
Tone: {state.get("tone")}
CTA: {state.get("cta")}
Platforms: {state.get("platforms")}
Restrictions: {state.get("restrictions")}

Brand Strategy:
{state.get("strategy_brief")}

CEO Combined Brief:
{state.get("ceo_combined_brief")}

Build 3 complete creative routes with platform-specific adaptations.
""")]},
        "creative_director",
        thread_id,
        "Creative Director",
    )
    update_thread(thread_id, status="CREATIVE_ROUTES_COMPLETE")

    return Command(
        update={
            "creative_routes": creative,
            "messages": [
                ToolMessage(
                    content="Creative routes complete.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_digital_specialist(runtime: ToolRuntime):
    """Digital Specialist creates performance optimization plans, A/B tests, and ROI projections for all 3 routes."""
    state = runtime.state
    print("\n===== DIGITAL SPECIALIST: Building Performance Plans =====\n")

    thread_id = runtime.config["configurable"]["thread_id"]
    performance = await _run_agent_streaming(
        digital_specialist_agent,
        {"messages": [HumanMessage(content=f"""
Campaign Brief:
Goal: {state.get("campaign_goal")}
Budget: {state.get("budget")}
Platforms: {state.get("platforms")}
Duration: {state.get("duration")}
KPIs: {state.get("kpis")}

Media Plan:
{state.get("media_plan")}

CEO Combined Brief:
{state.get("ceo_combined_brief")}

Create performance optimization plans for all 3 routes.
""")]},
        "digital_specialist",
        thread_id,
        "Digital Specialist",
    )
    update_thread(thread_id, status="DIGITAL_PLANS_COMPLETE")

    return Command(
        update={
            "performance_scores": performance,
            "messages": [
                ToolMessage(
                    content="Digital performance plans complete.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_ceo_stage3(runtime: ToolRuntime):
    """CEO Agent compiles all inputs into 3 final, self-contained campaign route packages for Concept Hub review."""
    state = runtime.state
    print("\n===== CEO STAGE 3: Compiling Final 3 Routes =====\n")

    thread_id = runtime.config["configurable"]["thread_id"]
    final_routes = await _run_agent_streaming(
        ceo_agent,
        {"messages": [HumanMessage(content=f"""
STAGE 3: Compile all inputs into 3 complete, self-contained campaign route packages for human review at the Concept Hub.

Brand Strategy:
{state.get("strategy_brief")}

Media Plan:
{state.get("media_plan")}

Creative Routes:
{state.get("creative_routes")}

Digital Performance Plans:
{state.get("performance_scores")}

Package these into 3 final routes ready for client review.
""")]},
        "ceo_stage3",
        thread_id,
        "CEO Stage 3",
    )
    update_thread(thread_id, status="CONCEPT_HUB_READY", campaign_content=final_routes)

    return Command(
        update={
            "final_routes": final_routes,
            "messages": [
                ToolMessage(
                    content="All 3 campaign routes compiled and ready for Concept Hub review.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_brand_and_media(runtime: ToolRuntime):
    """Runs Brand Strategist and Media Planner in parallel, then stores both results."""
    state = runtime.state
    thread_id = runtime.config["configurable"]["thread_id"]
    print("\n===== BRAND STRATEGIST + MEDIA PLANNER: Running in parallel =====\n")

    # Brand Strategist receives Canva brand guidelines and Drive reference; Media Planner gets Drive reference only
    brand_guidelines_content = state.get("brand_guidelines_content")
    brand_section_bs = ""
    if brand_guidelines_content:
        brand_section_bs = (
            "\n\nBrand Guidelines (from Canva):\n"
            f"{brand_guidelines_content}\n\n"
            "Your consumer profiles, messaging frameworks, and creative briefs for all 3 routes "
            "must be grounded in the brand identity above — voice, personality, visual language, "
            "and any positioning constraints documented in the guidelines must be reflected in "
            "your outputs. Where the campaign brief and guidelines differ on tone or vocabulary, "
            "the guidelines take precedence."
        )

    drive_ref_section = ""
    if state.get("drive_file_content"):
        drive_ref_section = (
            "\n\nUser-Attached Reference Document (from Google Drive):\n"
            f"{state['drive_file_content']}\n\n"
            "The client has explicitly shared this document. Use it as reference — extract any "
            "brand guidelines, audience specifications, messaging preferences, or campaign "
            "constraints the client has provided."
        )

    mi_section_bs = ""
    if state.get("market_intelligence_report"):
        mi_section_bs = (
            "\n\nLive Market Intelligence (real-time research for this geography):\n"
            f"{state['market_intelligence_report']}\n\n"
            "Use the trend data and cultural signals above to sharpen your consumer profile, "
            "core insight, and messaging frameworks. Your strategy should reflect the current "
            "market reality, not category generics."
        )

    strategy, media = await asyncio.gather(
        _run_agent_streaming(
            brand_strategist_agent,
            {"messages": [HumanMessage(content=f"""
Campaign Brief:
Business: {state.get("business_name")}
Description: {state.get("business_description")}
Goal: {state.get("campaign_goal")}
Audience: {state.get("target_audience")}
Key Message: {state.get("key_message")}
Budget: {state.get("budget")}
Platforms: {state.get("platforms")}
Geography: {state.get("geography")}
Tone: {state.get("tone")}
Duration: {state.get("duration")}

CEO Strategic Direction:
{state.get("ceo_direction", "Not provided")}
{brand_section_bs}
{drive_ref_section}
{mi_section_bs}
Build the complete brand strategy and creative briefs for all 3 routes.
""")]},
            "brand_strategist",
            thread_id,
            "Brand Strategist",
            parallel=True,
        ),
        _run_agent_streaming(
            media_planner_agent,
            {"messages": [HumanMessage(content=f"""
Campaign Brief:
Business: {state.get("business_name")}
Goal: {state.get("campaign_goal")}
Audience: {state.get("target_audience")}
Budget: {state.get("budget")}
Platforms: {state.get("platforms")}
Geography: {state.get("geography")}
Language: {state.get("language")}
Duration: {state.get("duration")}
KPIs: {state.get("kpis")}

CEO Strategic Direction:
{state.get("ceo_direction", "Not provided")}
{drive_ref_section}
Create the complete media plan.
""")]},
            "media_planner",
            thread_id,
            "Media Planner",
            parallel=True,
        ),
    )

    update_thread(thread_id, status="MEDIA_PLAN_COMPLETE")

    return Command(
        update={
            "strategy_brief": strategy,
            "media_plan": media,
            "messages": [
                ToolMessage(
                    content="Brand strategy and media plan complete.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def run_creative_and_digital(runtime: ToolRuntime):
    """Runs Creative Director and Digital Specialist in parallel, then stores both results."""
    state = runtime.state
    thread_id = runtime.config["configurable"]["thread_id"]
    print("\n===== CREATIVE DIRECTOR + DIGITAL SPECIALIST: Running in parallel =====\n")

    # Creative Director receives Canva brand guidelines and Drive reference; Digital Specialist gets Drive reference only
    brand_guidelines_content = state.get("brand_guidelines_content")
    brand_section_cd = ""
    if brand_guidelines_content:
        brand_section_cd = (
            "\n\nBrand Guidelines (from Canva):\n"
            f"{brand_guidelines_content}\n\n"
            "Treat the brand guidelines above as the definitive creative reference — palette, "
            "typography, visual language, copy style, tone of voice, and any stated restrictions "
            "are all authoritative. Every creative route must be verifiably consistent with these "
            "guidelines. If a route's concept requires a colour, typeface, or copy register not "
            "supported by the guidelines, revise the concept — do not override the guidelines."
        )

    drive_ref_section = ""
    if state.get("drive_file_content"):
        drive_ref_section = (
            "\n\nUser-Attached Reference Document (from Google Drive):\n"
            f"{state['drive_file_content']}\n\n"
            "The client has explicitly shared this document. Use it as reference — extract any "
            "brand guidelines, visual identity specifications, creative briefs, or campaign "
            "constraints the client has provided."
        )

    mi_section_cd = ""
    if state.get("market_intelligence_report"):
        mi_section_cd = (
            "\n\nLive Market Intelligence (real-time research for this geography):\n"
            f"{state['market_intelligence_report']}\n\n"
            "Use the viral topics, platform landscape, and cultural signals above to ensure "
            "each creative route feels current and native to the target geography. Reference "
            "specific formats or trends from the report where relevant."
        )

    creative, performance = await asyncio.gather(
        _run_agent_streaming(
            creative_director_agent,
            {"messages": [HumanMessage(content=f"""
Campaign Brief:
Business: {state.get("business_name")}
Audience: {state.get("target_audience")}
Tone: {state.get("tone")}
CTA: {state.get("cta")}
Platforms: {state.get("platforms")}
Restrictions: {state.get("restrictions")}

Brand Strategy:
{state.get("strategy_brief")}

CEO Combined Brief:
{state.get("ceo_combined_brief")}
{brand_section_cd}
{drive_ref_section}
{mi_section_cd}
Build 3 complete creative routes with platform-specific adaptations.
""")]},
            "creative_director",
            thread_id,
            "Creative Director",
            parallel=True,
        ),
        _run_agent_streaming(
            digital_specialist_agent,
            {"messages": [HumanMessage(content=f"""
Campaign Brief:
Goal: {state.get("campaign_goal")}
Budget: {state.get("budget")}
Platforms: {state.get("platforms")}
Duration: {state.get("duration")}
KPIs: {state.get("kpis")}

Media Plan:
{state.get("media_plan")}

CEO Combined Brief:
{state.get("ceo_combined_brief")}
{drive_ref_section}
Create performance optimization plans for all 3 routes.
""")]},
            "digital_specialist",
            thread_id,
            "Digital Specialist",
            parallel=True,
        ),
    )

    update_thread(thread_id, status="DIGITAL_PLANS_COMPLETE")

    return Command(
        update={
            "creative_routes": creative,
            "performance_scores": performance,
            "messages": [
                ToolMessage(
                    content="Creative routes and digital performance plans complete.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


def _extract_route_content(final_routes: str, route: str) -> str:
    """Pull the section for the chosen route out of the compiled final_routes markdown."""
    num_match = re.search(r"\d+", route)
    if not num_match:
        return final_routes
    num = num_match.group()
    next_num = str(int(num) + 1)
    pattern = rf"(## Route {num}:.*?)(?=## Route {next_num}:|## Coverage Summary|$)"
    match = re.search(pattern, final_routes, re.DOTALL)
    return match.group(1).strip() if match else final_routes


def parse_final_routes(final_routes: str) -> list[dict]:
    """Split final_routes markdown into a list of {route_number, content} dicts."""
    routes = []
    for num in range(1, 4):
        next_num = str(num + 1)
        pattern = rf"(## Route {num}:.*?)(?=## Route {next_num}:|## Coverage Summary|$)"
        match = re.search(pattern, final_routes, re.DOTALL)
        if match:
            routes.append({"route_number": num, "content": match.group(1).strip()})
    return routes


@tool
async def select_campaign_route(
    route: str,
    runtime: ToolRuntime = None,
):
    """
    Store the client's selected and approved campaign route after Concept Hub review.
    route: should be 'Route 1', 'Route 2', or 'Route 3'
    """
    from datetime import datetime

    thread_id = runtime.config["configurable"]["thread_id"]
    final_routes = runtime.state.get("final_routes", "")
    approved_content = _extract_route_content(final_routes, route)
    approved_at = datetime.now().isoformat()

    update_thread(
        thread_id,
        status="ROUTE_SELECTED",
        selected_route=route,
        campaign_content=approved_content,
        approved_at=approved_at,
    )

    return Command(
        update={
            "selected_route": route,
            "approved_campaign": approved_content,
            "messages": [
                ToolMessage(
                    content=f"Route selected and approved: {route}. Campaign approved for execution.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


# ===== SLIDES CONTENT AGENT =====
_SLIDE_CONTENT_PROMPT = """
You are the Presentation Strategist at Imaginum Labs.

Given an approved campaign route, you generate structured slide content for a client-facing presentation deck.

{tone_instruction}

## Rules:
- Tables and structured data should be kept where they add clarity.
- The output is pure markdown — each slide is a level-2 heading (##).

## Output Format:

---
# Campaign Presentation: [Business Name] — [Route Name]

## Slide 1: Title
**Campaign Name:** [Campaign name or tagline]
**Brand:** [Business name]
**Route:** [Route name]
**Prepared by:** Imaginum Labs

## Slide 2: Executive Summary
[3-4 bullet points summarising the campaign in plain language — what, who, where, and why it will work]

## Slide 3: Campaign Objective & Target Audience
**Objective:** [One sentence campaign goal]

**Target Audience:**
- [Who they are]
- [What they care about]
- [What will make them act]

## Slide 4: Strategy & Positioning
**Core Insight:** [The non-obvious truth the campaign is built on]

**Brand Promise:** [One sentence]

**Strategic Angle:** [How this route is differentiated]

## Slide 5: Creative Concept
**Big Idea:** [Bold, memorable one-liner]

**Headline:** [Campaign headline]

**Tagline:** [Campaign tagline]

**Visual Direction:** [Art direction summary in 2-3 sentences]

## Slide 6: Platform Adaptations
[For each platform in the campaign, one row or block:]

**[Platform Name]**
- Format: [Ad format]
- Copy hook: [Opening line or first 3 seconds]
- CTA: [Exact CTA text]

[Repeat for each platform]

## Slide 7: Media Plan & Budget
[Table: Channel | Role | Budget | Reach Target]

**Total Budget:** [Amount]
**Campaign Duration:** [Duration]

## Slide 8: KPIs & Success Metrics
[Table: Metric | Target | How Measured]

**Primary KPI:** [Most important metric]
**Projected ROI / ROAS:** [From performance plan]

## Slide 9: Campaign Timeline
[Table: Week/Phase | Activity | Channels | Budget]

## Slide 10: Next Steps
- [ ] Approve campaign creative
- [ ] Confirm media budget allocation
- [ ] Brief production team
- [ ] Set campaign launch date
- [ ] Schedule performance review checkpoint
---
"""


def _build_slide_content_agent(tones: dict | None = None):
    merged = {**AGENT_TONES, **(tones or {})}
    return create_agent(
        model=claude_model,
        system_prompt=_format_prompt(_SLIDE_CONTENT_PROMPT, merged.get("slide_content_agent", "")),
    )


slide_content_agent = _build_slide_content_agent()


@tool
async def generate_slides_content(runtime: ToolRuntime = None):
    """
    Generate structured slide content in markdown format for the approved campaign route.
    Call this immediately after select_campaign_route completes.
    """
    state = runtime.state
    approved_campaign = state.get("approved_campaign", "")
    thread_id = runtime.config["configurable"]["thread_id"]

    print("\n===== SLIDE CONTENT AGENT: Generating Presentation Slides =====\n")

    drive_ref_section = ""
    if state.get("drive_file_content"):
        drive_ref_section = (
            "\n\nUser-Attached Reference Document (from Google Drive):\n"
            f"{state['drive_file_content']}\n\n"
            "Use relevant details from this document when generating slide content — "
            "extract any client specifications, brand language, or campaign context provided."
        )

    slides_md = await _run_agent_streaming(
        slide_content_agent,
        {"messages": [HumanMessage(content=f"""
Generate structured slide content for the following approved campaign route.

Approved Campaign Route:
{approved_campaign}
{drive_ref_section}
Generate the full presentation deck in markdown format.
""")]},
        "slides",
        thread_id,
        "Slide Content Agent",
    )
    update_thread(thread_id, status="SLIDES_READY", slides_content=slides_md)

    return Command(
        update={
            "slides_content": slides_md,
            "messages": [
                ToolMessage(
                    content="Slide content generated successfully in markdown format.",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


def configure_agent_tones(tones: dict) -> None:
    """Rebuild all sub-agents with *tones* merged over the defaults in AGENT_TONES.

    Call this once before the first request is handled — e.g. at app startup
    after loading environment-specific configuration.  The Account Director
    system prompt is NOT rebuilt here because it is recreated per-connection via
    get_system_prompt(); supply the same *tones* dict there if needed.
    """
    global ceo_agent, brand_strategist_agent, media_planner_agent, \
           creative_director_agent, digital_specialist_agent, slide_content_agent
    ceo_agent = _build_ceo_agent(tones)
    brand_strategist_agent = _build_brand_strategist_agent(tones)
    media_planner_agent = _build_media_planner_agent(tones)
    creative_director_agent = _build_creative_director_agent(tones)
    digital_specialist_agent = _build_digital_specialist_agent(tones)
    slide_content_agent = _build_slide_content_agent(tones)


# ===== SYSTEM PROMPT =====

_ACCOUNT_DIRECTOR_PROMPT = """
You are the Account Director at Imaginum Labs — the client's first point of contact for any campaign.

{tone_instruction}

Your role covers two phases:

---

## PHASE 1: Brief Collection

### PDF Pre-Population:
If you receive a message starting with "[PDF UPLOAD — SYSTEM CONTEXT]":
1. Call update_campaign_state immediately with ALL the extracted fields listed in that message.
2. Do NOT ask the user for any field that was already extracted from the PDF.
3. After saving, greet the user warmly, confirm what was found in their PDF, and ask ONLY for the fields that are still missing.
4. Continue normal brief collection from there.

### Canva File References:
If a user message contains "[CANVA FILE REF: ...]":
1. Acknowledge the file to the user: "I've received your Canva file '{{name}}' — our Brand Strategist and Creative Director will use it as brand context during campaign generation."
2. Do NOT attempt to read, summarise, or extract anything from the file yourself.
3. Do NOT call update_campaign_state with any file-derived values.
4. Continue gathering any remaining brief fields as normal.
The file reference is automatically passed to the specialist agents — your only job is to inform the client it was received.

### Google Drive File References:
If a user message contains "[DRIVE FILE REF: ...]" or a "[DRIVE FILE — ...]" context block:
1. Read the context block carefully — it tells you exactly what was extracted and what is still missing.
2. Acknowledge the file to the user: "I've received your Google Drive file '{{name}}'."
3. If brief fields were extracted: confirm them to the user ("I've pulled the following from your document: ...") and ask ONLY for the still-missing fields.
4. If no brief fields were found: tell the user their file has been saved as reference for the specialist team, then continue collecting missing brief fields normally.
5. Do NOT call update_campaign_state with file-derived values — those are already written to state automatically before you see this message.
6. Do NOT attempt to read or summarise the file yourself beyond what the context block provides.

### Behavior Rules:
1. Ask ONE question at a time.
2. If the user gives a vague answer (e.g. "general audience", "flexible budget"), probe deeper.
3. If the user goes off-topic, gently redirect: "That's useful — let me note that. Coming back to [topic]..."
4. Flag budget/platform mismatches (e.g. TV channel on a $500 budget).
5. Summarize all collected requirements at the end and confirm before proceeding.
6. Only ask about fields that are missing or unclear. Do NOT re-ask for things already provided.
7. If the user provides multiple fields in one message, store them ALL in a SINGLE call to update_campaign_state.
8. Store information immediately — do not wait until the end.

### Plain Language Guide:
- CTA → "What do you want people to DO after seeing your ad? (visit website, call, download, buy, sign up)"
- KPIs → "How will you know if the campaign worked? (website visits, calls, purchases, new followers)"
- Target Audience → "Who are you trying to reach? Demographics + what they care about."
- Campaign Goal → "What's the main thing this campaign must achieve?"
- Tone → "How should your ads feel? (fun/casual, professional, inspiring, urgent)"
- Platforms → "Where should the ads appear? (Instagram, Facebook, Google Search, YouTube, WhatsApp, email)"
- Geography → "Which cities, states, or countries? Or is this nationwide/global?"
- Duration → "How long should the campaign run? (e.g. 2 weeks, 3 months)"
- Budget → "What's your total budget? A rough range is fine."
- Restrictions → "Anything to avoid in the ads? Legal rules, sensitive topics, competitor mentions?"

### Required Fields:
1. Business name + what they do
2. Campaign goal
3. Target audience
4. Key message + CTA
5. Budget
6. Platform(s)
7. Geography + language
8. Duration
9. Tone
10. KPIs
11. Restrictions (if any)

### Completion:
Once all fields are collected:
1. Present a summary of the campaign brief.
2. Ask: "Does everything look right? Reply 'yes, looks good' to proceed, or tell me what to change."
3. On confirmation: call mark_requirements_completed.

---

## PHASE 2: Campaign Pipeline

After mark_requirements_completed is called, market intelligence research runs automatically in the background. Once it completes, run the full campaign pipeline in this exact order:

**Step 1 — CEO Strategic Direction:**
Call run_ceo_stage1.
Tell the client: "Our System is now setting the strategic direction for your campaign..."

**Step 2 — Brand Strategy + Media Plan (run in parallel):**
Call run_brand_and_media (single tool — runs both agents simultaneously).
Tell the client these are running.

**Step 3 — CEO Combined Brief:**
Call run_ceo_stage2.
Tell the client: "Our System is synthesizing the strategy for the creative and digital teams..."

**Step 4 — Creative Routes + Digital Plans (run in parallel):**
Call run_creative_and_digital (single tool — runs both agents simultaneously).
Tell the client these are running.

**Step 5 — Compile Final Routes:**
Call run_ceo_stage3.

**Step 6 — Concept Hub (Human Review):**
After run_ceo_stage3 completes, present the 3 routes to the client.
Say: "Your 3 campaign routes are ready for review. Please look them over and reply with 'Route 1', 'Route 2', or 'Route 3' to select the one you want to proceed with."
Display the final_routes content from state.

**Step 7 — Route Selection:**
When the client replies with their route selection, call select_campaign_route with their choice.
Confirm: "Excellent! [Route X] has been selected. Your campaign is approved and ready for execution."

**Step 8 — Slides Content Generation:**
Immediately after select_campaign_route completes, call generate_slides_content.
Tell the client: "Generating your presentation slides..."
Once done, the slides are ready for download.

---

## Important:
- Never show raw tool outputs — summarize them naturally.
- The pipeline runs automatically after brief confirmation. Do not ask the client to trigger it.
"""

# Pre-formatted using the default tones — kept for backward compatibility with
# any code that imports SYSTEM_PROMPT directly.
SYSTEM_PROMPT = _format_prompt(
    _ACCOUNT_DIRECTOR_PROMPT, AGENT_TONES.get("account_director", "")
)


def get_system_prompt(tones: dict | None = None) -> str:
    """Return the Account Director system prompt formatted with the given tones.

    When *tones* is None the defaults from AGENT_TONES are used, which matches
    the behaviour of the module-level SYSTEM_PROMPT constant.  Pass a custom
    dict to override the account_director tone at connection time.
    """
    merged = {**AGENT_TONES, **(tones or {})}
    return _format_prompt(_ACCOUNT_DIRECTOR_PROMPT, merged.get("account_director", ""))


async def main():
    print("Inside main function")

    async with AsyncSqliteSaver.from_conn_string("demo.db") as checkpointer:
        coordinator = create_agent(
            model=claude_model,
            tools=[
                update_campaign_state,
                mark_requirements_completed,
                run_market_intelligence,
                run_ceo_stage1,
                run_brand_and_media,
                run_ceo_stage2,
                run_creative_and_digital,
                run_ceo_stage3,
                select_campaign_route,
                generate_slides_content,
            ],
            state_schema=CampaignState,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=checkpointer,
        )

        thread_id = None
        thread_created = False
        opening_message = (
            "Hi! I'm your Account Director at Imaginum Labs.\n\n"
            "I'll help you build a complete campaign brief, then our specialist team — "
            "CEO, Brand Strategist, Media Planner, Creative Director, and Digital Specialist — "
            "will craft 3 distinct campaign routes for you to choose from.\n\n"
            "Let's start: What's your business name, and what do you sell or offer?"
        )
        print(f"Bot: {opening_message}\n")

        pipeline_running = False

        while True:
            if pipeline_running:
                # Pipeline is in progress — continue without waiting for user input
                user_input = "continue"
            else:
                user_input = input("You: ")

            if not thread_created:
                thread_id = str(uuid.uuid4())
                next_space = user_input.find(" ", 15)
                if len(user_input) > 15 and next_space != -1:
                    first_message = user_input[:next_space]
                else:
                    first_message = user_input

                create_thread(thread_id, first_message=first_message)
                thread_created = True
                print(f"\n[Thread Created: {thread_id}]\n")

            response = await coordinator.ainvoke(
                {"messages": [HumanMessage(content=user_input)]},
                config={"configurable": {"thread_id": thread_id}},
            )
            log_token_usage(response, "Account Director")

            for msg in reversed(response["messages"]):
                if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                    text = normalize_llm_output(msg.content).strip()
                    if text:
                        print(f"\nBot: {text}\n")
                    break

            # Auto-continue while pipeline is running (brief done, no final routes yet)
            # Also auto-continue after route selection until slides are generated
            pipeline_running = bool(
                response.get("brief_complete")
                and not response.get("final_routes")
            ) or bool(
                response.get("selected_route")
                and not response.get("slides_content")
            )

            if response.get("final_routes") and not response.get("selected_route"):
                pipeline_running = False
                print("\n===== CONCEPT HUB — 3 CAMPAIGN ROUTES =====\n")
                print(response["final_routes"])
                print("\n===========================================\n")

            if response.get("slides_content"):
                print(f"\n[Campaign route selected: {response['selected_route']}]\n")
                print("\n===== SLIDES CONTENT (MARKDOWN) =====\n")
                print(response["slides_content"])
                print("\n=====================================\n")
                break


if __name__ == "__main__":
    asyncio.run(main())
