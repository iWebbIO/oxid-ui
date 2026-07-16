#!/usr/bin/env bash
# Rebuild the AmneziaWG-capable sing-box (Leadaxe/sing-box-lx) for the router's
# armv7 core. Re-run this to UPDATE: it pulls latest source and recompiles.
set -e
SP="$(cd "$(dirname "$0")" && pwd)"
SRC="$SP/sbx-src"
TAGS="with_gvisor,with_quic,with_dhcp,with_wireguard,with_utls,with_acme,with_clash_api,with_awg,badlinkname,tfogo_checklinkname0"
[ -d "$SRC/.git" ] || git clone --depth 1 https://github.com/Leadaxe/sing-box-lx "$SRC"
cd "$SRC"
git pull --ff-only 2>/dev/null || true
git submodule update --init --depth 1 submodules/wireguard-go
# Build with the EXACT Go toolchain the fork targets (badtls hooks crypto/tls
# internals that break on newer Go). Fetch that SDK via the golang.org/dl helper
# (pulls from dl.google.com, avoiding the flaky module proxy), then build local.
# Google/golang.org egress is filtered in this env; use a mirror that serves
# BOTH the pinned Go toolchain and the modules. Integrity comes from the
# committed go.sum (every module hash is verified against it).
GOVER="go$(grep -m1 '^go ' go.mod | awk '{print $2}')"
export GOPROXY="https://goproxy.cn,direct"
echo "building $(git describe --tags --always) with $GOVER (via mirror), tags: $TAGS"
GOTOOLCHAIN="$GOVER" GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 \
  go build -trimpath -tags "$TAGS" -ldflags "-s -w" -o "$SP/sing-box-awg" ./cmd/sing-box
echo "built: $(wc -c < "$SP/sing-box-awg") bytes"
