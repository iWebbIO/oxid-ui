#!/bin/sh
# oxid-ui installer for OpenWrt. Run from the repo root on the router.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ZASHBOARD_VER="${ZASHBOARD_VER:-v3.15.0}"
ZASHBOARD_ASSET="dist-no-fonts.zip"

say() { echo ">> $*"; }

# ---- package manager ----
if command -v apk >/dev/null 2>&1; then PKG="apk add"; elif command -v opkg >/dev/null 2>&1; then PKG="opkg install"; else
	echo "no apk/opkg found"; exit 1; fi

say "installing dependencies"
( command -v opkg >/dev/null 2>&1 && opkg update ) >/dev/null 2>&1 || true
for p in python3 python3-light curl ca-bundle unzip luci-compat luci-lua-runtime; do
	$PKG "$p" >/dev/null 2>&1 || true
done

# ---- copy files (everything except the UCI default config, handled below) ----
say "installing files"
cp -a "$HERE/files/etc/init.d" "$HERE/files/etc/singbox-sub" /etc/ 2>/dev/null || {
	mkdir -p /etc/singbox-sub; cp -a "$HERE/files/etc/singbox-sub/." /etc/singbox-sub/
	cp -a "$HERE/files/etc/init.d/singbox-sub" /etc/init.d/singbox-sub; }
cp -a "$HERE/files/usr/." /usr/
chmod 755 /etc/init.d/singbox-sub /etc/singbox-sub/gen.py /etc/singbox-sub/ctl.sh \
	/etc/singbox-sub/awg2singbox.py /etc/singbox-sub/oxid-update.sh 2>/dev/null || true
rm -f /etc/singbox-sub/static/amnezia.json.example.installed

# ---- UCI config: keep existing, install default only if absent ----
if [ ! -f /etc/config/singbox-sub ]; then
	say "installing default UCI config"
	cp "$HERE/files/etc/config/singbox-sub" /etc/config/singbox-sub
fi

# ---- sing-box binary ----
if [ ! -x /usr/bin/sing-box-awg ]; then
	if [ -x /usr/bin/sing-box ]; then
		say "no sing-box-awg found -> symlinking stock sing-box (AmneziaWG disabled; build/build-awg.sh to enable)"
		ln -sf /usr/bin/sing-box /usr/bin/sing-box-awg
	else
		echo "!! no sing-box binary. Install the sing-box package or build build/build-awg.sh, then re-run."; exit 1
	fi
fi
/usr/bin/sing-box-awg version >/dev/null 2>&1 || { echo "!! sing-box-awg not runnable"; exit 1; }

# ---- generate a dashboard secret if empty ----
if [ -z "$(uci -q get singbox-sub.main.secret)" ]; then
	SEC="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"
	uci set singbox-sub.main.secret="$SEC"; uci commit singbox-sub
	say "generated dashboard secret"
fi

# ---- dashboard (zashboard) ----
mkdir -p /etc/singbox-sub/ui
if [ ! -f /etc/singbox-sub/ui/index.html ]; then
	say "downloading zashboard $ZASHBOARD_VER"
	if curl -m120 -sSL -o /tmp/zash.zip \
		"https://github.com/Zephyruso/zashboard/releases/download/$ZASHBOARD_VER/$ZASHBOARD_ASSET"; then
		rm -rf /tmp/zash && mkdir -p /tmp/zash && unzip -q /tmp/zash.zip -d /tmp/zash
		SRC=/tmp/zash; [ -d /tmp/zash/dist ] && SRC=/tmp/zash/dist
		cp -a "$SRC/." /etc/singbox-sub/ui/ && rm -rf /tmp/zash /tmp/zash.zip
	else
		echo "!! zashboard download failed — the dashboard will be empty until you populate /etc/singbox-sub/ui"
	fi
fi

# ---- cron: hourly graceful refresh + watchdog ----
say "installing cron jobs"
CRON=/etc/crontabs/root; touch "$CRON"
grep -v 'singbox-sub/ctl.sh' "$CRON" > "$CRON.new" 2>/dev/null || true
cat >> "$CRON.new" <<EOF
0 * * * * /etc/singbox-sub/ctl.sh stage >/tmp/singbox-sub/stage.log 2>&1
*/3 * * * * /etc/singbox-sub/ctl.sh watchdog >/dev/null 2>&1
EOF
mv "$CRON.new" "$CRON"; chmod 600 "$CRON"
/etc/init.d/cron enable >/dev/null 2>&1 || true
/etc/init.d/cron restart >/dev/null 2>&1 || true

# ---- enable + start ----
# OXID_NO_RESTART=1 (used by self-update): install files + stage a fresh RAM config
# but DON'T restart the core, so live connections aren't dropped (applies on reboot).
/etc/init.d/singbox-sub enable
if [ "${OXID_NO_RESTART:-0}" = "1" ]; then
	say "staging config (no core restart)"
	/etc/singbox-sub/ctl.sh 'stage!' >/dev/null 2>&1 || true
else
	say "starting service"
	/etc/init.d/singbox-sub restart
fi

# ---- refresh LuCI ----
rm -f /tmp/luci-*cache* 2>/dev/null || true
/etc/init.d/rpcd restart >/dev/null 2>&1 || true
/etc/init.d/uhttpd restart >/dev/null 2>&1 || true

IP="$(uci -q get network.lan.ipaddr || echo <router-ip>)"
echo
say "done."
echo "   LuCI:      Services -> Sing-Box Subs"
echo "   Dashboard: http://$IP:9090/ui/"
echo "   Secret:    $(uci -q get singbox-sub.main.secret)"
echo "   SOCKS:     127.0.0.1:7890  (point passwall2 node + udp_node here)"
