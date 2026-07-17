#!/bin/sh
# OXID self-update: fetch the latest package from GitHub and reinstall.
# Fetches THROUGH the local proxy (so it works even where GitHub is blocked),
# falling back to a direct connection.
#
#   oxid-update.sh            update the panel + config, NO core restart (no disconnect)
#   oxid-update.sh --apply    update AND restart the core (loads fresh config)
#
# The LuCI "Update" button calls the first form; its arrow ("Update & apply")
# calls --apply.
REPO="${OXID_REPO:-iWebbIO/oxid-ui}"
BRANCH="${OXID_BRANCH:-main}"
TARBALL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
SOCKS="127.0.0.1:$(uci -q get singbox-sub.main.socks_port 2>/dev/null || echo 7890)"
TMP=/tmp/oxid-update
say() { echo ">> $*"; }

rm -rf "$TMP"; mkdir -p "$TMP"
say "downloading $REPO@$BRANCH via proxy $SOCKS"
if ! curl -m240 -fsSL --socks5-hostname "$SOCKS" -o "$TMP/oxid.tar.gz" "$TARBALL"; then
	say "proxy fetch failed, trying direct"
	curl -m240 -fsSL -o "$TMP/oxid.tar.gz" "$TARBALL" || { echo "!! download failed"; exit 1; }
fi
tar -xzf "$TMP/oxid.tar.gz" -C "$TMP" || { echo "!! extract failed"; exit 1; }
SRC="$(find "$TMP" -maxdepth 1 -type d -name '*oxid-ui*' | head -1)"
[ -d "$SRC/files" ] || { echo "!! unpacked package looks wrong"; exit 1; }

if [ "$1" = "--apply" ]; then
	say "installing (with apply/restart)"
	sh "$SRC/install.sh"
else
	say "installing (staged, no core restart)"
	OXID_NO_RESTART=1 sh "$SRC/install.sh"
fi
rm -rf "$TMP"
say "update complete"
