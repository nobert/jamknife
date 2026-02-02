"""Main entry point for Jamknife application."""

import logging
import sys
from pathlib import Path

import uvicorn

from jamknife.config import get_config
from jamknife.web.app import app, setup_templates

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def main():
    """Run the Jamknife application."""
    config = get_config()

    # Validate configuration
    errors = config.validate()
    if errors:
        for error in errors:
            logger.error("Configuration error: %s", error)
        logger.warning("Some features may not work without proper configuration")

    # Set up templates directory
    templates_dir = Path(__file__).parent / "web" / "templates"
    setup_templates(str(templates_dir))

    # Ensure data directory exists
    config.data_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting Jamknife on %s:%d", config.web_host, config.web_port)
    logger.info("Data directory: %s", config.data_dir)
    logger.info("Downloads directory: %s", config.downloads_dir)

    uvicorn.run(
        app,
        host=config.web_host,
        port=config.web_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
