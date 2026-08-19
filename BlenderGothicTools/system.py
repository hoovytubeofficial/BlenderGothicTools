from .log import klog as print  # route console output through the add-on's log tag
import datetime
import json
import os
import platform
import subprocess
from pathlib import Path

MACHINE: str = platform.machine().upper()
"""Machine/CPU architecture"""

PYTHON_VERSION: str = platform.python_version()
"""Version of the running Python executable"""

SYSTEM: str = platform.system().upper()
"""System variant"""

PLUGIN_ROOT: Path = Path(os.path.dirname(os.path.realpath(__file__)))
"""Path to the root directory of the plugin"""

# Folders starting with "_" hold things the USER looks at or we generate at runtime;
# everything else in the add-on folder is code.
LOG_DIR: Path = PLUGIN_ROOT / "_Logs"
"""Session logs and diagnostics reports"""

SAMPLES_DIR: Path = PLUGIN_ROOT / "_Samples"
"""One small example of each supported format, for troubleshooting"""

CACHE_DIR: Path = PLUGIN_ROOT / "_Cache"
"""Generated files (converted textures)"""

ESSEMBLE_DIR: Path = PLUGIN_ROOT / "_Essemble"
"""Saved character recipes (.json), written and read by the Essemble panel"""

LOG_FILE_NAME: str = f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_KRX.log"
"""Filename of the current log for this Blender run time"""


def _obfuscation_map():
    mapping = {str(PLUGIN_ROOT): f"...{os.sep}PLUGIN_ROOT"}
    try:
        mapping[os.getlogin()] = "USER-LOGIN"
    except OSError:
        pass
    return mapping


OBFUSCATION_MAP = _obfuscation_map()
"""When logging to file hide these keys with values"""


def format_json_for_logging(data: dict) -> str:
    # Do not modify the reference, make a copy
    local = {**data}

    # Obfuscate paths
    for key, value in local.items():
        if "path" in key.lower() or "file" in key.lower() and isinstance(value, str):
            safe_to_show_parent = Path(value).parent.parent.parent.parent.parent
            relative_path = str(Path(value).relative_to(safe_to_show_parent))
            local[key] = "..." + relative_path

    return json.dumps(local, default=vars, indent=2, sort_keys=True, ensure_ascii=False)


def write_log(content: str, mode: str = "a", filename: str = LOG_FILE_NAME):
    if content:
        for key, value in OBFUSCATION_MAP.items():
            content = content.replace(key, value)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / filename, mode) as file:
        file.write(content)
        if mode != "w":
            file.write("\n#---#\n")


def prune_logs(root: Path = None):
    root = root or LOG_DIR
    if not root.is_dir():
        return

    # tidy away logs older versions dropped in the add-on root
    for stray in PLUGIN_ROOT.glob("*.log"):
        try:
            stray.replace(root / stray.name)
        except OSError:
            pass

    logs = []
    for path in root.iterdir():
        if path.suffix.lower() == ".log":
            logs.append(path)

    # Descending order based on name
    logs = sorted(logs, key=lambda p: p.name, reverse=True)

    while len(logs) > 7:
        path = logs.pop()
        print(f"Removing {path.name}")
        path.unlink()


def open_plugin_directory():
    if SYSTEM == "WINDOWS":
        args = ["explorer"]
    elif SYSTEM == "LINUX":
        args = ["xdg-open"]  # TODO Linux not tested
    elif SYSTEM == "DARWIN":
        args = ["open"]  # TODO macOS not tested
    else:
        return

    args.append(str(PLUGIN_ROOT))
    subprocess.Popen(args, shell=True)
