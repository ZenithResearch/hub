import logging

import uvicorn

from .appservice import create_app
from .config import IngestSettings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    settings = IngestSettings()
    app = create_app(settings)
    logger.info(
        "Starting ingest app service on port %s", settings.as_port
    )
    uvicorn.run(app, host="0.0.0.0", port=settings.as_port)


if __name__ == "__main__":
    main()
