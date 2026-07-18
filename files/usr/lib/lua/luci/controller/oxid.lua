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
	entry({"admin","services","oxid","manage"},
		template("oxid/manage"), _("Manage"), 2).leaf = true
	entry({"admin","services","oxid","test"},
		template("oxid/test"), _("Test"), 3).leaf = true
	entry({"admin","services","oxid","settings"},
		cbi("oxid/settings"), _("Advanced"), 4).leaf = true
	entry({"admin","services","oxid","status"}, call("act_status")).leaf = true
	entry({"admin","services","oxid","config"}, call("act_config")).leaf = true
	entry({"admin","services","oxid","tags"}, call("act_tags")).leaf = true
	entry({"admin","services","oxid","do"}, call("act_do")).leaf = true
end

function act_config()
	http.prepare_content("application/json")
	http.write(util.exec(CTL .. " config"))
end

function act_tags()
	http.prepare_content("application/json")
	http.write(util.exec(CTL .. " tags"))
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
	elseif a == "delay" then
		-- arg = outbound tag, body = target URL; measured through the tunnel
		out = util.exec(CTL .. " delay " .. sq(arg) .. " " .. sq(body or ""))
	elseif a == "measure" then
		-- reliable: switch live to arg, settle, time a fetch through it, restore
		out = util.exec(CTL .. " measure " .. sq(arg) .. " " .. sq(body or ""))
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
	elseif a == "sub-add" or a == "sub-del" or a == "sub-toggle"
		or a == "node-set" or a == "node-del" or a == "group-set" or a == "group-del"
		or a == "chain-set" or a == "chain-del" then
		-- manager mutations: JSON body on stdin -> edit.py -> apply
		local tmp = "/tmp/oxid/_edit.tmp"
		local f = io.open(tmp, "w")
		if f then f:write(body or ""); f:close() end
		out = util.exec(CTL .. " " .. a .. " < " .. tmp .. " 2>&1")
		os.remove(tmp)
	else
		out = "unknown action"
	end
	http.prepare_content("application/json")
	http.write(luci.jsonc.stringify({ ok = true, out = out }))
end
