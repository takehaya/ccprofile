#!/bin/sh
set -eu

fail() { echo "ccprofile install: $1" >&2; exit 1; }

case "${1-}" in
    '') mode=remote ;;
    --local) mode=local ;;
    --help|-h)
        echo 'Usage: sh install.sh [--local]'
        echo 'Installs to ~/.local/bin, or CCPROFILE_INSTALL_DIR. Requires Python 3.11+.'
        exit 0 ;;
    *) fail 'unknown option; use --help' ;;
esac
[ "$#" -le 1 ] || fail 'too many arguments'
command -v python3 >/dev/null 2>&1 || fail 'Python 3.11+ is required (brew install python)'
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail 'Python 3.11+ is required (brew install python)'

install_dir=${CCPROFILE_INSTALL_DIR:-"$HOME/.local/bin"}
mkdir -p "$install_dir"
[ ! -L "$install_dir/ccprofile" ] || fail 'refusing to replace a symlink'
[ ! -d "$install_dir/ccprofile" ] || fail 'destination is a directory'
tmp=$(mktemp "$install_dir/.ccprofile-install.XXXXXXXX")
trap 'rm -f "$tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$mode" = local ]; then
    source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    cat "$source_dir/src/launcher.sh" "$source_dir/src/ccprofile.py" > "$tmp"
else
    command -v curl >/dev/null 2>&1 || fail 'curl is required'
    for source_file in launcher.sh ccprofile.py; do
        curl --proto '=https' --tlsv1.2 -fsSL \
            "https://raw.githubusercontent.com/takehaya/ccprofile/main/src/$source_file" >> "$tmp"
    done
fi

chmod 755 "$tmp"
"$tmp" --version >/dev/null
mv -f "$tmp" "$install_dir/ccprofile"
echo "Installed: $install_dir/ccprofile"
case ":$PATH:" in
    *":$install_dir:"*) ;;
    *) echo 'Add the installation directory to PATH in ~/.zshrc.'
       echo 'For the default location: export PATH="$HOME/.local/bin:$PATH"' ;;
esac
