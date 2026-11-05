# PyInstaller spec - build with:  .venv\Scripts\python tools\build_exe.py
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
import site

sp = Path(next(p for p in site.getsitepackages() if p.endswith("site-packages")))

# NVIDIA runtime DLLs keep their nvidia/<lib>/bin layout; engine.py adds those dirs at startup.
nvidia_bins = []
for lib in ("cublas", "cudnn", "cuda_nvrtc"):
    for dll in (sp / "nvidia" / lib / "bin").glob("*.dll"):
        nvidia_bins.append((str(dll), f"nvidia/{lib}/bin"))

a = Analysis(
    ["tools/exe_entry.py"],
    pathex=["."],
    binaries=nvidia_bins + collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime"),
    datas=collect_data_files("faster_whisper") + collect_data_files("wordfreq")
          + collect_data_files("_sounddevice_data"),
    hiddenimports=collect_submodules("pynput") + ["pynput.keyboard._win32", "pynput.mouse._win32"],
    excludes=["torch", "matplotlib", "IPython", "pytest", "pandas", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ChatScribe", console=False,
          icon="assets/chatscribe.ico",
          version=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ChatScribe")
