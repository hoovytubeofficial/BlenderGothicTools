# log.py: one console format for the whole add-on, plus a scrapeable buffer.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# Every module writes through here, so the console can be filtered on a single tag:
#
#   GOTHIC TOOLS | INFO  | some message
#   GOTHIC TOOLS | WARN  | something looked odd
#   GOTHIC TOOLS | ERROR | something failed
#
# Modules opt in with `from .log import klog as print`, which routes their existing
# print() calls through this function without touching the call sites.
# Everything is also kept in a buffer so the Developer tab can hand the user a
# complete report to paste into a bug report.
# -------------------------------------------------------------------------------------------------------
import datetime

LOG_TAG = "GOTHIC TOOLS"
MAX_LINES = 8000

_buffer = []
_stdout_print = print

DEVELOPER = False
"""Verbose mode. DEBUG lines are always buffered for the report, but only printed to
the console when this is on - so the report stays useful without flooding the console."""


def set_developer(enabled: bool):
    global DEVELOPER
    DEVELOPER = bool(enabled)
    klog(f"developer mode {'ON - verbose console output' if DEVELOPER else 'off'}")


def debug(*args, **kwargs):
    """Detail that is always kept for the diagnostics report, printed only in dev mode."""
    text = " ".join(str(arg) for arg in args).rstrip()
    if not text:
        return
    for raw_line in text.splitlines():
        line = f"{LOG_TAG} | DEBUG | {raw_line}"
        _buffer.append(line)
        if DEVELOPER:
            _stdout_print(line, flush=kwargs.get("flush", False))
    if len(_buffer) > MAX_LINES:
        del _buffer[: len(_buffer) - MAX_LINES]


def klog(*args, level="INFO", **kwargs):
    """print()-compatible logger. Every line is tagged so the console can be scraped."""
    text = " ".join(str(arg) for arg in args).rstrip()
    if not text:
        return

    for raw_line in text.splitlines():
        line = f"{LOG_TAG} | {level:<5} | {raw_line}"
        _buffer.append(line)
        _stdout_print(line, flush=kwargs.get("flush", False))

    if len(_buffer) > MAX_LINES:
        del _buffer[: len(_buffer) - MAX_LINES]


def info(*args, **kwargs):
    klog(*args, level="INFO", **kwargs)


def warn(*args, **kwargs):
    klog(*args, level="WARN", **kwargs)


def error(*args, **kwargs):
    klog(*args, level="ERROR", **kwargs)


def exception(prefix: str = ""):
    """Log the traceback of the exception being handled, so reports show HOW it broke."""
    import traceback

    text = traceback.format_exc().rstrip()
    if prefix:
        klog(prefix, level="ERROR")
    klog(text, level="ERROR")


def errors():
    return [line for line in _buffer if "| ERROR |" in line or "| WARN  |" in line]


def dump_to(path) -> bool:
    """Write the whole buffer to a file (the session log)."""
    from pathlib import Path

    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(_buffer), encoding="utf8")
        return True
    except OSError:
        return False


def lines():
    return list(_buffer)


def clear():
    _buffer.clear()


def report_text(header_lines=()) -> str:
    """Diagnostics blob: environment, everything that went wrong, then the whole log."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = [
        "=" * 78,
        f"{LOG_TAG} diagnostics report - {stamp}",
        "=" * 78,
        "",
        "ENVIRONMENT",
        "-" * 78,
    ]
    out.extend(header_lines)

    problems = errors()
    out += ["", f"WARNINGS AND ERRORS ({len(problems)})", "-" * 78]
    out.extend(problems if problems else ["(none - nothing was logged as a problem)"])

    out += ["", f"FULL CONSOLE LOG ({len(_buffer)} lines)", "-" * 78]
    out.extend(_buffer if _buffer else ["(empty - nothing has been imported yet this session)"])
    out += ["", "=" * 78, "end of report", "=" * 78]
    return "\n".join(out)
