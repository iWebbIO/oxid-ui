#!/bin/sh
# Remove oxid-ui. Pass --purge to also delete settings and the dashboard.
/etc/init.d/singbox-sub stop 2>/dev/null
/etc/init.d/singbox-sub disable 2>/dev/null
rm -f /etc/init.d/singbox-sub
rm -f /usr/lib/lua/luci/controller/singbox-sub.lua
rm -rf /usr/lib/lua/luci/model/cbi/singbox-sub
rm -rf /usr/lib/lua/luci/view/singbox-sub
rm -f /usr/share/rpcd/acl.d/luci-app-singbox-sub.json
if [ -f /etc/crontabs/root ]; then
	grep -v 'singbox-sub/ctl.sh' /etc/crontabs/root > /tmp/cron.new 2>/dev/null || true
	mv /tmp/cron.new /etc/crontabs/root; /etc/init.d/cron restart 2>/dev/null
fi
rm -rf /tmp/singbox-sub
if [ "$1" = "--purge" ]; then
	rm -rf /etc/singbox-sub
	rm -f /etc/config/singbox-sub
	echo "purged settings, static nodes and dashboard"
else
	echo "kept /etc/singbox-sub and /etc/config/singbox-sub (use --purge to remove)"
fi
rm -f /tmp/luci-*cache* 2>/dev/null
/etc/init.d/rpcd restart >/dev/null 2>&1
/etc/init.d/uhttpd restart >/dev/null 2>&1
echo "uninstalled. (The sing-box-awg binary was left in place.)"
