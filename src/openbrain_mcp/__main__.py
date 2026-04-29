import logging
import os

import uvicorn

from .server import build_app

logging.basicConfig(
    level=os.environ.get("OPENBRAIN_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main() -> None:
    host = os.environ.get("OPENBRAIN_HOST", "0.0.0.0")
    port = int(os.environ.get("OPENBRAIN_PORT", "8080"))
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
