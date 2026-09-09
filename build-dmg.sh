#!/bin/bash
# ---------------------------------------------------------------
#  Builds "Suno-Playlist-Downloader-macOS.dmg" containing the
#  script + double-click launcher + instructions.
#  Usage:  ./build-dmg.sh     (macOS only, hdiutil required)
# ---------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

STAGE_ROOT="$(mktemp -d)"
STAGE="$STAGE_ROOT/Suno Playlist Downloader"
mkdir -p "$STAGE"

cp suno_dl.py requirements.txt README.md LICENSE "$STAGE/"
cp "Start Suno Downloader.command" "$STAGE/"
chmod +x "$STAGE/Start Suno Downloader.command"

cat > "$STAGE/START HERE.txt" <<'TXT'
               Suno Playlist Downloader  🎵
------------------------------------------------------------
1.  Keep this folder somewhere easy (Desktop is fine).
    (If you are reading this inside the opened .dmg window,
    drag the folder to your Desktop first.)

2.  Double-click  "Start Suno Downloader.command"
    The first time, macOS may complain because the file came
    from the internet. If it does:
        right-click the file  →  Open  →  Open

3.  Paste your Suno playlist link, press Enter,
    then press Enter once more on the empty line.

4.  Your music is saved to:   Music → Suno Downloads

Works with any PUBLIC playlist link. No Suno account needed.
TXT

hdiutil create \
    -volname "Suno Playlist Downloader" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "Suno-Playlist-Downloader-macOS.dmg"

rm -rf "$STAGE_ROOT"
echo
echo "Built: Suno-Playlist-Downloader-macOS.dmg"
