local m = Map("oxid", translate("OXID — Settings & Subscriptions"),
	translate("Subscription-fed sing-box balancer feeding a local SOCKS at 127.0.0.1:7890. " ..
		"Nodes live in RAM; only these settings sit on flash. Node lists and the beautiful " ..
		"dashboard are on the Control tab."))

function m.on_after_commit(self)
	luci.sys.call("/etc/oxid/oxid apply >/tmp/oxid/apply.log 2>&1 &")
end

local s = m:section(NamedSection, "main", "singbox", translate("Core"))
s.addremove = false
s.anonymous = true

local act = s:option(ListValue, "active", translate("Active config (main outbound)"),
	translate("Applied on Save & Apply (brief reconnect). To switch WITHOUT reconnecting, use the Control tab."))
act:value("direct", "direct (no proxy)")
m.uci:foreach("oxid", "subscription", function(sn)
	if sn.name then act:value(sn.name, "sub: " .. sn.name) end
end)
local sd = "/etc/oxid/static"
if nixio.fs.access(sd) then
	for f in nixio.fs.dir(sd) do
		if f:match("%.json$") then
			local o = luci.jsonc.parse(nixio.fs.readfile(sd .. "/" .. f) or "")
			local tag = (o and o.tag) or f:gsub("%.json$", "")
			act:value(tag, "static: " .. tag)
		end
	end
end

local hr = s:option(Flag, "hourly", translate("Auto-refresh subscriptions hourly"),
	translate("Opt-in. When on, re-fetch all subscriptions into RAM every hour WITHOUT restarting the " ..
		"core — no disconnect; fresh nodes load on the next switch/apply or reboot. Off by default: leave " ..
		"it off unless OXID is your day-to-day exit. You can always refresh manually from the Dashboard."))
hr.rmempty = false
hr.default = "0"

local wd = s:option(Flag, "watchdog", translate("Auto-failover watchdog"),
	translate("Every few minutes, verify the main config works. If it dies, fail over to a healthy " ..
		"subscription automatically; when the main recovers, switch back to it."))
wd.rmempty = false
wd.default = "1"

s:option(Value, "socks_port", translate("SOCKS port")).datatype = "port"
s:option(Value, "controller", translate("Dashboard controller (host:port)"))
local sec = s:option(Value, "secret", translate("Dashboard secret"))
sec.password = true
s:option(Value, "max_nodes", translate("Max nodes per subscription")).datatype = "uinteger"

local bm = s:option(Value, "bypass_mark", translate("passwall2 bypass mark"),
	translate("fwmark set on OXID's own outbound sockets so passwall2's transparent proxy lets them " ..
		"out directly instead of re-tunnelling them (which wrecks latency and makes nodes falsely " ..
		"show as down). 255 matches passwall's 0xff bypass rule; 0 disables."))
bm.datatype = "uinteger"
bm.default = "255"

s:option(Value, "interval", translate("Health-check interval (e.g. 3m0s)"))
s:option(Value, "healthcheck_url", translate("Health-check URL"))

local ss = m:section(TypedSection, "subscription", translate("Subscriptions"),
	translate("sing-box-format subscription URLs. Toggle, add or remove — changes apply on Save & Apply."))
ss.addremove = true
ss.anonymous = true
ss.sortable = true
ss.template = "cbi/tblsection"

local en = ss:option(Flag, "enabled", translate("On"))
en.rmempty = false
en.default = "1"
ss:option(Value, "name", translate("Name")).rmempty = false
local url = ss:option(Value, "url", translate("URL (sing-box JSON)"))
url.rmempty = false
url.width = "60%"

return m
