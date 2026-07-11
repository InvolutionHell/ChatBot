import logging

from mcp.server.fastmcp import FastMCP

from chat_bot.api_client import (
    DuplicateURL,
    InternalAPIError,
    fetch_link,
    fetch_summary,
    submit_internal,
)
from chat_bot.config import Settings

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chat_bot.mcp_server")

# Load settings
try:
    settings = Settings()
except Exception as e:
    logger.error("Failed to load settings: %s", e)
    raise

# Initialize FastMCP
mcp = FastMCP("Involution Hell Bridge")


@mcp.tool()
async def submit_link(url: str, submitter: str, recommendation: str = None) -> str:
    """Submit a shared link to the involution-hell community links pipeline for enrichment and audit.

    Args:
        url: The URL of the shared link to submit.
        submitter: The name/label of the person sharing the link.
        recommendation: Optional recommendation, summary or note about the shared link.
    """
    logger.info(f"mcp_submit_link url={url} submitter={submitter}")
    try:
        res = await submit_internal(
            base_url=settings.internal_submit_url,
            internal_key=settings.internal_api_key.get_secret_value(),
            url=url,
            submitter_label=submitter,
            recommendation=recommendation,
        )
        return f"Successfully submitted link.\nID: {res.link_id}\nStatus: {res.status}\nHost: {res.host}\nTitle: {res.og_title or 'N/A'}"
    except DuplicateURL:
        return f"Submission rejected: The URL {url} has already been shared in the community."
    except InternalAPIError as e:
        return f"Backend API Error [{e.status}]: {e.message}"
    except Exception as e:
        return f"Unexpected error during submission: {e}"


@mcp.tool()
async def get_link_status(link_id: int) -> str:
    """Fetch the status and detail of a submitted community link by its ID.

    Args:
        link_id: The ID of the submitted link.
    """
    logger.info(f"mcp_get_link_status link_id={link_id}")
    try:
        res = await fetch_link(
            base_url=settings.internal_submit_url,
            internal_key=settings.internal_api_key.get_secret_value(),
            link_id=link_id,
        )
        if res is None:
            return f"Link with ID {link_id} not found."

        details = [
            f"ID: {res.link_id}",
            f"URL: {res.url}",
            f"Host: {res.host}",
            f"Status: {res.status}",
            f"Title: {res.og_title or 'N/A'}",
            f"Description: {res.og_description or 'N/A'}",
            f"Cover Image: {res.og_cover or 'N/A'}",
            f"Recommendation: {res.recommendation or 'N/A'}",
        ]
        return "\n".join(details)
    except InternalAPIError as e:
        return f"Backend API Error [{e.status}]: {e.message}"
    except Exception as e:
        return f"Unexpected error: {e}"


@mcp.tool()
async def get_community_summary(sample_limit: int = 5) -> str:
    """Get the administrative summary of community link submissions, including pending, flagged, and approved counts.

    Args:
        sample_limit: The maximum number of pending samples to return. Default is 5.
    """
    logger.info(f"mcp_get_community_summary sample_limit={sample_limit}")
    try:
        res = await fetch_summary(
            base_url=settings.internal_submit_url,
            internal_key=settings.internal_api_key.get_secret_value(),
            sample_limit=sample_limit,
        )

        lines = [
            "### Community Links Queue Summary",
            f"- Pending Manual Review: {res.pending_manual}",
            f"- Flagged (Security/Suspicious): {res.flagged}",
            f"- Approved in last 24h: {res.approved_last_24h}",
            "",
            "### Pending Samples in Queue:",
        ]
        if not res.pending_samples:
            lines.append("No pending items.")
        else:
            for item in res.pending_samples:
                lines.append(f"- [{item.get('id')}]: {item.get('url')} (Host: {item.get('host')})")

        return "\n".join(lines)
    except InternalAPIError as e:
        return f"Backend API Error [{e.status}]: {e.message}"
    except Exception as e:
        return f"Unexpected error: {e}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
