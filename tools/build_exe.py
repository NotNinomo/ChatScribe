"""Build a self-contained ChatScribe folder:  dist/ChatScribe/ChatScribe.exe

    .venv\\Scripts\\python tools\\build_exe.py            (bundles the speech model: works offline)
    .venv\\Scripts\\python tools\\build_exe.py --no-model (smaller; downloads the model on first run)
    .venv\\Scripts\\python tools\\build_exe.py --zip      (also writes dist/ChatScribe.zip)

Nothing personal is copied: config.json, calibration and learned words start fresh.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "ChatScribe"
MODEL = "large-v3-turbo"
HF_REPO = "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"


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
    shutil.copytree(ROOT / "lexicon", DIST / "lexicon", dirs_exist_ok=True)
    shutil.copy2(ROOT / "README.md", DIST / "README.md")
    (DIST / "data").mkdir(exist_ok=True)
    for stale in ("config.json",):
        (DIST / stale).unlink(missing_ok=True)

    if not args.no_model:
        src = find_model()
        if src is None:
            sys.exit("Speech model not found - run ChatScribe once from source first, "
                     "or build with --no-model.")
        dst = DIST / "models" / MODEL
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            # HF cache entries can be links into blobs/ - copy the real file contents.
            target = dst / f.name
            if not target.exists() or target.stat().st_size != f.resolve().stat().st_size:
                print("   model:", f.name)
                shutil.copyfile(f.resolve(), target)

    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 1e9
    print(f"\nBuilt {DIST / 'ChatScribe.exe'}  ({size:.1f} GB folder)")

    if args.zip:
        print("== Zipping ==")
        out = shutil.make_archive(str(ROOT / "dist" / "ChatScribe"), "zip", DIST.parent,
                                  "ChatScribe")
        print("Wrote", out)


if __name__ == "__main__":
    main()
