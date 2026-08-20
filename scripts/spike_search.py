"""Day-1 spike (docs/08, 14): one grounded GoogleSearchTool query on
Vertex AI + the configured model. Records the result to docs/verification-notes.md.

Fallback if this fails: plain search HTTP API behind the same
`search_programs(query)` signature — the lane contract does not change.
"""

import asyncio
import os

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types


async def main() -> None:
    agent = Agent(
        name="search_spike",
        model=os.environ.get("ADK_MODEL", "gemini-3.5-flash"),
        instruction="Answer using Google Search grounding. List 3 results as URLs.",
        tools=[GoogleSearchTool()],
    )
    runner = Runner(agent=agent, app_name="spike", session_service=InMemorySessionService())
    session = await runner.session_service.create_session(app_name="spike", user_id="spike")
    async for event in runner.run_async(
        user_id="spike",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part.from_text(
                text="Find 3 startup grant or accelerator programs accepting applications in 2026."
            )],
        ),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(part.text[:500])


if __name__ == "__main__":
    asyncio.run(main())
