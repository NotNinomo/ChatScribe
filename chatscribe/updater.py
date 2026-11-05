"""Self-update from GitHub Releases.

Each release publishes:
  update.json                      version, runtime id, file names, sizes, sha256
  ChatScribe-vX.Y.Z-app.zip        the app WITHOUT the NVIDIA runtime (~100 MB)
  ChatScribe-vX.Y.Z-windows.zip    everything (~1.5 GB) - for new installs or a new CUDA runtime

If the installed NVIDIA runtime matches the new one (same "runtime" id), only the small
app zip is downloaded. The update is unpacked next to the app, then a tiny script waits for
ChatScribe to close, copies the new files over the old ones (never touching config.json,
data/, models/ or lexicon/custom.txt) and starts ChatScribe again.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import APP_DIR, FROZEN, __version__

log = logging.getLogger(__name__)

REPO = "NotNinomo/ChatScribe"
API = os.environ.get("CHATSCRIBE_UPDATE_API",
                     f"https://api.github.com/repos/{REPO}/releases/latest")
UA = {"User-Agent": f"ChatScribe-Updater/{__version__}", "Accept": "application/vnd.github+json"}
BUNDLE_FILE = APP_DIR / "bundle.json"
STAGING = APP_DIR / "_update"
KEEP_FILES = ("config.json", "custom.txt")          # never overwritten
KEEP_DIRS = ("data", "models", "_update")


def parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def local_runtime() -> str:
    try:
        return json.loads(BUNDLE_FILE.read_text(encoding="utf-8")).get("runtime", "")
    except Exception:
        return ""


@dataclass
class UpdateInfo:
    version: str
    notes: str
    page_url: str
    download_url: str
    download_name: str
    size: int
    sha256: str
    full: bool            # True = full zip (runtime changed / unknown)


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check(current: str = __version__) -> UpdateInfo | None:
    """Return UpdateInfo if GitHub has a newer release, else None. Raises on network errors."""
    current = os.environ.get("CHATSCRIBE_VERSION_OVERRIDE", current)   # for testing
    rel = _get_json(API)
    tag = rel.get("tag_name", "")
    if parse_version(tag) <= parse_version(current):
        return None
    assets = {a["name"]: a for a in rel.get("assets", [])}
    manifest = {}
    if "update.json" in assets:
        try:
            manifest = _get_json(assets["update.json"]["browser_download_url"])
        except Exception as e:
            log.warning("update.json unreadable: %s", e)
    app_name, full_name = manifest.get("app"), manifest.get("full")
    if not full_name:    # older releases without a manifest
        full_name = next((n for n in assets if n.endswith("-windows.zip")), None)
    same_runtime = bool(manifest.get("runtime")) and manifest.get("runtime") == local_runtime()
    use_app = same_runtime and app_name in assets
    name = app_name if use_app else full_name
    if not name or name not in assets:
        log.warning("Release %s has no downloadable zip", tag)
        return None
    a = assets[name]
    sha = (manifest.get("sha256") or {}).get(name, "")
    return UpdateInfo(version=tag.lstrip("v"), notes=(rel.get("body") or "").strip(),
                      page_url=rel.get("html_url", f"https://github.com/{REPO}/releases"),
                      download_url=a["browser_download_url"], download_name=name,
                      size=int(a.get("size", 0)), sha256=sha, full=not use_app)


def download(info: UpdateInfo, progress=lambda done, total: None, cancelled=lambda: False) -> Path:
    """Stream the zip to a temp file, verifying size and sha256."""
    out = Path(tempfile.gettempdir()) / f"chatscribe-{info.download_name}"
    h = hashlib.sha256()
    req = urllib.request.Request(info.download_url, headers={"User-Agent": UA["User-Agent"]})
    done = 0
    with urllib.request.urlopen(req, timeout=30) as r, open(out, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.size or 0)
        while True:
            if cancelled():
                raise RuntimeError("cancelled")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total)
    if info.size and done != info.size:
        raise RuntimeError(f"download incomplete ({done} of {info.size} bytes)")
    if info.sha256 and h.hexdigest().lower() != info.sha256.lower():
        raise RuntimeError("download is corrupted (checksum mismatch)")
    return out


def stage(zip_path: Path, progress=lambda msg: None) -> Path:
    """Unpack into <app>/_update and return the folder that holds ChatScribe.exe."""
    if STAGING.exists():
        shutil.rmtree(STAGING, ignore_errors=True)
    STAGING.mkdir(parents=True)
    progress("Unpacking...")
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            p = Path(m.filename)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError(f"unsafe path in update: {m.filename}")
        z.extractall(STAGING)
    exe = next(STAGING.rglob("ChatScribe.exe"), None)
    if exe is None:
        raise RuntimeError("update zip has no ChatScribe.exe")
    try:
        zip_path.unlink()
    except OSError:
        pass
    return exe.parent


def install_and_restart(src: Path) -> None:
    """Hand off to a script that swaps files once this process exits, then relaunch."""
    if not FROZEN:
        raise RuntimeError("Running from source - update with: git pull")
    pid = os.getpid()
    app = APP_DIR
    xf = " ".join(f'"{n}"' for n in KEEP_FILES)
    xd = " ".join(f'"{d}"' for d in KEEP_DIRS)   # by name: data/, models/ are never copied
    script = Path(tempfile.gettempdir()) / f"chatscribe-update-{pid}.cmd"
    script.write_text(f"""@echo off
rem ChatScribe updater - waits for ChatScribe to close, copies the new version in, restarts it.
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)
robocopy "{src}" "{app}" /E /R:5 /W:1 /XF {xf} /XD {xd} /NFL /NDL /NJH /NJS /NP >"{app / 'data' / 'update.log'}"
if %ERRORLEVEL% GEQ 8 (
  echo Update copy failed with code %ERRORLEVEL% >>"{app / 'data' / 'update.log'}"
) else (
  rmdir /s /q "{STAGING}"
)
start "" "{app / 'ChatScribe.exe'}"
(goto) 2>nul & del "%~f0"
""", encoding="utf-8")
    _spawn_detached(f'cmd /c "{script}"')
    log.info("Update to be installed from %s after exit", src)


def _spawn_detached(cmdline: str) -> None:
    """Start a hidden process that is guaranteed to outlive ChatScribe.

    A plain child process dies with us if ChatScribe was started inside a Windows job
    (launchers, some terminals). Creating it through WMI makes it a child of the WMI
    service instead - outside any job. Falls back to a breakaway / detached Popen."""
    ps = ("$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
          "-Property @{ShowWindow=[uint16]0}; "
          "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
          "@{CommandLine=$env:CS_CMD; CurrentDirectory=$env:TEMP; ProcessStartupInformation=$si}; "
          "exit $r.ReturnValue")
    env = dict(os.environ, CS_CMD=cmdline)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           env=env, capture_output=True, timeout=30,
                           creationflags=0x08000000)            # CREATE_NO_WINDOW
        if r.returncode == 0:
            return
        log.warning("WMI launch failed (%s): %s", r.returncode, r.stderr[-300:])
    except Exception as e:
        log.warning("WMI launch failed: %s", e)
    base = 0x08000000 | 0x00000008 | 0x00000200   # NO_WINDOW | DETACHED | NEW_PROCESS_GROUP
    for flags in (base | 0x01000000, base):       # + CREATE_BREAKAWAY_FROM_JOB, then without
        try:
            subprocess.Popen(cmdline, creationflags=flags, close_fds=True,
                             cwd=tempfile.gettempdir())
            return
        except OSError as e:
            log.info("launch flags %#x failed: %s", flags, e)
    raise RuntimeError("couldn't start the update helper")


def can_self_update() -> bool:
    """Packaged exe, or a git checkout we can pull - either way 'Update now' just works."""
    return sys.platform == "win32" and (FROZEN or _is_git_checkout())


def _is_git_checkout() -> bool:
    return (APP_DIR / ".git").exists() and shutil.which("git") is not None


def update_source_and_restart(progress=lambda msg: None) -> None:
    """Running from source: git pull, reinstall requirements if they changed, restart."""
    def run(*cmd, timeout=600):
        r = subprocess.run(cmd, cwd=APP_DIR, capture_output=True, text=True, timeout=timeout,
                           creationflags=0x08000000)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or f"{cmd[0]} failed")
        return r.stdout

    req = APP_DIR / "requirements.txt"
    before = req.read_bytes() if req.exists() else b""
    progress("Downloading the new version (git pull)...")
    run("git", "pull", "--ff-only")
    if req.exists() and req.read_bytes() != before:
        progress("Installing new requirements...")
        run(sys.executable, "-m", "pip", "install", "-q", "-r", str(req), timeout=1800)
    progress("Restarting...")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(tempfile.gettempdir()) / f"chatscribe-restart-{os.getpid()}.cmd"
    script.write_text(f"""@echo off
:wait
tasklist /FI "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul && (timeout /t 1 /nobreak >nul & goto wait)
cd /d "{APP_DIR}"
start "" "{exe}" -m chatscribe
(goto) 2>nul & del "%~f0"
""", encoding="utf-8")
    _spawn_detached(f'cmd /c "{script}"')
