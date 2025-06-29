#!/usr/bin/env python
# client.py
import os
import sys
import asyncio
import logging
import argparse
from agno.models.openai import OpenAIChat
from agno.agent import Agent
from agno.tools.mcp import MCPTools
from mcp import ClientSession
from rich.console import Console
from rich.prompt import Prompt
from rich.theme import Theme
from typing import Optional
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv

# Silence all logging
class SilentFilter(logging.Filter):
    def filter(self, record):
        return False

# Configure root logger to be silent
root_logger = logging.getLogger()
root_logger.addFilter(SilentFilter())
root_logger.setLevel(logging.CRITICAL)

# Silence specific loggers
for logger_name in ['agno', 'httpx', 'urllib3', 'asyncio']:
    logger = logging.getLogger(logger_name)
    logger.addFilter(SilentFilter())
    logger.setLevel(logging.CRITICAL)
    logger.propagate = False

# Redirect stdout/stderr for the agno library
class DevNull:
    def write(self, msg): pass
    def flush(self): pass

sys.stderr = DevNull()

# Define custom theme
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "yellow",
    "danger": "bold red",
    "success": "bold green",
    "query": "bold blue",
    "response": "bold green",
    "assistant": "bold magenta",
    "user": "bold magenta"
})

# Initialize rich console with custom theme
console = Console(theme=custom_theme)

load_dotenv()

class MCPClient:
    def __init__(self):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

    async def connect_to_sse_server(self, server_url: str):
        """Connect to an MCP server running with SSE transport"""
        # Store the context managers so they stay alive
        self._streams_context = sse_client(url=server_url)
        streams = await self._streams_context.__aenter__()

        self._session_context = ClientSession(*streams)
        self.session: ClientSession = await self._session_context.__aenter__()

        # Initialize
        await self.session.initialize()

        # List available tools to verify connection
        response = await self.session.list_tools()

    async def cleanup(self):
        """Properly clean up the session and streams"""
        if self._session_context:
            await self._session_context.__aexit__(None, None, None)
        if self._streams_context:
            await self._streams_context.__aexit__(None, None, None)

    async def disconnect(self):
        """Disconnect from the MCP server"""
        await self.cleanup()

async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='MCP Client')
    parser.add_argument('--host', help='MCP server host (default: localhost)')
    parser.add_argument('--port', type=int, help='MCP server port (default: 8001)')
    args = parser.parse_args()

    # Get MCP host and port from args or environment variables
    mcp_host = args.host or os.environ.get("MCP_HOST", "localhost")
    mcp_port = args.port or int(os.environ.get("MCP_PORT", "8001"))
    mcp_url = f"http://{mcp_host}:{mcp_port}/sse"

    mcp_client = MCPClient()
    await mcp_client.connect_to_sse_server(mcp_url)

    # List available tools
    response = await mcp_client.session.list_tools()
    mcp_tools = MCPTools(session=mcp_client.session)
    await mcp_tools.initialize()

    # Create the Agno agent with Gemini model
    agent = Agent(
        instructions="""
You are a Zerodha Trading Account Assistant, helping users securely manage their accounts, orders, portfolio, and positions using tools provided over MCP.

# Important Instructions:
- ALWAYS respond in plain text. NEVER use markdown formatting (no asterisks, hashes, or code blocks).
- Respond in human-like conversational, friendly, and professional tone in concise manner.

# Authentication Steps (must be followed if no access token is generated):
1. Use the 'get_login_url' tool to generate a Kite login URL and ask the user to log in and send the request token to you. Use this tool automatically when the user is not authenticated.
2. Use the 'get_access_token' tool with the request token to generate and validate the access token.
3. Proceed only if the access token is valid.

# Responsibilities:
- Check if the user is authenticated (e.g., by calling 'get_user_profile').
- Assist with order placement ('place_order'), modification ('modify_order'), and cancellation ('cancel_order').
- Provide insights on portfolio holdings ('get_holdings'), positions ('get_positions'), and available margin ('get_margins').
- Track order status ('get_orders'), execution details ('get_order_trades'), and trade history ('get_order_history').
- Any more tools can be used if needed.

# Limitations:
You do not provide real-time market quotes, historical data, or financial advice. Your role is to ensure secure, efficient, and compliant account management.
""",
        model=OpenAIChat(
            id="gpt-4o"
        ),
        add_history_to_messages=True,
        num_history_responses=10,
        tools=[mcp_tools],
        show_tool_calls=False,
        markdown=True,
        read_tool_call_history=True,
        read_chat_history=True,
        tool_call_limit=10,
        telemetry=False,
        add_datetime_to_instructions=True
    )

    # Welcome message
    console.print()
    console.print("[info]Welcome to Zerodha! I'm here to assist you with managing your trading account, orders, portfolio, and positions. How can I help you today?[/info]", style="response")

    try:
        while True:
            # Add spacing before the prompt
            console.print()
            # Get user input with rich prompt
            user_query = Prompt.ask("[query]Enter your query:[/query] [dim](or 'quit' to exit)[/dim]")

            # Check if user wants to quit
            if user_query.lower() == 'quit':
                break

            # Add spacing before the prompt
            console.print()
            # Display user query
            console.print(f"[user]You:[/user] {user_query}")
            # Add spacing before the assistant's response
            console.print()
            console.print(f"[assistant]Assistant:[/assistant] ", end="")

            # Run the agent and stream the response
            result = await agent.arun(user_query, stream=True)
            async for response in result:
                if response.content:
                    console.print(response.content, style="response", end="")

            console.print()  # Add newline after the full response
            console.print()  # Add extra spacing after the response

    except Exception as e:
        console.print(f"[danger]An error occurred: {str(e)}[/danger]")
    finally:
        # Disconnect from the MCP server
        await mcp_client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())