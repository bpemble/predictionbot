import os
import sys
from loguru import logger


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | "
                      "<cyan>{name}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>")
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logger.add(
        os.path.join(log_dir, "bot_{time:YYYY-MM-DD}.log"),
        level=level,
        rotation="00:00",
        retention="30 days",
        compression="gz",
    )


def get_logger(name: str):
    return logger.bind(name=name)
