"""Updater decision logic (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatscribe import updater  # noqa: E402

RELEASE = {
    "tag_name": "v9.0.0", "html_url": "https://example.invalid/r", "body": "notes",
    "assets": [
        {"name": "ChatScribe-v9.0.0-app.zip", "size": 100, "browser_download_url": "u/app"},
        {"name": "ChatScribe-v9.0.0-windows.zip", "size": 1000, "browser_download_url": "u/full"},
        {"name": "update.json", "size": 10, "browser_download_url": "u/manifest"},
    ],
}
MANIFEST = {"version": "9.0.0", "runtime": "abc", "app": "ChatScribe-v9.0.0-app.zip",
            "full": "ChatScribe-v9.0.0-windows.zip",
            "sha256": {"ChatScribe-v9.0.0-app.zip": "11", "ChatScribe-v9.0.0-windows.zip": "22"}}


def _fake(monkeypatch, runtime):
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=10: MANIFEST if "manifest" in url
                        else RELEASE)
    monkeypatch.setattr(updater, "local_runtime", lambda: runtime)
    monkeypatch.delenv("CHATSCRIBE_VERSION_OVERRIDE", raising=False)


def test_versions():
    assert updater.parse_version("v1.10.0") > updater.parse_version("1.9.9")
    assert updater.parse_version("1.2") == (1, 2, 0)


def test_same_runtime_downloads_small_app_zip(monkeypatch):
    _fake(monkeypatch, "abc")
    info = updater.check("1.0.0")
    assert info.download_name.endswith("-app.zip") and not info.full and info.sha256 == "11"


def test_new_runtime_downloads_full_zip(monkeypatch):
    _fake(monkeypatch, "old-runtime")
    info = updater.check("1.0.0")
    assert info.download_name.endswith("-windows.zip") and info.full


def test_up_to_date(monkeypatch):
    _fake(monkeypatch, "abc")
    assert updater.check("9.0.0") is None
