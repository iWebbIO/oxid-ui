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

# ---- migrate a previous singbox-sub install -> oxid (one-time) ----
if [ -e /etc/config/singbox-sub ] || [ -e /etc/init.d/singbox-sub ] || [ -d /etc/singbox-sub ]; then
	say "migrating singbox-sub -> oxid"
	/etc/init.d/singbox-sub stop 2>/dev/null || true
	/etc/init.d/singbox-sub disable 2>/dev/null || true
	mkdir -p /etc/oxid
	[ -f /etc/config/singbox-sub ] && [ ! -f /etc/config/oxid ] && cp /etc/config/singbox-sub /etc/config/oxid
	for d in static lastgood ui config.last; do   # carry over runtime data + dashboard
		[ -e "/etc/singbox-sub/$d" ] && cp -a "/etc/singbox-sub/$d" /etc/oxid/ 2>/dev/null || true
	done
	rm -rf /etc/singbox-sub /tmp/singbox-sub
	rm -f /etc/init.d/singbox-sub /etc/config/singbox-sub
	rm -f /usr/lib/lua/luci/controller/singbox-sub.lua
	rm -rf /usr/lib/lua/luci/model/cbi/singbox-sub /usr/lib/lua/luci/view/singbox-sub
	rm -f /usr/share/rpcd/acl.d/luci-app-singbox-sub.json
	[ -f /etc/crontabs/root ] && sed -i '/singbox-sub/d' /etc/crontabs/root
fi

# ---- copy files (everything except the UCI default config, handled below) ----
say "installing files"
mkdir -p /etc/oxid /etc/init.d
cp -a "$HERE/files/etc/oxid/." /etc/oxid/
cp -f "$HERE/files/etc/init.d/oxid" /etc/init.d/oxid
cp -a "$HERE/files/usr/." /usr/
chmod 755 /etc/init.d/oxid /etc/oxid/gen.py /etc/oxid/oxid /etc/oxid/edit.py \
	/etc/oxid/awg2singbox.py /etc/oxid/oxid-update.sh 2>/dev/null || true
rm -f /etc/oxid/static/amnezia.json.example.installed
ln -sf /etc/oxid/oxid /usr/bin/oxid   # global CLI: `oxid status`, `oxid switch <tag>`, `oxid self-update`

# ---- UCI config: keep existing, install default only if absent ----
if [ ! -f /etc/config/oxid ]; then
	say "installing default UCI config"
	cp "$HERE/files/etc/config/oxid" /etc/config/oxid
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
if [ -z "$(uci -q get oxid.main.secret)" ]; then
	SEC="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"
	uci set oxid.main.secret="$SEC"; uci commit oxid
	say "generated dashboard secret"
fi

# ---- dashboard (zashboard) ----
mkdir -p /etc/oxid/ui
if [ ! -f /etc/oxid/ui/index.html ]; then
	say "downloading zashboard $ZASHBOARD_VER"
	if curl -m120 -sSL -o /tmp/zash.zip \
		"https://github.com/Zephyruso/zashboard/releases/download/$ZASHBOARD_VER/$ZASHBOARD_ASSET"; then
		rm -rf /tmp/zash && mkdir -p /tmp/zash && unzip -q /tmp/zash.zip -d /tmp/zash
		SRC=/tmp/zash; [ -d /tmp/zash/dist ] && SRC=/tmp/zash/dist
		cp -a "$SRC/." /etc/oxid/ui/ && rm -rf /tmp/zash /tmp/zash.zip
	else
		echo "!! zashboard download failed — the dashboard will be empty until you populate /etc/oxid/ui"
	fi
fi

# ---- cron: hourly graceful refresh + watchdog ----
say "installing cron jobs"
CRON=/etc/crontabs/root; touch "$CRON"
grep -v 'oxid/oxid' "$CRON" > "$CRON.new" 2>/dev/null || true
cat >> "$CRON.new" <<EOF
0 * * * * /etc/oxid/oxid stage >/tmp/oxid/stage.log 2>&1
*/3 * * * * /etc/oxid/oxid watchdog >/dev/null 2>&1
EOF
mv "$CRON.new" "$CRON"; chmod 600 "$CRON"
/etc/init.d/cron enable >/dev/null 2>&1 || true
/etc/init.d/cron restart >/dev/null 2>&1 || true

# ---- enable + start ----
# OXID_NO_RESTART=1 (used by self-update): install files + stage a fresh RAM config
# but DON'T restart the core, so live connections aren't dropped (applies on reboot).
/etc/init.d/oxid enable
if [ "${OXID_NO_RESTART:-0}" = "1" ]; then
	say "staging config (no core restart)"
	/etc/oxid/oxid 'stage!' >/dev/null 2>&1 || true
else
	say "starting service"
	/etc/init.d/oxid restart
fi

# ---- refresh LuCI ----
rm -f /tmp/luci-*cache* 2>/dev/null || true
/etc/init.d/rpcd restart >/dev/null 2>&1 || true
/etc/init.d/uhttpd restart >/dev/null 2>&1 || true

IP="$(uci -q get network.lan.ipaddr 2>/dev/null || echo 192.168.1.1)"
echo
say "done."
echo "   LuCI:      Services -> OXID"
echo "   Dashboard: http://$IP:9090/ui/"
echo "   Secret:    $(uci -q get oxid.main.secret)"
echo "   SOCKS:     127.0.0.1:7890  (point passwall2 node + udp_node here)"
