"""Small logging helper used by demo scripts."""

import logging


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given module name."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return logging.getLogger(name)
