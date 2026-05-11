import uvicorn
from .http import app  # noqa: F401 — registers routes


def main() -> None:
    uvicorn.run(
        "services.eventbus.http:app",
        host="0.0.0.0",
        port=8082,
        log_level="info",
    )


if __name__ == "__main__":
    main()
