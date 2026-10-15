"""Ask the Match as a Microsoft Foundry agent.

The agent (instructions + a hosted MCP tool pointing at our public match MCP server) is
published to the Foundry project once; Foundry then runs it and calls our MCP server itself.

    python -m pitchpulse.foundry_agent publish https://<app-host>/mcp/
"""
import os
import sys

from .agents import MODEL

PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
AGENT_NAME = "ask-the-match"
MATCH_TOOLS = ["match_snapshot", "player_stats", "top_players", "key_moments"]
INSTRUCTIONS = (
    "You are 'Ask the Match', the TV studio's data assistant for PitchPulse synthetic football matches. Each "
    "message starts with the match seed, until_event, answer language and audience, then the viewer's question. "
    "Answer using ONLY the match tools, passing that seed and until_event to every tool call. Max 60 words, in the "
    "requested language, for the requested audience. For 'right now' questions use the last-5-minute figures "
    "(flow.*, momentum), otherwise the match totals. Before answering, check every comparison: the higher number must "
    "be the one you call higher. Quote numbers exactly as the tools return them; never invent numbers or players. "
    "If the tools cannot answer it, say so briefly."
)


def message(question, seed, until_event, language, audience):
    return f"seed={seed}; until_event={until_event}; language={language}. {audience}\nQuestion: {question}"


async def run(text):
    """Run the published Foundry agent once; returns the agent response."""
    from agent_framework.foundry import FoundryAgent
    from azure.identity.aio import DefaultAzureCredential

    async with DefaultAzureCredential() as credential:
        agent = FoundryAgent(project_endpoint=PROJECT_ENDPOINT, agent_name=AGENT_NAME, credential=credential)
        return await agent.run(text)


def publish(mcp_url):
    """Create a new version of the agent in the Foundry project (needs the 'Foundry User' role)."""
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient, to_prompt_agent
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential

    credential = AzureCliCredential()
    client = FoundryChatClient(project_endpoint=PROJECT_ENDPOINT, model=MODEL, credential=credential)
    match_mcp = client.get_mcp_tool(name="pitchpulse_match", url=mcp_url, approval_mode="never_require",
                                    allowed_tools=MATCH_TOOLS,
                                    description="Live stats, players and key moments of a PitchPulse match")
    definition = to_prompt_agent(Agent(client, name=AGENT_NAME, instructions=INSTRUCTIONS, tools=[match_mcp]))
    with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential) as project:
        version = project.agents.create_version(
            agent_name=AGENT_NAME, definition=definition,
            description="Answers viewer questions about a live PitchPulse match through the match MCP server.")
    print(f"published {version.name} version {version.version}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "publish" or not PROJECT_ENDPOINT:
        sys.exit("usage: FOUNDRY_PROJECT_ENDPOINT=... python -m pitchpulse.foundry_agent publish <mcp-url>")
    publish(sys.argv[2])
