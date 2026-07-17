module("luci.controller.oxid", package.seeall)
local http = require "luci.http"
local util = require "luci.util"
local sq   = util.shellquote
local CTL  = "/etc/oxid/oxid"

function index()
	if not nixio.fs.access("/etc/config/oxid") then return end
	entry({"admin","services","oxid"},
		alias("admin","services","oxid","control"), _("OXID"), 45).dependent = true
	entry({"admin","services","oxid","control"},
		template("oxid/control"), _("Dashboard"), 1).leaf = true
	entry({"admin","services","oxid","settings"},
		cbi("oxid/settings"), _("Settings & Subscriptions"), 2).leaf = true
	entry({"admin","services","oxid","status"}, call("act_status")).leaf = true
	entry({"admin","services","oxid","do"}, call("act_do")).leaf = true
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
	elseif a == "test" then
		out = util.exec(CTL .. " test")
	elseif a == "self-update" then
		-- run detached: self-update reinstalls this very controller mid-request
		util.exec("(" .. CTL .. " self-update) >/tmp/oxid/update.log 2>&1 &")
		out = "Updating OXID from GitHub (panel only, no reconnect). Reloading shortly…"
	elseif a == "self-update-apply" then
		util.exec("(" .. CTL .. " self-update-apply) >/tmp/oxid/update.log 2>&1 &")
		out = "Updating OXID from GitHub + applying. Core reconnects; reloading shortly…"
	elseif a == "static-del" then
		out = util.exec(CTL .. " static-del " .. sq(arg))
	elseif a == "awg-import" or a == "static-add" then
		local tmp = "/tmp/oxid/_import.tmp"
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
