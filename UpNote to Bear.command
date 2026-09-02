#!/bin/bash
# Double-clickable front door for upnote2bear.py.
# Opens in Terminal, asks two plain questions, then does the work.

cd "$(dirname "$0")" || exit 1

bold=$(tput bold 2>/dev/null); dim=$(tput dim 2>/dev/null)
red=$(tput setaf 1 2>/dev/null); green=$(tput setaf 2 2>/dev/null)
off=$(tput sgr0 2>/dev/null)

say()  { printf '%s\n' "$1"; }
fail() { printf '%s%s%s\n' "$red" "$1" "$off"; printf '\nPress return to close.'; read -r _; exit 1; }

clear
say "${bold}Copy your UpNote notes into Bear${off}"
say "${dim}Nothing is uploaded. Nothing in UpNote is changed.${off}"
say ""

# --- checks -------------------------------------------------------------

UPNOTE_DB="$HOME/Library/Containers/com.getupnote.desktop/Data/Library/Application Support/UpNote/upnote.sqlite3"

if [ ! -f "$UPNOTE_DB" ]; then
  fail "Could not find UpNote's notes on this Mac.

Open UpNote once, let it finish syncing, then run this again."
fi

if [ ! -d "/Applications/Bear.app" ]; then
  say "${red}Bear is not installed in your Applications folder.${off}"
  say "Your notes will still be converted and saved to the Desktop,"
  say "so you can install Bear and import them later."
  say ""
fi

if ! command -v python3 >/dev/null 2>&1; then
  fail "Python 3 is missing. Install Apple's command line tools with:

    xcode-select --install

then run this again."
fi

# --- questions ----------------------------------------------------------

say "${bold}Two quick questions.${off}"
say ""

say "1. Also copy the notes sitting in UpNote's trash?"
say "   ${dim}They arrive tagged #UpNote Trash# so they stay separate.${off}"
printf "   Copy the trash too? [y/N] "
read -r answer_trash
case "$answer_trash" in
  [Yy]*) TRASH="--include-trash" ;;
  *)     TRASH="" ;;
esac
say ""

say "2. Send the notes straight into Bear when they are ready?"
say "   ${dim}Answer no to only save them to your Desktop.${off}"
printf "   Import into Bear now? [Y/n] "
read -r answer_import
case "$answer_import" in
  [Nn]*) IMPORT="--no-import" ;;
  *)     IMPORT="" ;;
esac
say ""

# --- run ----------------------------------------------------------------

say "${bold}Working. This usually takes about a minute.${off}"
say ""

# shellcheck disable=SC2086
if python3 upnote2bear.py $TRASH $IMPORT; then
  say ""
  say "${green}${bold}Finished.${off}"
  if [ -z "$IMPORT" ]; then
    say "Your notes are now in Bear, and a copy is in the"
    say "\"UpNote to Bear\" folder on your Desktop."
  else
    say "Your notes are in the \"UpNote to Bear\" folder on your Desktop."
    say "To put them in Bear later, select the notes in that folder"
    say "and open them with Bear."
  fi
  if [ -f "$HOME/Desktop/UpNote to Bear/MISSING-IMAGES.txt" ]; then
    say ""
    say "${dim}Some images were never saved on this Mac by UpNote, so they"
    say "could not be copied. See MISSING-IMAGES.txt in that folder.${off}"
  fi
else
  say ""
  fail "Something went wrong. The message above says what."
fi

say ""
printf 'Press return to close this window.'
read -r _
