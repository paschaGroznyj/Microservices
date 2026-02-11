import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_DEFAULT_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | %(processName)s | %(message)s"
)
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
        logs_dir: Path,
        log_file: str = "parsers.log",
        level: str = "INFO",
        max_bytes: int = 10_000_000,
        backup_count: int = 5,
        also_console: bool = False,
        fmt: str = _DEFAULT_FORMAT,
        datefmt: str = _DEFAULT_DATEFMT,
) -> logging.Logger:
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("parser")

    if root.handlers:
        lvl = getattr(logging, level.upper(), logging.INFO)
        root.setLevel(lvl)
        for h in root.handlers:
            h.setLevel(lvl)
        return root

    lvl = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(lvl)
    root.propagate = False
    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    fh = RotatingFileHandler(
        filename=str(logs_dir / log_file),
        maxBytes=int(max_bytes),
        backupCount=int(backup_count),
        encoding="utf-8",
    )
    fh.setLevel(lvl)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    if also_console:
        sh = logging.StreamHandler()
        sh.setLevel(lvl)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)

    root.info("Логирование настроено: папка=%s файл=%s уровень=%s", logs_dir, log_file, level.upper())
    return root


def get_logger(name: str) -> logging.Logger:
    name = name.strip()
    if not name:
        return logging.getLogger("parser")
    if name == "parser" or name.startswith("parser."):
        return logging.getLogger(name)
    return logging.getLogger(f"parser.{name}")
