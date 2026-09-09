#!/bin/bash
# ---------------------------------------------------------------
#  Suno Playlist Downloader — Linux launcher
#  Run from a terminal:  ./start-linux.sh
#  First run installs what it needs (pip --user).
# ---------------------------------------------------------------
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
export SUNODL_NO_PAUSE=1

PY="$(command -v python3)"
if [ -z "$PY" ]; then
    echo " [!] python3 not found. Install it with your package manager, e.g.:"
    echo "       sudo apt install python3 python3-pip   (Debian/Ubuntu)"
    echo "       sudo dnf install python3 python3-pip   (Fedora)"
    exit 1
fi

if ! "$PY" -c 'import requests, rich, cryptography' >/dev/null 2>&1; then
    echo " First run — installing the needed packages…"
    "$PY" -m pip install --user --quiet -r requirements.txt || {
        echo " [!] Could not install packages automatically. Try:"
        echo "       python3 -m pip install --user requests rich cryptography"
        exit 1
    }
    # make sure ~/.local/bin is on PATH for this session (usually already is)
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) export PATH="$HOME/.local/bin:$PATH" ;;
    esac
fi

exec "$PY" suno_dl.py "$@"
