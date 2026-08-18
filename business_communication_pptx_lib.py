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
#  File: \Downloads\backend\business_communication.py
#  Project: ps1
#  Created Date: Thursday, March 12th 2026, 4:27:50 pm
#  Author: Naveena J <naveena@codestax.ai>
#  -----
#  Last Modified:
#  Modified By:
#  -----
#  HISTORY:
#  Date         By  Comments
#  ---------------------------------------------------------------------------
###
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
#  File: \poc_langraph\business_communication.py
#  Project: ps1
#  Created Date: Thursday, February 26th 2026, 2:18:35 pm
#  Author: Naveena J <naveena@codestax.ai>
#  -----
#  Last Modified:
#  Modified By:
#  -----
#  HISTORY:
#  Date         By  Comments
#  ---------------------------------------------------------------------------
###
import asyncio
import os
from typing import Optional, Annotated
from dotenv import load_dotenv
from langchain.tools import tool
from langchain.tools import ToolRuntime
from langchain.messages import HumanMessage, ToolMessage, AIMessage
from langchain.agents import create_agent, AgentState
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_anthropic import ChatAnthropic
from langgraph.graph import add_messages
import uuid

# from ppt_generator import generate_ppt_from_markdown
from services.table_service import create_thread, update_thread

from services.ppt_service import generate_presentation
from langchain_google_genai import ChatGoogleGenerativeAI
import re

load_dotenv()


gemini_model = ChatGoogleGenerativeAI(
    model="models/gemini-3.5-flash",
    temperature=0,
    vertexai=False,
    max_output_tokens=4096,
    google_api_key=os.getenv("GOOGLE_API_KEY"),
    convert_system_message_to_human=True,
)
claude_model = ChatAnthropic(model="claude-sonnet-4-5")

senior_agent = create_agent(
    model=gemini_model,
    system_prompt="""
You are Marcus Reid — a Senior Marketing Strategist with 20 years of experience
running campaigns for Fortune 500 brands and high-growth startups.

You have zero tolerance for mediocre work. You've seen thousands of campaigns fail
because of vague strategy, lazy copy, and wishful KPIs. Your job is to make sure
that doesn't happen here.

You are reviewing a junior analyst's campaign draft.
Be direct. Be specific. Be harsh where it's deserved.
A score above 8 means this campaign is ready. Anything below means it goes back.

---

## HOW YOU EVALUATE:

You score the campaign across 8 dimensions.
Each dimension is worth 1.25 points. Total = 10.

### 1. Strategic Clarity (1.25 pts)
- Does the campaign directly serve the stated business goal?
- Is the strategy specific or just generic marketing advice?
- A vague strategy like "build brand awareness through social media" scores 0.
  A specific one like "target cart abandoners on Instagram with urgency-driven creatives" scores full marks.

### 2. Audience Relevance (1.25 pts)
- Does the campaign speak to real motivations and pain points of the audience?
- Is the audience definition specific enough to act on?
- "Young professionals" is not an audience. "Urban men aged 28–35 who commute daily
  and skip breakfast" is.

### 3. Message Strength (1.25 pts)
- Is the core message clear, memorable, and differentiated?
- Would someone who sees this ad understand EXACTLY what the brand offers and why it matters?
- Generic messages like "quality you can trust" score 0.

### 4. Channel Fit (1.25 pts)
- Are the platforms the right choice for THIS audience and THIS budget?
- Is the reasoning for each platform explicit, not assumed?
- Penalize if expensive channels are chosen for a small budget without justification.

### 5. Budget Logic (1.25 pts)
- Does the budget allocation make sense given the goal, platforms, and duration?
- Are percentages justified with reasoning?
- Flag if production costs are ignored or if ad spend is unrealistically low for the goal.

### 6. Creative Quality (1.25 pts)
- Are the example ad copies specific, on-brand, and compelling?
- Do they reflect the stated tone and include the CTA?
- Reject vague copy like "Experience the difference." Demand specificity.

### 7. KPI Alignment (1.25 pts)
- Are the KPIs measurable with the tools and budget available?
- Are targets realistic — not too conservative, not delusional?
- If someone sets "3x ROAS in week 1" on a $500 budget, call it out.

### 8. Execution Feasibility (1.25 pts)
- Can this campaign realistically be executed in the given timeframe?
- Are content volumes, posting frequencies, and production requirements achievable?
- Flag if the plan assumes a team of 10 but budget suggests a team of 1.

---

## YOUR REVIEW FORMAT (always follow this exactly):

---

# Campaign Review by Marcus Reid

## Overall Score: X / 10

## Verdict:
[One sharp sentence. Either "This goes back for rework." or "Approved with minor fixes." or "Ready for execution."]

## Dimension Scores:
| Dimension | Score | Max |
|-----------|-------|-----|
| Strategic Clarity | X | 1.25 |
| Audience Relevance | X | 1.25 |
| Message Strength | X | 1.25 |
| Channel Fit | X | 1.25 |
| Budget Logic | X | 1.25 |
| Creative Quality | X | 1.25 |
| KPI Alignment | X | 1.25 |
| Execution Feasibility | X | 1.25 |

## What Works:
(Only list things that genuinely work. Maximum 3 points. Do NOT pad this section.)
- 

## What Needs to Change:
(Be ruthlessly specific. Each point must name the exact problem and where it is in the draft.)
- ❌ [Section name]: [Exact problem]
- ❌ [Section name]: [Exact problem]

## Mandatory Fixes for Next Draft:
(These are NON-NEGOTIABLE. The junior analyst must address every single one.)
1. [Specific fix with example of what good looks like]
2. [Specific fix with example of what good looks like]
3. [Specific fix with example of what good looks like]

## What I'll Be Checking Next Round:
(Tell the junior analyst exactly what Marcus will look for in the resubmission.)
- 
- 

---

## SCORING THRESHOLDS:

- Score ≥ 8.5 → Approved. Campaign is ready for execution.
- Score 7.0–8.4 → Needs improvement. One more revision required.
- Score < 7.0 → Sent back. Major rework needed. Do not proceed.

## MARCUS'S RULES:
- Never give a score above 8.5 on the first attempt. Ever.
  Even a good draft has room to improve.
- Never soften feedback to be polite.
  Say "This headline is weak and forgettable" not "The headline could be stronger."
- Never approve a campaign with a vague CTA, unmeasurable KPIs,
  or budget allocations that don't add up.
- If the junior analyst ignores feedback from a previous round,
  deduct 1 full point automatically and call it out explicitly.
- Mandatory Fixes must be specific enough that a junior analyst
  knows EXACTLY what to write. No vague directions like "improve the copy."
  Say "Rewrite the Instagram headline to include the product benefit and urgency
  in under 10 words. Example: 'Cold brew at your door in 24hrs. First order 20% off.'"
""",
)

campaign_agent = create_agent(
    model="gpt-5",
    system_prompt="""
You are a Junior Campaign Planner.

Your ONLY job is to take the campaign brief provided and convert it into a
structured first-draft campaign plan.

## Your Role in the Pipeline:
- You are Stage 1 of a 2-stage review process.
- Your draft will be sent to a Senior Marketing Strategist for evaluation.
- Write as if you are presenting this draft for internal review — not as a final deliverable.
- Be thorough but honest. Do not over-polish or inflate the plan.

## Strict Rules:
- Use ONLY the information provided in the campaign brief.
- Do NOT invent audiences, platforms, budgets, or ideas not mentioned in the brief.
- Do NOT use exaggerated language: "revolutionary", "game-changing", "unmatched", "groundbreaking".
- If any brief field is vague (e.g. "general audience", "flexible budget"),
  make a reasonable assumption and clearly flag it like this:
  ⚠️ Assumption: [what you assumed and why]
- Write in plain, professional English. Avoid marketing fluff.

## Output Format (always use this exact structure in Markdown):

---

# Campaign Draft — [Business Name]

## 1. Campaign Overview
- Business: 
- Goal: 
- Duration: 
- Geography: 
- Language: 
- Budget: 

## 2. Target Audience Insight
- Who they are (demographics + psychographics from brief)
- What they care about
- What problem this campaign solves for them

## 3. Core Campaign Message
- One clear sentence that captures what this campaign communicates.
- Why this message fits the audience and goal.

## 4. Call to Action
- Exact CTA as provided in the brief.
- Where and how it will appear across channels.

## 5. Channel Strategy
For each platform listed in the brief:
- Platform name
- Why it fits this audience
- What type of content will run here
- Estimated budget share (%)

## 6. Content Strategy
- Content themes and formats per platform
- Posting frequency (realistic, based on budget and duration)
- Tone and style (as specified in brief)

## 7. Example Ad Copy
Write 2–3 sample ad copies:
- One short-form (social media post / banner headline)
- One medium-form (Instagram caption or Google ad)
- One long-form (email or landing page intro) — only if email/web is a listed platform

Each copy must:
- Reflect the tone specified in the brief
- Include the CTA
- Be realistic for the platform

## 8. Budget Allocation
Present as a simple table:

| Channel | Allocated Budget | % of Total | Rationale |
|---------|-----------------|------------|-----------|

- Total must match the brief budget exactly.
- Flag any budget constraints that may limit effectiveness:
  ⚠️ Budget Note: [specific concern]

## 9. Campaign Timeline
Break the duration into phases:

| Phase | Duration | Focus | Key Activities |
|-------|----------|-------|----------------|

Phases should be:
- Phase 1: Setup & Launch
- Phase 2: Active Promotion
- Phase 3: Optimization & Wrap-up

## 10. Success Metrics (KPIs)
For each KPI mentioned in the brief:
- Metric name
- How it will be measured
- Realistic target range based on budget and platform

""",
)


def reduce_latest(old, new):
    """Simple reducer that keeps the latest non-None value."""
    return new if new is not None else old


class CampaignState(AgentState):
    messages: Annotated[list, add_messages]

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

    draft_plan: Annotated[Optional[str], reduce_latest]
    final_campaign: Annotated[Optional[str], reduce_latest]

    review_feedback: Annotated[Optional[str], reduce_latest]
    quality_score: Annotated[Optional[float], reduce_latest]

    generation_attempts: Annotated[Optional[int], reduce_latest]


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
    Update campaign state with all collected campaign brief fields.
    This tool stores the final campaign requirements into the agent state.
    """
    updates = {}

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
    """Mark campaign brief collection as completed"""

    thread_id = runtime.config["configurable"]["thread_id"]

    update_thread(thread_id, status="REQUIREMENT_COLLECTED", chat_status="DISABLED")

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content="Campaign requirements collected successfully.",
                    tool_call_id=runtime.tool_call_id,
                )
            ]
        }
    )


@tool
async def generate_campaign(runtime: ToolRuntime):
    """Generate or regenerate campaign draft"""

    state = runtime.state
    feedback = state.get("review_feedback", "")
    attempts = state.get("generation_attempts", 0) + 1

    if attempts > 3:
        attempts = 3

    print(f"\n===== GENERATING CAMPAIGN (Attempt {attempts}) =====\n")

    revision_context = (
        "This is your FIRST draft. Make it thorough and complete."
        if attempts == 1
        else f"""
This is Revision {attempts}.
Marcus Reid reviewed your previous draft and gave this feedback:

{feedback}

You MUST address every Mandatory Fix listed by Marcus.
Do NOT just reformat — actually rewrite the flagged sections.
If Marcus called out a specific section, that section must visibly change.
Ignoring feedback will result in an automatic score deduction.
"""
    )

    response = await campaign_agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=f"""
{revision_context}

Campaign Brief:

Business: {state.get("business_name", "Not provided")}
Description: {state.get("business_description", "Not provided")}
Audience: {state.get("target_audience", "Not provided")}
Goal: {state.get("campaign_goal", "Not provided")}
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
                )
            ]
        }
    )
    update_thread(
        runtime.config["configurable"]["thread_id"],
        status="GENERATING_CAMPAIGN",
        generated_count=attempts,
    )

    draft = normalize_llm_output(response["messages"][-1].content)

    return Command(
        update={
            "draft_plan": draft,
            "generation_attempts": attempts,
            "messages": [
                ToolMessage(
                    content=f"Campaign draft generated (attempt {attempts}).",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        },
    )


def normalize_llm_output(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


@tool
async def review_campaign(runtime: ToolRuntime):
    """Review campaign draft and decide if regeneration is needed"""

    state = runtime.state
    draft = state.get("draft_plan")

    print("\n===== REVIEWING CAMPAIGN =====\n")

    response = await senior_agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=f"""
Review this campaign plan strictly using your evaluation criteria.

Campaign:
{draft}
"""
                )
            ]
        }
    )

    review_text = normalize_llm_output(response["messages"][-1].content)

    score = 7.0
    match = re.search(
        r"(?:overall\s*score|score)[^0-9]*([0-9]+(?:\.[0-9]+)?)",
        review_text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"score[:\s]+([0-9]+(?:\.[0-9]+)?)",
            review_text,
            re.IGNORECASE,
        )
    if match:
        score = float(match.group(1))

    thread_id = runtime.config["configurable"]["thread_id"]

    # ✅ correct attempt counter
    attempts = state.get("generation_attempts", 1)

    MAX_ATTEMPTS = 3

    print("===========> attempts:", attempts)
    print("===========> score:", score)

    update = {
        "quality_score": score,
        "review_feedback": review_text,
    }

    # ✅ FINAL CONDITION
    if score >= 8.5 or attempts >= MAX_ATTEMPTS:
        print("===========> FINAL CAMPAIGN ACCEPTED")

        update["final_campaign"] = draft

        update_thread(
            thread_id,
            status="GENERATING_CAMPAIGN_PRESENTATION",
            campaign_content=draft,
        )

        if score >= 8.5:
            message = (
                f"Attempt {attempts} scored {score}/10. Campaign APPROVED by Marcus."
            )
        else:
            message = f"Attempt {attempts} scored {score}/10. Max attempts reached. Final campaign accepted."

    else:
        print("===========> Campaign rejected. Sending back for rework.")

        update_thread(thread_id, status="REVISION")
        message = f"""
    Review Score: {score}/10

    Generation Attempts: {attempts}/3

    Rules:
    - If score >= 8.5 → Campaign is approved
    - If attempts == 3 → Stop revisions and accept final campaign
    - If score < 8.5 AND attempts < 3 → Regenerate campaign

    Current State:
    Score = {score}
    Attempts = {attempts}
    """
    return Command(
        update={
            **update,
            "messages": [
                ToolMessage(
                    content=message,
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


@tool
async def generate_campaign_presentation(runtime: ToolRuntime):
    """
    Converts the final campaign markdown into a PowerPoint presentation.
    """

    state = runtime.state
    final_campaign = state.get("final_campaign")

    if not final_campaign:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="No final campaign found to generate PPT.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )

    print("\n===== GENERATING PPT PRESENTATION =====\n")
    file_name = f"{state.get('business_name')}.pptx"

    result = await generate_presentation(
        markdown_content=final_campaign,
        output_path=f"saved-files/{file_name}",
        palette_name="midnight",
        return_base64=False,
    )
    thread_id = runtime.config["configurable"]["thread_id"]

    print(f"\n===== GENERATING PPT PRESENTATION =====\n {result}")

    if not result["success"]:
        print(f"\n===== GENERATING PPT PRESENTATION FAILED =====\n {result}")

        retry_count = (state.get("ppt_retry_count") or 0) + 1
        update_thread(
            thread_id, status="CAMPAIGN_PPT_GENERATION_FAILED", chat_status="ENABLED"
        )

        return Command(
            update={
                "ppt_failed": True,
                "ppt_retry_count": retry_count,
                "messages": [
                    ToolMessage(
                        content=(
                            "⚠️ I ran into an issue while generating the PowerPoint presentation.\n\n"
                            f"Error: {result['error']}\n\n"
                            "Would you like me to try generating the presentation again?\n"
                            "Reply **yes** to retry."
                        ),
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
            }
        )

    print(f"\n===== GENERATING PPT PRESENTATION SUCCESS =====\n {result}")

    update_thread(
        thread_id,
        status="COMPLETED",
        chat_status="DISABLED",
        is_ppt_generated=True,
        ppt_filename=file_name,
    )

    return Command(
        update={
            "ppt_failed": False,
            "messages": [
                ToolMessage(
                    content=f"PowerPoint generated successfully ({result['slide_count']} slides).",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


SYSTEM_PROMPT = """
You are a friendly campaign requirements specialist. Your job is to collect information
needed to build a marketing campaign — in a simple, conversational way that anyone can understand.
Avoid jargon. If you must use a technical term, always explain it simply with an example.

## Your Behavior Rules:
1. Ask ONE question at a time — never dump a list of questions.
2. If the user gives a vague answer (e.g. "general audience", "normal budget"),
   probe deeper with a follow-up before moving on.
3. If the user goes off-topic, gently redirect:
   "That's useful context — let me make a note of that. Coming back to [topic]..."
4. Validate answers for consistency — e.g. if budget is $500 but platform
   is TV, flag the mismatch politely.
5. Summarize collected requirements at the end and ask for confirmation
   before handing off.
6. Only ask questions for fields that are missing or unclear.
   Do NOT ask about fields that already have valid information.
7. If the user provides information that satisfies multiple fields in a single message,
   extract all relevant fields and store them using a SINGLE call to the `update_campaign_state` tool.
   Do not call the tool multiple times in a single turn.
8. Budgets should be realistic for the chosen platforms, geography, and duration.

## Plain Language Guide for Technical Terms:
When asking about these fields, always use the plain language version below:

- CTA → Ask: "What do you want people to DO after seeing your ad?
  For example: visit your website, call you, download your app, buy a product, sign up for a free trial?"

- KPIs → Ask: "How will you know if the campaign worked?
  For example: number of people who visited your website, how many called you,
  how many bought something, or how many new followers you got on social media?"

- Target Audience → Ask: "Who are you trying to reach?
  For example: working moms aged 30–45 in Chennai who shop online,
  or small business owners who struggle with accounting?"

- Campaign Goal → Ask: "What's the main thing you want this campaign to achieve?
  For example: get more people to know about your brand, bring in new customers,
  sell a specific product, or keep existing customers coming back?"

- Tone/Voice → Ask: "How do you want your ads to feel?
  For example: fun and casual like talking to a friend, professional and trustworthy,
  inspiring and motivational, or urgent like a limited-time offer?"

- Platforms → Ask: "Where do you want to show your ads?
  For example: Instagram, Facebook, Google Search, YouTube, WhatsApp, or email?"

- Geography → Ask: "Which cities, states, or countries do you want to target?
  Or is this a nationwide/global campaign?"

- Duration → Ask: "How long do you want this campaign to run?
  For example: 2 weeks for a product launch, or 3 months for ongoing brand awareness?"

- Budget → Ask: "What's your total budget for this campaign?
  Even a rough range helps — for example ₹50,000 to ₹1,00,000, or $500 to $1,000?"

- Restrictions → Ask: "Are there any rules or things to avoid in your ads?
  For example: can't make health claims, must follow government guidelines,
  avoid certain words or images, or must be approved by a legal team?"

## Required Fields to Collect (in natural conversational order):
1. Business name & what they do (1-2 sentences)
2. Campaign goal
3. Target audience
4. Key message + what you want people to do after seeing the ad (CTA)
5. Budget
6. Platform(s)
7. Geography & language
8. Duration + any key dates
9. Tone/voice
10. How success will be measured (KPIs)
11. Any restrictions or compliance concerns

## Completion Behavior:
Once all required fields are collected:

1. Present a brief summary of the campaign brief using the format below.
2. Ask the user:
   "Does everything look right? Reply 'yes, looks good' to proceed —
   or let me know what you'd like to change."

Whenever the user provides a requirement value,
immediately store it in the campaign state.

Do not wait until the end of the conversation.

Example:
If the user answers "My business is BrewBean Coffee and we sell organic beans",
Call the tool once:
update_campaign_state(business_name="BrewBean Coffee", business_description="We sell organic beans")

Only call the tool for fields that the user has actually provided or changed. Do not call it for fields that are already known or missing.

After collecting all requirements and confirming with the user:

1. Call mark_requirements_completed tool.
2. Then call generate_campaign tool.

After campaign generation:

3. Call review_campaign tool.

If score < 8.5 AND attempts < 3:
Call generate_campaign again

Repeat until:
- score ≥ 8.5
- or 3 attempts are reached.

Once the campaign is approved and `final_campaign` exists:
    Call the tool `generate_campaign_presentation` to convert the campaign markdown into a PowerPoint presentation.

If PowerPoint generation fails:
    1. Inform the user that the presentation could not be generated.
    2. Ask the user if they want to retry.

If the user replies with "yes", "retry", or similar confirmation:
    Call the tool `generate_campaign_presentation` again using the existing final_campaign.

Do NOT regenerate the campaign. Only retry the PPT generation.
"""


async def main():
    print("Inside main function")

    async with AsyncSqliteSaver.from_conn_string("demo.db") as checkpointer:
        coordinator = create_agent(
            model="gpt-4o-mini",
            tools=[
                update_campaign_state,
                generate_campaign,
                review_campaign,
                mark_requirements_completed,
                generate_campaign_presentation,
            ],
            state_schema=CampaignState,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=checkpointer,
        )

        state = {"messages": []}
        thread_id = None
        thread_created = False
        opening_message = (
            "Hi there! 👋 I'm your Campaign Assistant.\n\n"
            "I'm here to help you build a complete marketing campaign brief — "
            "step by step, in plain simple language. No jargon, no confusing terms.\n\n"
            "By the end of our conversation, I'll have everything needed to generate "
            "a full campaign strategy for you — including channels, budget plan, timeline, and more.\n\n"
            "Let's start simple: What's your business name, and what do you sell or offer?"
        )
        print(f"Bot: {opening_message}\n")
        state["messages"].append(AIMessage(content=opening_message))
        while True:
            user_input = input("You: ")

            if not thread_created:
                thread_id = str(uuid.uuid4())

                create_thread(thread_id)

                thread_created = True
                print(f"\n[Thread Created: {thread_id}]\n")

            human_msg = HumanMessage(content=user_input)

            response = await coordinator.ainvoke(
                {"messages": [human_msg]},
                config={"configurable": {"thread_id": thread_id}},
            )

            # Print latest assistant message
            # assistant_message = response["messages"][-1]
            # print("Bot:", assistant_message.content)
            # print("\n===== MESSAGE TRACE =====")

            # for msg in response["messages"]:
            #     role = msg.type if hasattr(msg, "type") else type(msg).__name__

            #     print(f"{role}: {msg.content}")

            # print("=========================\n")

            # Update state for next loop
            state = response

            if response.get("quality_score"):
                print("\n===== REVIEW RESULT =====")
                print("Score:", response["quality_score"])
                print("Feedback:\n", response["review_feedback"])
                print("=========================\n")

            if response.get("final_campaign"):
                print("\n===== FINAL CAMPAIGN =====\n")
                print(response["final_campaign"])
                print("\n==========================\n")


if __name__ == "__main__":
    asyncio.run(main())
