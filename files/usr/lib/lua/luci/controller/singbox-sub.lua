module("luci.controller.singbox-sub", package.seeall)
local http = require "luci.http"
local util = require "luci.util"
local sq   = util.shellquote
local CTL  = "/etc/singbox-sub/ctl.sh"

function index()
	if not nixio.fs.access("/etc/config/singbox-sub") then return end
	entry({"admin","services","singbox-sub"},
		alias("admin","services","singbox-sub","control"), _("Sing-Box Subs"), 45).dependent = true
	entry({"admin","services","singbox-sub","control"},
		template("singbox-sub/control"), _("Control"), 1).leaf = true
	entry({"admin","services","singbox-sub","settings"},
		cbi("singbox-sub/settings"), _("Settings & Subscriptions"), 2).leaf = true
	entry({"admin","services","singbox-sub","status"}, call("act_status")).leaf = true
	entry({"admin","services","singbox-sub","do"}, call("act_do")).leaf = true
end

function act_status()
	http.prepare_content("application/json")
	http.write(util.exec(CTL .. " status"))
end

function act_do()
	local a    = http.formvalue("act") or ""
	local arg  = http.formvalue("arg") or ""
	local body = http.formvalue("body")
	local out  = ""
	if a == "switch" then
		out = util.exec(CTL .. " switch " .. sq(arg))
	elseif a == "apply" then
		out = util.exec(CTL .. " apply")
	elseif a == "stage" then
		out = util.exec(CTL .. " stage!")
	elseif a == "restart" then
		out = util.exec(CTL .. " restart")
	elseif a == "static-del" then
		out = util.exec(CTL .. " static-del " .. sq(arg))
	elseif a == "awg-import" or a == "static-add" then
		local tmp = "/tmp/singbox-sub/_import.tmp"
		local f = io.open(tmp, "w")
		if f then f:write(body or ""); f:close() end
		out = util.exec(CTL .. " " .. a .. " " .. sq(arg) .. " < " .. tmp .. " 2>&1")
		os.remove(tmp)
	else
		out = "unknown action"
	end
	http.prepare_content("application/json")
	http.write(luci.jsonc.stringify({ ok = true, out = out }))
end
