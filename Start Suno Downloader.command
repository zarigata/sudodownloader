#!/bin/bash
# ---------------------------------------------------------------
#  Suno Playlist Downloader — macOS launcher
#  Double-click this file. First run installs what it needs.
#  (If macOS asks: right-click the file → Open → Open.)
# ---------------------------------------------------------------
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
export SUNODL_NO_PAUSE=1

PY="$(command -v python3)"
if [ -z "$PY" ]; then
    # python3 ships with the Xcode Command Line Tools
    echo
    echo " [!] Python 3 isn't installed yet."
    echo "     A system dialog will offer to install it — click Install,"
    echo "     then double-click this file again."
    echo
    xcode-select --install 2>/dev/null
    read -r -p "Press Enter to close…" _
    exit 1
fi

# First run: quietly install the two needed packages
if ! "$PY" -c 'import requests, rich, cryptography' >/dev/null 2>&1; then
    echo
    echo " First run — setting things up one time only…"
    echo
    "$PY" -m pip install --user --quiet -r requirements.txt || {
        echo " [!] Could not install packages automatically."
        echo "     Run this in Terminal, then double-click this file again:"
        echo "        python3 -m pip install --user requests rich cryptography"
        read -r -p "Press Enter to close…" _
        exit 1
    }
fi

# When double-clicked (no arguments): save music to Music/Suno Downloads
# so it also works when run straight from a mounted .dmg (read-only).
if [ $# -eq 0 ]; then
    set -- --out "$HOME/Music/Suno Downloads"
fi

"$PY" suno_dl.py "$@"

echo
read -r -p "Press Enter to close…" _
