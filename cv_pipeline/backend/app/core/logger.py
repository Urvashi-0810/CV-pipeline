"""Simple shared logger: prints to console and writes data/state/pipeline.log."""
import logging
import sys

from app.core.config import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    settings.state_folder.mkdir(parents=True, exist_ok=True)
    fileh = logging.FileHandler(settings.state_folder / "pipeline.log", encoding="utf-8")
    fileh.setFormatter(fmt)
    logger.addHandler(fileh)
    logger.propagate = False
    return logger
