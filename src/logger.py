"""
Konfigurasi logger untuk RX-0 Unicorn.

Menggunakan loguru untuk setup cepat dengan rotasi file dan output berwarna.
"""

import sys
from loguru import logger

from src.config import LOGS_DIR


def setup_logger(
    level: str = "INFO",
    log_file: str = "rx0_unicorn.log",
    rotation: str = "10 MB",
    retention: str = "14 days",
) -> "logger":  # type: ignore[valid-type]
    """
    Setup logger dengan console + file handlers.

    Args:
        level: Log level (DEBUG/INFO/WARNING/ERROR).
        log_file: Nama file log di direktori logs/.
        rotation: Ukuran rotasi file (e.g. "10 MB", "1 day").
        retention: Berapa lama log disimpan sebelum dihapus.

    Returns:
        Configured loguru logger.
    """
    # Hapus handler default
    logger.remove()

    # Console handler dengan warna
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=True,
        diagnose=False,
    )

    # File handler dengan rotasi
    log_path = LOGS_DIR / log_file
    logger.add(
        log_path,
        level=level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
        rotation=rotation,
        retention=retention,
        compression="zip",
        backtrace=True,
        diagnose=False,
        enqueue=True,  # thread-safe
    )

    return logger


# Default logger — modules cukup `from src.logger import logger`
logger = setup_logger()
