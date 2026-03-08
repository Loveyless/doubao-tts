import uvicorn

from service.config import get_service_config


def main() -> None:
    config = get_service_config()
    uvicorn.run(
        "service.app:app",
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    main()

