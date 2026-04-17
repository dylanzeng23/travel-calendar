import json
import logging
import os
from datetime import datetime, UTC

import anthropic
from tavily import TavilyClient

from models import Config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_WITH_SEARCH = """You are an expert travel planner assistant. You help users plan detailed trip itineraries.

Your job:
1. Chat naturally with the user about their trip — ask clarifying questions about preferences, must-sees, dietary needs, pace, budget, etc.
2. Use the web_search tool to look up REAL, current information: opening hours, transport options, restaurant recommendations, ticket prices, travel advisories, weather, etc.
3. Build a detailed day-by-day itinerary incrementally based on the conversation.
4. When the user is happy with the plan, present a clean text preview of the full itinerary.

Guidelines:
- Be proactive — suggest things based on what you know and search for
- Include practical details: addresses, opening hours, expected costs, booking tips
- Consider travel time between locations realistically
- Mix popular spots with local gems
- Include meal times and restaurant suggestions
- Add reminders for things like melatonin for jet lag, early check-in, etc.
- Respect the user's pace — don't over-schedule
- Use emojis in the text preview to make it scannable
- NEVER fake or simulate tool calls, web searches, or API calls in your text. Do not output XML tags or JSON tool call blocks.

When presenting the itinerary preview, use this format:
📋 TRIP TITLE — Dates

Day 1 (Jun 15, Sun) — Theme
✈️ HH:MM Event description
🏨 HH:MM Hotel check-in
🍝 HH:MM Restaurant name
🏛️ HH:MM Activity
...

The user will say something like "generate it" or "make the calendar" when ready.
Respond in the same language the user uses (Chinese or English)."""

SYSTEM_PROMPT_NO_SEARCH = """You are an expert travel planner assistant. You help users plan detailed trip itineraries.

Your job:
1. Chat naturally with the user about their trip — ask clarifying questions about preferences, must-sees, dietary needs, pace, budget, etc.
2. Build a detailed day-by-day itinerary based on the conversation using your knowledge.
3. When the user is happy with the plan, present a clean text preview of the full itinerary.

IMPORTANT: You do NOT have web search capability. Use your existing knowledge to make recommendations. Be honest when you're unsure about specific details like current opening hours or prices — note them as "verify before trip" rather than guessing.

Guidelines:
- Be proactive — suggest things based on what you know
- Include practical details where you're confident, mark uncertain details for verification
- Consider travel time between locations realistically
- Mix popular spots with local gems
- Include meal times and restaurant suggestions
- Add reminders for things like melatonin for jet lag, early check-in, etc.
- Respect the user's pace — don't over-schedule
- Use emojis in the text preview to make it scannable

When presenting the itinerary preview, use this format:
📋 TRIP TITLE — Dates

Day 1 (Jun 15, Sun) — Theme
✈️ HH:MM Event description
🏨 HH:MM Hotel check-in
🍝 HH:MM Restaurant name
🏛️ HH:MM Activity
...

The user will say something like "generate it" or "make the calendar" when ready.
Respond in the same language the user uses (Chinese or English)."""

FINALIZE_PROMPT = """Based on the conversation so far, generate a complete structured itinerary as JSON.

Output ONLY valid JSON with this exact schema (no markdown, no explanation):
{
  "title": "Trip Title",
  "timezone": "Europe/Rome",
  "days": [
    {
      "date": "2026-06-15",
      "events": [
        {
          "title": "Event name",
          "start": "2026-06-15T09:00",
          "end": "2026-06-15T10:30",
          "location": "Address or place name",
          "description": "Practical notes, tips, booking refs",
          "category": "activity",
          "reminder_minutes": 30
        }
      ]
    }
  ]
}

Categories: flight, hotel, meal, activity, transit, reminder
- For flights: include flight number in title, terminals in description
- For hotels: check-in as start, check-out next day as a separate event
- For meals: include restaurant name and address
- For reminders (like melatonin): short duration (15min), descriptive title
- reminder_minutes: 180 for flights, 60 for activities, 30 for meals, 15 for reminders
- Ensure all events have realistic start/end times
- Use the timezone specified for the destination"""

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for real-time travel information: opening hours, prices, transport schedules, restaurant reviews, weather forecasts, travel advisories, etc. Use this to provide accurate, up-to-date information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Be specific, e.g. 'Uffizi Gallery Florence opening hours June 2026' or 'best ramen near Shinjuku station'"
                }
            },
            "required": ["query"],
        },
    }
]


class TravelPlanner:
    def __init__(self, config: Config):
        # Support multiple auth methods:
        # 1. ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL (Bedrock via CloudFront proxy)
        # 2. Direct ANTHROPIC_API_KEY
        # 3. Config file values
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
        api_key = config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")

        if auth_token and base_url:
            # Bedrock via proxy: use auth token as API key with custom base URL
            self.client = anthropic.Anthropic(api_key=auth_token, base_url=base_url)
            logger.info(f"Using Bedrock proxy at {base_url}")
        elif api_key:
            self.client = anthropic.Anthropic(api_key=api_key)
            logger.info("Using direct Anthropic API key")
        else:
            # Let the SDK figure it out from environment
            self.client = anthropic.Anthropic()
            logger.info("Using default Anthropic client (env-based)")

        tavily_key = config.tavily_api_key or os.environ.get("TAVILY_API_KEY", "")
        self.tavily = TavilyClient(api_key=tavily_key) if tavily_key else None
        self.model = config.anthropic_model

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "web_search":
            query = tool_input["query"]
            logger.info(f"Web search: {query}")
            try:
                result = self.tavily.search(query=query, max_results=5)
                snippets = []
                for r in result.get("results", []):
                    snippets.append(f"**{r['title']}**\n{r['content']}\nURL: {r['url']}")
                return "\n\n---\n\n".join(snippets) if snippets else "No results found."
            except Exception as e:
                logger.error(f"Tavily search error: {e}")
                return f"Search failed: {e}"
        return f"Unknown tool: {tool_name}"

    @property
    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT_WITH_SEARCH if self.tavily else SYSTEM_PROMPT_NO_SEARCH

    def _call_claude(self, messages: list[dict], system: str = None) -> str:
        """Call Claude with tool use loop — handles multiple tool calls until a text response."""
        if system is None:
            system = self._system_prompt
        kwargs = dict(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        if self.tavily:
            kwargs["tools"] = TOOLS
        response = self.client.messages.create(**kwargs)

        # Tool use loop
        while response.stop_reason == "tool_use":
            # Collect all tool uses and results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = self._execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # Add assistant response and tool results to messages
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            # Continue the conversation
            response = self.client.messages.create(**kwargs | {"messages": messages})

        # Extract text from final response
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "\n".join(text_parts)

    def chat(self, conversation: list[dict], user_message: str) -> str:
        """Send a message in the planning conversation. Returns Claude's response text."""
        messages = list(conversation)
        messages.append({"role": "user", "content": user_message})
        return self._call_claude(messages)

    def chat_with_image(self, conversation: list[dict], image_bytes: bytes, media_type: str, caption: str = "") -> str:
        """Send an image (with optional caption) in the planning conversation."""
        import base64
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
        ]
        if caption:
            content.append({"type": "text", "text": caption})
        else:
            content.append({"type": "text", "text": "The user sent this image. Describe what you see and incorporate it into the trip planning."})
        messages = list(conversation)
        messages.append({"role": "user", "content": content})
        return self._call_claude(messages)

    def finalize(self, conversation: list[dict]) -> dict | None:
        """Generate the structured JSON itinerary from the conversation."""
        messages = list(conversation)
        messages.append({
            "role": "user",
            "content": FINALIZE_PROMPT,
        })

        response = self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=self._system_prompt,
            messages=messages,
        )

        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        # Parse JSON — try to extract from markdown code block if present
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse itinerary JSON: {e}\nRaw text: {text[:500]}")
            return None
