#!/bin/sh
# Remove oxid-ui. Pass --purge to also delete settings and the dashboard.
/etc/init.d/oxid stop 2>/dev/null
/etc/init.d/oxid disable 2>/dev/null
rm -f /etc/init.d/oxid
rm -f /usr/bin/oxid
rm -f /usr/lib/lua/luci/controller/oxid.lua
rm -rf /usr/lib/lua/luci/model/cbi/oxid
rm -rf /usr/lib/lua/luci/view/oxid
rm -f /usr/share/rpcd/acl.d/luci-app-oxid.json
if [ -f /etc/crontabs/root ]; then
	grep -v 'oxid/oxid' /etc/crontabs/root > /tmp/cron.new 2>/dev/null || true
	mv /tmp/cron.new /etc/crontabs/root; /etc/init.d/cron restart 2>/dev/null
fi
rm -rf /tmp/oxid
if [ "$1" = "--purge" ]; then
	rm -rf /etc/oxid
	rm -f /etc/config/oxid
	echo "purged settings, static nodes and dashboard"
else
	echo "kept /etc/oxid and /etc/config/oxid (use --purge to remove)"
fi
rm -f /tmp/luci-*cache* 2>/dev/null
/etc/init.d/rpcd restart >/dev/null 2>&1
/etc/init.d/uhttpd restart >/dev/null 2>&1
echo "uninstalled. (The sing-box-awg binary was left in place.)"
