from mcp.server.fastmcp import FastMCP
from datetime import datetime
from zoneinfo import ZoneInfo

mcp = FastMCP("Time")

@mcp.tool()
async def get_current_time(timezone: str = "UTC") -> str:
    """Get the current time in the specified timezone (e.g. UTC, Asia/Bangkok, America/New_York)."""
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        return f"The current time in {timezone} is {now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    except Exception as e:
        now = datetime.now()
        return f"The current local time is {now.strftime('%Y-%m-%d %H:%M:%S')} (Error resolving {timezone}: {e})"

if __name__ == "__main__":
    mcp.run()
