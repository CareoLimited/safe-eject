#!/usr/bin/env python3
"""
SafeEject: see what is keeping a macOS volume busy, close it, and eject the disk safely.

A single file that only uses the Python standard library. It runs as a CLI or
as a small dashboard served on 127.0.0.1.
"""

import argparse
import json
import os
import plistlib
import pwd
import re
import secrets
import signal
import subprocess
import sys
import time
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse, parse_qs

__version__ = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

SCAN_TIMEOUT = 10
PROCESS_EXIT_TIMEOUT = 3
ADMIN_PROMPT_TIMEOUT = 120
MAX_REQUEST_BYTES = 16 * 1024
MAX_FILES_PER_PROCESS = 50
# The API can terminate processes, so the server only ever binds to loopback.
LOCAL_HOST = "127.0.0.1"


class LockScanError(RuntimeError):
    """The scan did not complete; it must never be read as an empty lock list."""


# Keywords used to group processes by name
AI_PROCESSES = {
    "claude", "cursor", "cursor-agent", "ollama", "gemini", "chatgpt",
    "lmstudio", "lm studio", "jan", "copilot", "codex", "skycomputeruse", "openai",
    "antigravity"
}
DEV_PROCESSES = {
    "python", "python3", "node", "node_repl", "npm", "yarn", "pnpm", "bun", "deno",
    "git", "ruby", "go", "cargo", "rustc", "java", "docker", "dockerd",
    "code", "vscodium", "zed", "rg", "ripgrep", "make", "cmake", "swift",
    "language_server", "xcode"
}
TERMINAL_PROCESSES = {"zsh", "bash", "fish", "sh", "ssh", "iterm2", "terminal", "alacritty", "kitty", "warp"}
SYSTEM_PROCESSES = {
    "mds", "mds_stores", "mdworker", "mdworker_shared", "fseventsd",
    "finder", "quicklook", "diskarbitrationd", "kernel_task", "launchd",
    "coreservicesd", "securityd", "distnoted", "cfprefsd", "spotlight"
}
IGNORED_COMMANDS = {"lsof", "fuser", "safe_eject"}
# Ancestors that say nothing about who really started a process
LAUNCHER_PROCESSES = {"zsh", "bash", "fish", "sh", "login", "sudo", "tmux", "screen", "launchd"}

CATEGORY_LABELS = {
    "ai": "AI agent",
    "dev": "Developer tool",
    "terminal": "Terminal",
    "system": "macOS service",
    "app": "App",
}

DATABASE_SUFFIXES = ("-wal", "-shm", "-journal", ".db", ".sqlite", ".sqlite3")
CODE_SUFFIXES = (".dylib", ".so", ".node", ".bundle")
APP_BUNDLE_RE = re.compile(r"/([^/]+)\.app/")
# Apps people open themselves live here; other bundles are tools shipped as .app
INSTALLED_APP_RE = re.compile(r"^(/System/|(/Users/[^/]+)?/Applications/)")
VERSION_RE = re.compile(r"\d+(\.\d+)+")


def _matches_keyword(lowered: str, keyword: str) -> bool:
    """
    The keyword must start a word. Keywords shorter than 4 letters must match a
    whole word, so "go" doesn't catch Google Chrome and "sh" doesn't catch Photoshop.
    """
    end = "" if len(keyword) >= 4 else r"(?![a-z0-9])"
    return re.search(r"(?<![a-z0-9])" + re.escape(keyword) + end, lowered) is not None


def classify_process(name: str, pid: int) -> tuple[str, str, bool]:
    """
    Groups a process by its name.
    Returns: (category_id, category_label, is_system)
    """
    lowered = name.lower()
    if pid <= 100 or any(_matches_keyword(lowered, k) for k in SYSTEM_PROCESSES):
        return "system", CATEGORY_LABELS["system"], True

    for category, keywords in (("ai", AI_PROCESSES), ("dev", DEV_PROCESSES), ("terminal", TERMINAL_PROCESSES)):
        if any(_matches_keyword(lowered, k) for k in keywords):
            return category, CATEGORY_LABELS[category], False

    return "app", CATEGORY_LABELS["app"], False


def display_name(executable: str) -> str:
    """
    Turns an executable path into a readable process name. Tools installed as
    versioned binaries (such as ~/.local/share/claude/versions/2.1.3) are named
    after their folder instead of the version number. Login shells lose their "-".
    """
    base = os.path.basename(executable.rstrip("/")) or executable
    if VERSION_RE.fullmatch(base) and "/versions/" in executable:
        base = os.path.basename(executable.split("/versions/")[0]) or base
    return base.lstrip("-")


def app_name(executable: str) -> Optional[str]:
    """Returns the outermost .app bundle an executable belongs to, if any."""
    match = APP_BUNDLE_RE.search(executable)
    return match.group(1) if match else None


def get_process_table() -> dict[int, tuple[int, str]]:
    """Maps each PID to (parent PID, executable path) with a single ps call. Best effort."""
    try:
        res = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=SCAN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in res.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2].strip())
    return table


def describe_origin(pid: int, table: dict[int, tuple[int, str]]) -> dict:
    """
    Finds the app a process belongs to and the program that started it, so the
    user knows which window to close. Shells and launchers in between are skipped.
    """
    origin = {"app": None, "own_app": False, "parent": None}
    own_bundle = None
    seen = set()
    current = pid
    while current in table and current > 1 and current not in seen:
        seen.add(current)
        ppid, executable = table[current]
        bundle = app_name(executable)
        if current == pid:
            # Only a process named after its app (like "Cursor Helper" in Cursor) is
            # part of it. Tools shipped inside an app, like Xcode's Python, are not.
            name = display_name(executable).lower()
            own_bundle = bundle if bundle and name.startswith(bundle.lower()) else None
        elif bundle and bundle != own_bundle and INSTALLED_APP_RE.match(executable):
            # A tool shipped as its own .app (like Claude Code's CLI) still runs
            # inside another app, such as a terminal; that one is more useful.
            origin["app"] = bundle
            return origin
        elif (bundle is None or bundle != own_bundle) and origin["parent"] is None:
            name = display_name(executable)
            if name.lower() not in LAUNCHER_PROCESSES:
                origin["parent"] = name
        current = ppid
    if own_bundle:
        origin["app"], origin["own_app"] = own_bundle, True
    return origin


def lock_kind(fd: str, file_type: str, path: str, executable: str = "") -> str:
    """
    Names how a process uses a path, from lsof's file descriptor and type fields.
    macOS lsof marks both code and memory-mapped data files as "txt", so only the
    executable and libraries count as the program itself.
    """
    if fd in ("cwd", "rtd"):
        return "cwd"
    lowered = path.lower()
    if fd == "txt" and (path == executable or "/contents/macos/" in lowered
                        or ".framework/" in lowered or lowered.endswith(CODE_SUFFIXES)):
        return "program"
    if lowered.endswith(DATABASE_SUFFIXES):
        return "database"
    if fd in ("txt", "mem") or fd.startswith("mmap"):
        return "mapped"
    if file_type == "DIR":
        return "directory"
    return "file"


def get_username(uid_val: str) -> str:
    """Turns a UID into a user name."""
    try:
        return pwd.getpwuid(int(uid_val)).pw_name
    except Exception:
        return str(uid_val)


def get_volume_info(mount_point: str) -> Optional[dict]:
    """Reads volume details with diskutil info -plist."""
    try:
        cmd = ["diskutil", "info", "-plist", mount_point]
        res = subprocess.run(cmd, capture_output=True, check=True, timeout=SCAN_TIMEOUT)
        plist = plistlib.loads(res.stdout)

        total_size = plist.get("TotalSize") or plist.get("APFSContainerSize") or 0
        free_space = plist.get("FreeSpace") or plist.get("APFSContainerFree") or 0
        used_space = plist.get("CapacityInUse") or 0

        # Since Catalina the internal disk is split into a read-only system volume (/)
        # and a data volume (/System/Volumes/Data). Add the data volume's usage so the
        # total matches what Finder shows.
        if mount_point == "/" and os.path.exists("/System/Volumes/Data"):
            try:
                data_res = subprocess.run(["diskutil", "info", "-plist", "/System/Volumes/Data"],
                                          capture_output=True, timeout=SCAN_TIMEOUT)
                if data_res.returncode == 0:
                    data_plist = plistlib.loads(data_res.stdout)
                    data_used = data_plist.get("CapacityInUse", 0)
                    data_total = data_plist.get("APFSContainerSize", 0)
                    data_free = data_plist.get("APFSContainerFree", 0)

                    if data_total > 0:
                        total_size = data_total
                    if data_free > 0:
                        free_space = data_free

                    used_space = used_space + data_used
            except Exception:
                pass

        if used_space == 0 and total_size >= free_space:
            used_space = total_size - free_space

        used_percent = 0.0
        if total_size > 0:
            used_percent = round((used_space / total_size) * 100, 1)

        is_external = plist.get("Internal") is False or plist.get("BusProtocol") in {"USB", "Thunderbolt", "FireWire"}
        is_ejectable = plist.get("Ejectable", False)

        return {
            "name": plist.get("VolumeName") or os.path.basename(mount_point) or "Untitled",
            "mount_point": mount_point,
            "device": plist.get("DeviceIdentifier") or "",
            "device_node": plist.get("DeviceNode") or "",
            "fs_type": plist.get("FilesystemUserVisibleName") or plist.get("FilesystemName") or "APFS",
            "bus_protocol": plist.get("BusProtocol") or "Unknown",
            "total_size": total_size,
            "free_space": free_space,
            "used_space": used_space,
            "used_percent": used_percent,
            "is_external": is_external,
            "is_ejectable": is_ejectable,
            "is_apfs": "apfs" in str(plist.get("FilesystemPersonality", "")).lower(),
            "is_system_root": mount_point == "/",
        }
    except Exception:
        # Fall back to os.statvfs when diskutil fails
        try:
            st = os.statvfs(mount_point)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            pct = round((used / total) * 100, 1) if total > 0 else 0
            return {
                "name": os.path.basename(mount_point) or "Disk",
                "mount_point": mount_point,
                "device": "",
                "device_node": "",
                "fs_type": "Unknown",
                "bus_protocol": "External" if mount_point.startswith("/Volumes/") else "Internal",
                "total_size": total,
                "free_space": free,
                "used_space": used,
                "used_percent": pct,
                "is_external": mount_point.startswith("/Volumes/"),
                "is_ejectable": mount_point.startswith("/Volumes/"),
                "is_apfs": True,
                "is_system_root": mount_point == "/",
            }
        except Exception:
            return None


def list_all_volumes() -> list[dict]:
    """Lists every reachable volume mounted on this Mac."""
    volumes = []
    seen_mounts = set()

    # 1. Find volumes under /Volumes/ and the root volume in `mount` output
    try:
        res = subprocess.run(["mount"], capture_output=True, text=True, check=True)
        for line in res.stdout.splitlines():
            # Format: /dev/disk7s1 on /Volumes/MyDisk (apfs, ...)
            if " on " in line and " (" in line:
                try:
                    after_on = line.split(" on ", 1)[1]
                    mount_path = after_on.split(" (", 1)[0].strip()
                    if mount_path.startswith("/Volumes/") or mount_path == "/":
                        if mount_path not in seen_mounts and os.path.exists(mount_path):
                            info = get_volume_info(mount_path)
                            if info:
                                volumes.append(info)
                                seen_mounts.add(mount_path)
                except Exception:
                    continue
    except Exception as e:
        print(f"Couldn't read mount output: {e}", file=sys.stderr)

    # 2. Also look in /Volumes/ directly, when allowed
    volumes_dir = "/Volumes"
    if os.path.exists(volumes_dir):
        try:
            for entry in os.listdir(volumes_dir):
                full_path = os.path.join(volumes_dir, entry)
                if full_path not in seen_mounts and os.path.ismount(full_path):
                    info = get_volume_info(full_path)
                    if info:
                        volumes.append(info)
                        seen_mounts.add(full_path)
        except Exception:
            pass

    # 3. Add the system disk (/) if it wasn't found
    if "/" not in seen_mounts and os.path.exists("/"):
        root_info = get_volume_info("/")
        if root_info:
            volumes.append(root_info)

    # External disks first, the system disk last
    volumes.sort(key=lambda v: (not v.get("is_external", False), v.get("is_system_root", False), v["name"]))
    return volumes


def get_disk_locks(volume_path: str) -> list[dict]:
    """
    Finds every process holding files on the given volume, and how it holds them.
    Scans with lsof; raises LockScanError when the command fails or is incomplete.
    """
    norm_path = os.path.realpath(volume_path)
    if not os.path.exists(norm_path):
        raise LockScanError("The volume path doesn't exist or can't be read.")

    processes: dict[int, dict] = {}
    my_pid = os.getpid()
    prefix = norm_path.rstrip("/") + "/"

    # List open files, working directories, file types and descriptors (-F pcuftn).
    # cwd="/" keeps lsof itself from holding the disk and showing up as a ghost PID.
    try:
        cmd = ["lsof", "-F", "pcuftn", "+f", "--", norm_path]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd="/", timeout=SCAN_TIMEOUT)
        # lsof exits with 1 and an empty stderr when nothing matches.
        # Warnings can mean a partial scan, and a partial scan can't be trusted.
        if res.returncode not in (0, 1) or res.stderr.strip():
            raise LockScanError(res.stderr.strip() or f"lsof exited with code {res.returncode}")
        curr_pid: Optional[int] = None
        curr_fd = ""
        curr_type = ""

        for line in res.stdout.splitlines():
            if not line:
                continue
            tag = line[0]
            val = line[1:]

            if tag == "p":
                try:
                    curr_pid = int(val)
                except ValueError:
                    curr_pid = None
                    continue

                # The parent shell can be a real lock; only leave out our own process.
                if curr_pid == my_pid:
                    curr_pid = None
                    continue

                # Drop processes that have already exited (ghost PIDs)
                try:
                    os.kill(curr_pid, 0)
                except ProcessLookupError:
                    curr_pid = None
                    continue
                except PermissionError:
                    pass

                if curr_pid not in processes:
                    processes[curr_pid] = {"pid": curr_pid, "name": "", "user": "", "files": {}}
                curr_fd = curr_type = ""
            elif curr_pid is None:
                continue
            elif tag == "c":
                processes[curr_pid]["name"] = val
            elif tag == "u":
                processes[curr_pid]["user"] = get_username(val)
            elif tag == "f":
                curr_fd, curr_type = val, ""
            elif tag == "t":
                curr_type = val
            elif tag == "n":
                if val == norm_path or val.startswith(prefix):
                    processes[curr_pid]["files"].setdefault(val, (curr_fd, curr_type))

    except LockScanError:
        raise
    except Exception as e:
        raise LockScanError(f"The lock scan couldn't finish: {e}") from e

    table = get_process_table() if processes else {}
    results = []

    for pid, data in processes.items():
        # Make sure the process is still alive
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass

        executable = table.get(pid, (0, ""))[1]
        name = (display_name(executable) if executable else "") or data["name"] or f"PID {pid}"

        # Skip short-lived inspection commands
        if name.lower() in IGNORED_COMMANDS:
            continue

        cat_id, cat_label, is_system = classify_process(name, pid)
        paths = sorted(data["files"])
        kinds = {path: lock_kind(fd, file_type, path, executable) for path, (fd, file_type) in data["files"].items()}

        results.append({
            "pid": pid,
            "name": name,
            "executable": executable,
            "user": data["user"] or "n/a",
            "category_id": cat_id,
            "category_label": cat_label,
            "is_system": is_system,
            "origin": describe_origin(pid, table),
            "kinds": sorted(set(kinds.values())),
            "file_count": len(paths),
            "files": [{"path": p, "kind": kinds[p]} for p in paths[:MAX_FILES_PER_PROCESS]],
        })

    # AI agents and dev tools first, macOS services last
    priority = {"ai": 0, "dev": 1, "terminal": 2, "app": 3, "system": 4}
    results.sort(key=lambda x: (priority.get(x["category_id"], 99), -x["file_count"], x["name"]))
    return results


def wait_for_process_exit(pid: int) -> bool:
    """Waits until the process exits or the timeout runs out."""
    deadline = time.monotonic() + PROCESS_EXIT_TIMEOUT
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def terminate_process(pid: int, force: bool = False) -> tuple[bool, str]:
    """Terminates a single process."""
    if pid <= 1:
        return False, "The root system process (PID <= 1) can't be terminated."

    if pid == os.getpid():
        return False, "SafeEject can't terminate itself."

    try:
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        if not wait_for_process_exit(pid):
            return False, f"PID {pid} got the signal but didn't exit within {PROCESS_EXIT_TIMEOUT} seconds."
        how = "force quit" if force else "quit normally"
        return True, f"PID {pid} {how}."
    except ProcessLookupError:
        return True, f"PID {pid} isn't running anymore."
    except PermissionError:
        return False, f"Not allowed to terminate PID {pid}. It may need root."
    except Exception as e:
        return False, f"Couldn't terminate PID {pid}: {e}"


def terminate_all_locks(volume_path: str, force: bool = False, include_system: bool = False) -> dict:
    """Terminates every process holding the volume."""
    killed = []
    skipped = []
    failed = []

    try:
        if os.path.realpath(volume_path) == "/":
            raise LockScanError("Processes on the system disk can't be closed in bulk.")
        locks = get_disk_locks(volume_path)
    except LockScanError as e:
        return {
            "success": False, "stage": "scan_failed", "message": str(e),
            "killed_count": 0, "killed": [], "skipped_count": 0, "skipped": [],
            "failed_count": 0, "failed": [],
        }

    for item in locks:
        pid = item["pid"]
        if item["is_system"] and not include_system:
            skipped.append({"pid": pid, "name": item["name"], "reason": "macOS service"})
            continue

        success, msg = terminate_process(pid, force=force)
        if success:
            killed.append({"pid": pid, "name": item["name"], "msg": msg})
        else:
            failed.append({"pid": pid, "name": item["name"], "error": msg})

    return {
        "success": len(failed) == 0,
        "killed_count": len(killed),
        "killed": killed,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "failed_count": len(failed),
        "failed": failed,
    }


def safe_eject_volume(volume_path: str) -> dict:
    """
    1. Checks the target and any remaining locks.
    2. Flushes disk caches (sync).
    3. Runs diskutil eject, or diskutil unmount as a fallback.
    """
    norm_path = os.path.realpath(volume_path)

    if norm_path == "/" or not os.path.ismount(norm_path):
        return {"success": False, "stage": "invalid_volume", "message": "Pick a mounted external disk. The system disk can't be ejected."}

    # Don't run sync or diskutil until a lock scan has completed.
    try:
        remaining_locks = get_disk_locks(norm_path)
    except LockScanError as e:
        return {"success": False, "stage": "scan_failed", "message": str(e)}
    # macOS services such as Spotlight and fseventsd let go of a volume by
    # themselves when it is ejected, so only other processes block.
    blocking = [lock for lock in remaining_locks if not lock.get("is_system")]
    if blocking:
        return {
            "success": False,
            "stage": "locks_detected",
            "message": f"{len(blocking)} process(es) are still using the disk. Close them first.",
            "remaining_locks": blocking,
        }

    try:
        subprocess.run(["sync"], check=True, timeout=SCAN_TIMEOUT)
        # Try eject first
        cmd_eject = ["diskutil", "eject", norm_path]
        res = subprocess.run(cmd_eject, capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            return {
                "success": True,
                "message": f"'{volume_path}' was ejected. You can unplug it now.",
                "output": res.stdout.strip(),
            }

        # Fall back to unmount
        cmd_unmount = ["diskutil", "unmount", norm_path]
        res_un = subprocess.run(cmd_unmount, capture_output=True, text=True, timeout=30)
        if res_un.returncode == 0:
            return {
                "success": True,
                "message": f"'{volume_path}' was unmounted.",
                "output": res_un.stdout.strip(),
            }

        return {
            "success": False,
            "stage": "eject_failed",
            "message": f"Couldn't eject the disk: {res.stderr.strip() or res_un.stderr.strip() or 'unknown error'}",
        }
    except Exception as e:
        return {
            "success": False,
            "stage": "exception",
            "message": f"The eject command failed: {e}",
        }


def is_external_mount(path: str) -> bool:
    """True for a volume mounted under /Volumes/; the system disk never qualifies."""
    return path != "/" and path.startswith("/Volumes/") and os.path.ismount(path)


def get_spotlight_status(volume_path: str) -> Optional[bool]:
    """Returns whether Spotlight indexes the volume, or None when macOS doesn't say."""
    try:
        res = subprocess.run(["mdutil", "-s", volume_path], capture_output=True, text=True, timeout=SCAN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    output = res.stdout.lower()
    if "indexing enabled" in output:
        return True
    if "indexing disabled" in output:
        return False
    return None


# Runs mdutil through the standard macOS administrator password dialog.
# Arguments are passed via argv so the path is never part of the script text.
ADMIN_MDUTIL_SCRIPT = (
    "on run argv",
    'do shell script "/usr/bin/mdutil -i " & item 1 of argv & " " & quoted form of item 2 of argv'
    " with administrator privileges",
    "end run",
)


def set_spotlight(volume_path: str, enabled: bool) -> dict:
    """
    Turns Spotlight indexing on or off for an external volume. macOS usually
    needs an administrator for this, so a failed attempt is retried through
    the system password dialog.
    """
    norm_path = os.path.realpath(volume_path)
    if not is_external_mount(norm_path):
        return {"success": False, "message": "Spotlight can only be changed for a mounted external disk."}

    state = "on" if enabled else "off"
    try:
        res = subprocess.run(["mdutil", "-i", state, norm_path], capture_output=True, text=True, timeout=SCAN_TIMEOUT)
        if res.returncode != 0 or get_spotlight_status(norm_path) is not enabled:
            args = ["osascript"]
            for line in ADMIN_MDUTIL_SCRIPT:
                args += ["-e", line]
            res = subprocess.run(args + [state, norm_path], capture_output=True, text=True,
                                 timeout=ADMIN_PROMPT_TIMEOUT)
            if res.returncode != 0:
                if "-128" in res.stderr:
                    return {"success": False, "message": "Cancelled. Spotlight wasn't changed."}
                return {"success": False, "message": f"Couldn't change Spotlight: {res.stderr.strip() or 'unknown error'}"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"success": False, "message": f"Couldn't change Spotlight: {e}"}

    status = get_spotlight_status(norm_path)
    if status is not enabled:
        return {"success": False, "indexing": status, "message": "macOS didn't apply the Spotlight change."}
    return {"success": True, "indexing": status,
            "message": f"Spotlight indexing is now {state} for '{os.path.basename(norm_path)}'."}


def reveal_in_finder(path: str) -> dict:
    """Shows a file from a mounted external volume in Finder."""
    norm_path = os.path.realpath(path)
    if not norm_path.startswith("/Volumes/") or not os.path.exists(norm_path):
        return {"success": False, "message": "Only files on a mounted disk can be shown in Finder."}
    try:
        res = subprocess.run(["open", "-R", norm_path], capture_output=True, text=True, timeout=SCAN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return {"success": False, "message": f"Couldn't open Finder: {e}"}
    if res.returncode != 0:
        return {"success": False, "message": f"Couldn't open Finder: {res.stderr.strip() or 'unknown error'}"}
    return {"success": True, "message": f"Showing '{os.path.basename(norm_path)}' in Finder."}


def notify(title: str, message: str) -> None:
    """Shows a macOS notification. Best effort: failures are ignored."""
    script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
              "-e", "end run"]
    try:
        subprocess.run(["osascript", *script, title, message], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


class LiveBackend:
    """Runs real scans and commands on this Mac."""

    demo = False

    def volumes(self) -> list[dict]:
        return list_all_volumes()

    def locks(self, volume: str) -> list[dict]:
        return get_disk_locks(volume)

    def kill(self, pid: int, force: bool) -> tuple[bool, str]:
        return terminate_process(pid, force=force)

    def kill_all(self, volume: str, force: bool, include_system: bool) -> dict:
        return terminate_all_locks(volume, force=force, include_system=include_system)

    def eject(self, volume: str) -> dict:
        result = safe_eject_volume(volume)
        if result.get("success"):
            notify("SafeEject", result["message"])
        return result

    def spotlight_status(self, volume: str) -> Optional[bool]:
        return get_spotlight_status(os.path.realpath(volume))

    def set_spotlight(self, volume: str, enabled: bool) -> dict:
        return set_spotlight(volume, enabled)

    def reveal(self, path: str) -> dict:
        return reveal_in_finder(path)


def _demo_lock(pid, executable, user, origin, files):
    name = display_name(executable)
    cat_id, cat_label, is_system = classify_process(name, pid)
    return {
        "pid": pid, "name": name, "executable": executable, "user": user,
        "category_id": cat_id, "category_label": cat_label, "is_system": is_system,
        "origin": {"app": None, "own_app": False, "parent": None, **origin},
        "kinds": sorted({kind for _, kind in files}),
        "file_count": len(files),
        "files": [{"path": path, "kind": kind} for path, kind in files],
    }


class DemoBackend:
    """
    Serves made-up disks and processes, so the dashboard can be tried or
    screenshotted without touching any real disk or process.
    """

    demo = True
    # This process ignores a normal quit, to show the force quit step.
    STUBBORN_PIDS = {52110}

    def __init__(self):
        t7 = "/Volumes/T7 Shield"
        app = f"{t7}/projects/storefront"
        self._volumes = [
            {"name": "T7 Shield", "mount_point": t7, "fs_type": "APFS", "bus_protocol": "USB",
             "total_size": 1_000_204_886_016, "used_space": 612_400_000_000, "used_percent": 61.2,
             "is_external": True, "is_ejectable": True, "is_system_root": False},
            {"name": "Archive", "mount_point": "/Volumes/Archive", "fs_type": "ExFAT", "bus_protocol": "Thunderbolt",
             "total_size": 4_000_787_030_016, "used_space": 2_910_000_000_000, "used_percent": 72.7,
             "is_external": True, "is_ejectable": True, "is_system_root": False},
            {"name": "Macintosh HD", "mount_point": "/", "fs_type": "APFS", "bus_protocol": "Apple Fabric",
             "total_size": 494_384_795_648, "used_space": 301_200_000_000, "used_percent": 60.9,
             "is_external": False, "is_ejectable": False, "is_system_root": True},
        ]
        self._locks = {
            t7: [
                _demo_lock(48211, "/Users/alex/.local/share/claude/versions/2.1.3", "alex",
                           {"app": "iTerm"}, [(app, "cwd")]),
                _demo_lock(48377, "/opt/homebrew/bin/node", "alex", {"app": "iTerm", "parent": "claude"},
                           [(app, "cwd"), (f"{app}/node_modules/.vite/deps/_metadata.json", "file"),
                            (f"{app}/src", "directory")]),
                _demo_lock(51022, "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Plugin).app/"
                           "Contents/MacOS/Cursor Helper (Plugin)", "alex", {"app": "Cursor", "own_app": True},
                           [(f"{app}/.git", "directory"), (f"{app}/src", "directory")]),
                _demo_lock(52110, "/opt/homebrew/bin/python3.12", "alex", {"app": "iTerm"},
                           [(f"{t7}/projects/analytics/data/events.sqlite", "database"),
                            (f"{t7}/projects/analytics/data/events.sqlite-wal", "database")]),
                _demo_lock(47950, "-zsh", "alex", {"app": "Terminal"}, [(f"{t7}/photos/2026", "cwd")]),
                _demo_lock(389, "/System/Library/Frameworks/CoreServices.framework/Frameworks/Metadata.framework/"
                           "Support/mds_stores", "root", {}, [(f"{t7}/.Spotlight-V100/Store-V2/store.db", "database")]),
            ],
            "/Volumes/Archive": [],
            "/": [],
        }
        self._spotlight = {t7: True, "/Volumes/Archive": False}

    def _find_volume(self, volume: str) -> dict:
        for vol in self._volumes:
            if vol["mount_point"] == volume:
                return vol
        raise LockScanError("The volume path doesn't exist or can't be read.")

    def volumes(self) -> list[dict]:
        return [dict(v) for v in self._volumes]

    def locks(self, volume: str) -> list[dict]:
        self._find_volume(volume)
        locks = [dict(lock) for lock in self._locks.get(volume, [])]
        # Same order as a real scan
        priority = {"ai": 0, "dev": 1, "terminal": 2, "app": 3, "system": 4}
        locks.sort(key=lambda x: (priority[x["category_id"]], -x["file_count"], x["name"]))
        return locks

    def kill(self, pid: int, force: bool) -> tuple[bool, str]:
        if pid in self.STUBBORN_PIDS and not force:
            return False, f"PID {pid} got the signal but didn't exit within {PROCESS_EXIT_TIMEOUT} seconds."
        for locks in self._locks.values():
            for lock in locks:
                if lock["pid"] == pid:
                    locks.remove(lock)
                    return True, f"PID {pid} {'force quit' if force else 'quit normally'}."
        return True, f"PID {pid} isn't running anymore."

    def kill_all(self, volume: str, force: bool, include_system: bool) -> dict:
        try:
            locks = self.locks(volume)
        except LockScanError as e:
            return {"success": False, "stage": "scan_failed", "message": str(e), "killed_count": 0, "killed": [],
                    "skipped_count": 0, "skipped": [], "failed_count": 0, "failed": []}
        killed, skipped, failed = [], [], []
        for lock in locks:
            if lock["is_system"] and not include_system:
                skipped.append({"pid": lock["pid"], "name": lock["name"], "reason": "macOS service"})
                continue
            success, msg = self.kill(lock["pid"], force)
            if success:
                killed.append({"pid": lock["pid"], "name": lock["name"], "msg": msg})
            else:
                failed.append({"pid": lock["pid"], "name": lock["name"], "error": msg})
        return {"success": not failed, "killed_count": len(killed), "killed": killed,
                "skipped_count": len(skipped), "skipped": skipped, "failed_count": len(failed), "failed": failed}

    def eject(self, volume: str) -> dict:
        try:
            vol = self._find_volume(volume)
        except LockScanError as e:
            return {"success": False, "stage": "invalid_volume", "message": str(e)}
        if vol["is_system_root"]:
            return {"success": False, "stage": "invalid_volume", "message": "The system disk can't be ejected."}
        blocking = [lock for lock in self._locks.get(volume, []) if not lock["is_system"]]
        if blocking:
            return {"success": False, "stage": "locks_detected", "remaining_locks": blocking,
                    "message": f"{len(blocking)} process(es) are still using the disk. Close them first."}
        self._volumes.remove(vol)
        self._locks.pop(volume, None)
        return {"success": True, "message": f"'{volume}' was ejected. You can unplug it now."}

    def spotlight_status(self, volume: str) -> Optional[bool]:
        return self._spotlight.get(volume)

    def set_spotlight(self, volume: str, enabled: bool) -> dict:
        if volume not in self._spotlight:
            return {"success": False, "message": "Spotlight can only be changed for a mounted external disk."}
        self._spotlight[volume] = enabled
        return {"success": True, "indexing": enabled,
                "message": f"Spotlight indexing is now {'on' if enabled else 'off'} for '{os.path.basename(volume)}'."}

    def reveal(self, path: str) -> dict:
        return {"success": True, "message": "Demo mode: nothing to show in Finder."}


class SafeEjectHandler(SimpleHTTPRequestHandler):
    """Serves the local API and the static dashboard."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def _validate_source(self) -> bool:
        """Checks Host, Origin and the browser's fetch site against the server address."""
        host = self.headers.get("Host", "")
        try:
            authority = urlparse("//" + host)
            hostname = authority.hostname
            port = authority.port or 80
            if (not hostname or authority.username is not None or authority.path
                    or authority.query or authority.fragment
                    or hostname.lower() not in self.server.allowed_hosts
                    or port != self.server.server_address[1]):
                raise ValueError("Invalid Host")
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                if (parsed.scheme != "http" or parsed.netloc.lower() != host.lower()
                        or parsed.path or parsed.query or parsed.fragment
                        or parsed.username is not None):
                    raise ValueError("Invalid Origin")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ValueError("Cross-site request")
        except (ValueError, AttributeError):
            self._send_json({"error": "Requests from this origin aren't allowed."}, status=403)
            return False
        return True

    def _send_json(self, data: Union[dict, list], status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Never let the browser cache anything: after an update, or when another
        # version used this port before, stale scripts would mix old and new UI.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    @staticmethod
    def _valid_path(value) -> bool:
        return isinstance(value, str) and bool(value.strip()) and "\x00" not in value

    def do_OPTIONS(self):
        if not self._validate_source():
            return
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if not self._validate_source():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        backend = self.server.backend

        if path == "/api/session":
            self._send_json({"token": self.server.csrf_token})
            return

        elif path == "/api/volumes":
            volumes = backend.volumes()
            self._send_json({"volumes": volumes, "count": len(volumes)})
            return

        elif path == "/api/locks":
            volume = query.get("volume", [""])[0]
            if not self._valid_path(volume):
                self._send_json({"error": "The volume parameter is required."}, status=400)
                return
            try:
                locks = backend.locks(volume)
            except LockScanError as e:
                self._send_json({"error": str(e), "stage": "scan_failed"}, status=503)
                return
            self._send_json({"volume": volume, "locks": locks, "count": len(locks)})
            return

        elif path == "/api/spotlight":
            volume = query.get("volume", [""])[0]
            if not self._valid_path(volume):
                self._send_json({"error": "The volume parameter is required."}, status=400)
                return
            self._send_json({"volume": volume, "indexing": backend.spotlight_status(volume)})
            return

        elif path == "/api/status":
            self._send_json({"status": "running", "version": __version__, "demo": backend.demo})
            return

        # Serve static files (web/index.html and so on)
        return super().do_GET()

    def do_POST(self):
        if not self._validate_source():
            return
        token = self.headers.get("X-SafeEject-Token", "")
        if not secrets.compare_digest(token.encode('utf-8'), self.server.csrf_token.encode('utf-8')):
            self._send_json({"error": "Invalid session token. Reload the page."}, status=403)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        backend = self.server.backend

        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._send_json({"error": "Content-Type must be application/json."}, status=415)
            return
        if self.headers.get("Transfer-Encoding"):
            self._send_json({"error": "Transfer-Encoding isn't supported."}, status=400)
            return
        try:
            content_len = int(self.headers.get("Content-Length", "0"))
            if content_len <= 0:
                raise ValueError("Invalid Content-Length")
            if content_len > MAX_REQUEST_BYTES:
                self._send_json({"error": "The request body is too large."}, status=413)
                return
            post_data = self.rfile.read(content_len)
            if len(post_data) != content_len:
                raise ValueError("Incomplete request body")
            payload = json.loads(post_data.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("A JSON object is required")
        except (ValueError, RecursionError):
            self._send_json({"error": "A valid JSON object and Content-Length are required."}, status=400)
            return

        for flag in ("force", "include_system", "enabled"):
            if flag in payload and type(payload[flag]) is not bool:
                self._send_json({"error": f"{flag} must be a boolean."}, status=400)
                return

        if path == "/api/kill":
            pid = payload.get("pid")
            force = payload.get("force", False)
            if type(pid) is not int or pid <= 1:
                self._send_json({"error": "Invalid or missing PID."}, status=400)
                return
            success, msg = backend.kill(pid, force)
            self._send_json({"success": success, "message": msg}, status=200 if success else 400)
            return

        elif path == "/api/kill_all":
            volume = payload.get("volume")
            if not self._valid_path(volume):
                self._send_json({"error": "The volume parameter is required."}, status=400)
                return
            result = backend.kill_all(volume, payload.get("force", False), payload.get("include_system", False))
            self._send_json(result)
            return

        elif path == "/api/eject":
            volume = payload.get("volume")
            if not self._valid_path(volume):
                self._send_json({"error": "The volume parameter is required."}, status=400)
                return
            result = backend.eject(volume)
            self._send_json(result, status=200 if result.get("success") else 400)
            return

        elif path == "/api/spotlight":
            volume = payload.get("volume")
            if not self._valid_path(volume) or "enabled" not in payload:
                self._send_json({"error": "The volume and enabled parameters are required."}, status=400)
                return
            result = backend.set_spotlight(volume, payload["enabled"])
            self._send_json(result, status=200 if result.get("success") else 400)
            return

        elif path == "/api/reveal":
            target = payload.get("path")
            if not self._valid_path(target):
                self._send_json({"error": "The path parameter is required."}, status=400)
                return
            result = backend.reveal(target)
            self._send_json(result, status=200 if result.get("success") else 400)
            return

        self._send_json({"error": "Not found."}, status=404)

    def log_message(self, format, *args):
        # Quiet mode: don't flood the console with request logs
        pass


KIND_LABELS = {
    "cwd": "working directory",
    "program": "runs from disk",
    "mapped": "mapped in memory",
    "database": "open database",
    "directory": "open folder",
    "file": "open file",
}


def confirm(prompt: str) -> bool:
    """Asks a yes/no question in the terminal; refuses when there is no terminal."""
    if not sys.stdin.isatty():
        print("Can't ask for confirmation without a terminal. Add --yes to skip it.", file=sys.stderr)
        return False
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def format_origin(origin: dict) -> str:
    """Describes where a process came from, such as "started by claude, in iTerm"."""
    parts = []
    if origin.get("parent"):
        parts.append(f"started by {origin['parent']}")
    if origin.get("app") and not origin.get("own_app"):
        parts.append(f"in {origin['app']}")
    return ", ".join(parts)


def run_cli(args) -> int:
    """Runs a terminal command without the dashboard; returns the exit code."""
    if args.list:
        vols = list_all_volumes()
        print(f"\nMounted volumes ({len(vols)}):")
        print("-" * 60)
        for v in vols:
            tag = "[external]" if v["is_external"] else "[internal]"
            print(f"• {v['name']:<18} {tag:<10} {v['fs_type']:<6} {v['used_percent']:>5}%  -> {v['mount_point']}")
        print()
        return 0

    target = args.scan or args.eject or args.kill_all
    if target:
        try:
            locks = get_disk_locks(target)
        except LockScanError as e:
            print(f"Lock scan failed: {e}", file=sys.stderr)
            return 1
        print(f"\n'{target}': {len(locks)} process(es) holding the disk")
        print("-" * 65)
        if not locks:
            print("Nothing is holding this disk. It's safe to eject.")
        else:
            for item in locks:
                origin = format_origin(item["origin"])
                print(f"[{item['pid']}] {item['name']} ({item['user']}) · {item['category_label']}"
                      + (f" · {origin}" if origin else ""))
                for f in item["files"][:3]:
                    print(f"     ↳ {KIND_LABELS.get(f['kind'], f['kind']):<18} {f['path']}")
                if item["file_count"] > 3:
                    print(f"     ↳ ...and {item['file_count'] - 3} more")
        print()

        exit_code = 0
        if args.kill_all and locks:
            if not args.yes and not confirm("Close these processes? [y/N] "):
                print("Cancelled.")
                return 1
            print("Closing processes...")
            res = terminate_all_locks(target, force=args.force)
            print(f"Closed: {res['killed_count']}, skipped: {res['skipped_count']}, failed: {res['failed_count']}")
            if not res["success"]:
                exit_code = 1

        if args.eject:
            print("Ejecting the disk...")
            res = safe_eject_volume(target)
            print(res["message"])
            if res["success"]:
                notify("SafeEject", res["message"])
            else:
                exit_code = 1
        return exit_code
    return 0


def main():
    parser = argparse.ArgumentParser(description="SafeEject: see what keeps a macOS disk busy and eject it safely.")
    parser.add_argument("--port", type=int, default=5005, help="Dashboard port (default: 5005)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser on start")
    parser.add_argument("--demo", action="store_true", help="Run the dashboard with made-up disks and processes")
    parser.add_argument("--list", action="store_true", help="List mounted volumes")
    parser.add_argument("--scan", type=str, metavar="VOLUME", help="Show the processes holding a volume")
    parser.add_argument("--kill-all", type=str, metavar="VOLUME", help="Close the processes holding a volume")
    parser.add_argument("--eject", type=str, metavar="VOLUME", help="Eject a volume safely")
    parser.add_argument("--force", action="store_true", help="Force quit (SIGKILL) with --kill-all")
    parser.add_argument("--yes", "-y", action="store_true", help="Don't ask for confirmation with --kill-all")
    parser.add_argument("--version", action="version", version=f"SafeEject {__version__}")

    args = parser.parse_args()

    # Terminal commands run without the dashboard
    if args.list or args.scan or args.eject or args.kill_all:
        if args.demo:
            parser.error("--demo only works with the dashboard")
        raise SystemExit(run_cli(args))

    # Dashboard. Only change the working directory when serving, not on import:
    # a server standing in its own folder would hold the disk it lives on.
    os.chdir("/")
    port = args.port
    try:
        server = ThreadingHTTPServer((LOCAL_HOST, port), SafeEjectHandler)
    except OSError as e:
        print(f"Couldn't open port {port} ({e.strerror}). Try another one: --port {port + 1}", file=sys.stderr)
        raise SystemExit(1)
    server.csrf_token = secrets.token_urlsafe(32)
    server.allowed_hosts = {LOCAL_HOST, "localhost"}
    server.backend = DemoBackend() if args.demo else LiveBackend()
    url = f"http://{LOCAL_HOST}:{port}"
    print("\n" + "=" * 55)
    print(f"  SafeEject {__version__}" + (" (demo mode)" if args.demo else ""))
    print(f"  Dashboard: {url}")
    print("  Press Ctrl+C to stop")
    print("=" * 55 + "\n")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSafeEject stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
