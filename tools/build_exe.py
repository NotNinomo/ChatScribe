"""Build a self-contained ChatScribe folder:  dist/ChatScribe/ChatScribe.exe

    .venv\\Scripts\\python tools\\build_exe.py            (bundles the speech model: works offline)
    .venv\\Scripts\\python tools\\build_exe.py --no-model (smaller; downloads the model on first run)
    .venv\\Scripts\\python tools\\build_exe.py --zip      (also writes dist/ChatScribe.zip)

Nothing personal is copied: config.json, calibration and learned words start fresh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "ChatScribe"
MODEL = "large-v3-turbo"
HF_REPO = "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"

sys.path.insert(0, str(ROOT))
from chatscribe import __version__ as VERSION  # noqa: E402


def make_icon(path: Path) -> None:
    """Gold quill-ish 'C' on a dark Classic-style plate. Needs Pillow (build-time only)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pillow"], check=True)
        from PIL import Image, ImageDraw, ImageFont
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=44, fill=(28, 19, 11, 255),
                        outline=(201, 165, 74, 255), width=10)
    d.rounded_rectangle((26, 26, size - 26, size - 26), radius=30, outline=(90, 67, 37, 255),
                        width=4)
    try:
        font = ImageFont.truetype("georgiab.ttf", 170)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2 + 4), "C", font=font, fill=(255, 209, 0, 255), anchor="mm")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                          (256, 256)])


def find_model() -> Path | None:
    local = ROOT / "models" / MODEL
    if (local / "model.bin").is_file():
        return local
    snaps = ROOT / "models" / "hf" / "hub" / HF_REPO / "snapshots"
    for snap in sorted(snaps.glob("*")) if snaps.is_dir() else []:
        if (snap / "model.bin").is_file():
            return snap
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()

    icon = ROOT / "assets" / "chatscribe.ico"
    if not icon.exists():
        make_icon(icon)

    print("== PyInstaller ==")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
                    str(ROOT / "ChatScribe.spec")], check=True, cwd=ROOT)

    print("== App files ==")
    # Never ship anyone's personal word list.
    shutil.copytree(ROOT / "lexicon", DIST / "lexicon", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("custom.txt"))
    shutil.copy2(ROOT / "README.md", DIST / "README.md")
    shutil.copy2(ROOT / "LICENSE", DIST / "LICENSE.txt")
    (DIST / "data").mkdir(exist_ok=True)
    for stale in ("config.json", "lexicon/custom.txt"):
        (DIST / stale).unlink(missing_ok=True)
    runtime = runtime_id()
    (DIST / "bundle.json").write_text(json.dumps({"version": VERSION, "runtime": runtime},
                                                 indent=2), encoding="utf-8")

    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 1e9
    print(f"\nBuilt {DIST / 'ChatScribe.exe'}  ({size:.1f} GB folder)")

    if args.zip:
        make_release(runtime)


def runtime_id() -> str:
    """Fingerprint of the bundled NVIDIA runtime. If a new release has the same id, the
    updater downloads only the small app zip instead of 1.5 GB again."""
    nv = DIST / "_internal" / "nvidia"
    h = hashlib.sha256()
    for f in sorted(nv.rglob("*")) if nv.is_dir() else []:
        if f.is_file():
            h.update(f"{f.relative_to(nv).as_posix()}:{f.stat().st_size};".encode())
    return h.hexdigest()[:16]


def _zip(out: Path, include) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(DIST.rglob("*")):
            rel = f.relative_to(DIST)
            if f.is_file() and include(rel):
                z.write(f, Path("ChatScribe") / rel)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_release(runtime: str) -> None:
    """dist/ChatScribe-vX-windows.zip (full), dist/ChatScribe-vX-app.zip (no NVIDIA runtime),
    dist/update.json (manifest the in-app updater reads)."""
    print("== Release zips ==")
    out_dir = ROOT / "dist"

    def personal_or_model(rel: Path) -> bool:
        top = rel.parts[0]
        return top in ("models", "data") or rel.name in ("config.json", "custom.txt")

    full = out_dir / f"ChatScribe-v{VERSION}-windows.zip"
    app = out_dir / f"ChatScribe-v{VERSION}-app.zip"
    _zip(full, lambda rel: not personal_or_model(rel))
    _zip(app, lambda rel: not personal_or_model(rel)
         and rel.parts[:2] != ("_internal", "nvidia"))
    manifest = {
        "version": VERSION, "runtime": runtime,
        "full": full.name, "app": app.name,
        "sha256": {full.name: _sha256(full), app.name: _sha256(app)},
    }
    (out_dir / "update.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for f in (full, app):
        print(f"   {f.name}  {f.stat().st_size / 1e6:,.0f} MB")
    print("   update.json")


if __name__ == "__main__":
    main()
