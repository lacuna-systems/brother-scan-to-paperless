"""Best-effort daemon logging."""

from datetime import datetime


def log(msg: str, log_file: str | None = None) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except (OSError, UnicodeError, ValueError):
        pass
    if log_file:
        try:
            with open(log_file, "a") as f:
                f.write(line + "\n")
        except (OSError, UnicodeError, ValueError):
            pass
