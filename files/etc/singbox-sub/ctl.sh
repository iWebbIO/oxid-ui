#!/bin/sh
# singbox-sub control surface — used by cron and the LuCI app.
ETC=/etc/singbox-sub
STATIC=$ETC/static
CFG=/tmp/singbox-sub/config.json
BIN=/usr/bin/sing-box-awg
GEN="python3 $ETC/gen.py"

mkdir -p /tmp/singbox-sub   # tmpfs is wiped on reboot; every path below needs it

secret() { uci -q get singbox-sub.main.secret; }
api() { # api METHOD PATH [DATA]
    local m="$1" p="$2" d="$3"
    if [ -n "$d" ]; then
        curl -m6 -sS -X "$m" -H "Authorization: Bearer $(secret)" \
             -H 'Content-Type: application/json' -d "$d" "http://127.0.0.1:9090$p"
    else
        curl -m6 -sS -X "$m" -H "Authorization: Bearer $(secret)" "http://127.0.0.1:9090$p"
    fi
}

stage() {  # graceful: rebuild RAM config, validate, DO NOT restart
    $GEN 2>>/tmp/singbox-sub/gen.log
    if $BIN check -c "$CFG"; then
        cmp -s "$CFG" "$ETC/config.last" 2>/dev/null || cp "$CFG" "$ETC/config.last"  # boot safety net
        echo "staged"
    else echo "ERR invalid config; kept previous"; return 1; fi
}

member() { # resolve a bare tag (sub name / static / direct) to its selector member
    local t="$1"; uci -q show singbox-sub | grep -q "\.name='$t'" && echo "sub:$t" || echo "$t"
}
healthy() { # healthy <selector-member> -> 0 if a health probe through it succeeds
    # own curl with a longer -m than api() (idle AmneziaWG needs time to handshake)
    curl -m12 -sS -H "Authorization: Bearer $(secret)" \
        "http://127.0.0.1:9090/proxies/$1/delay?url=http://www.gstatic.com/generate_204&timeout=8000" \
        2>/dev/null | grep -q '"delay"'
}
watchdog() { # keep the main outbound preferred; fail over to a working sub if it dies
    [ "$(uci -q get singbox-sub.main.watchdog)" = "0" ] && return 0
    pgrep -f "sing-box-awg run -c $CFG" >/dev/null || return 0
    local main cur; main=$(uci -q get singbox-sub.main.active); [ -n "$main" ] || return 0
    main=$(member "$main")
    cur=$(api GET /proxies/PROXY 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('now',''))" 2>/dev/null)
    if healthy "$main"; then
        [ "$cur" != "$main" ] && { api PUT /proxies/PROXY "{\"name\":\"$main\"}" >/dev/null; logger -t singbox-sub "watchdog: restored $main"; }
    elif ! healthy "$cur"; then
        for g in $(python3 -c "import json;print(' '.join(o['tag'] for o in json.load(open('$CFG'))['outbounds'] if o.get('tag','').startswith('sub:')))" 2>/dev/null); do
            if healthy "$g"; then api PUT /proxies/PROXY "{\"name\":\"$g\"}" >/dev/null; logger -t singbox-sub "watchdog: $main down, failover to $g"; break; fi
        done
    fi
}
apply() {  # rebuild + validate + restart (loads fresh nodes into the core)
    $GEN 2>>/tmp/singbox-sub/gen.log
    if $BIN check -c "$CFG"; then
        /etc/init.d/singbox-sub restart
        # re-assert active outbound (cache_file may restore a stale selection)
        local t; t=$(uci -q get singbox-sub.main.active)
        [ -n "$t" ] && { i=0; while [ $i -lt 10 ]; do api GET /version >/dev/null 2>&1 && break; sleep 1; i=$((i+1)); done; switch "$t" >/dev/null 2>&1; }
        echo "applied"
    else echo "ERR invalid; not applied"; return 1; fi
}
switch() { # switch active outbound live (no restart) + persist to UCI
    local tag="$1"; [ -n "$tag" ] || { echo "usage: switch <tag>"; return 1; }
    # persist: store bare name (sub name / static tag / direct)
    uci set singbox-sub.main.active="$tag"; uci commit singbox-sub
    # resolve to selector member: sub name -> sub:<name>
    local member="$tag"
    uci -q show singbox-sub | grep -q "\.name='$tag'" && member="sub:$tag"
    api PUT /proxies/PROXY "{\"name\":\"$member\"}" >/dev/null && echo "switched to $member" || echo "ERR switch"
}
status() { # JSON status for the GUI
    local up=0; pgrep -f "sing-box-awg run -c $CFG" >/dev/null && up=1
    UP="$up" PROXY_JSON="$(api GET /proxies/PROXY 2>/dev/null)" python3 - <<PY 2>/dev/null
import sys,json,os
up=os.environ.get("UP","0")
try: now=json.loads(os.environ.get("PROXY_JSON","")).get("now","")
except: now=""
cfg={}
try: cfg=json.load(open("$CFG"))
except: pass
subs=[o["tag"] for o in cfg.get("outbounds",[]) if o.get("tag","").startswith("sub:")]
eps=[e["tag"] for e in cfg.get("endpoints",[])]
counts={o["tag"]:len(o.get("outbounds",[])) for o in cfg.get("outbounds",[]) if o.get("tag","").startswith("sub:")}
statics=[]
d="$STATIC"
if os.path.isdir(d):
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            try: statics.append({"file":f,"tag":json.load(open(d+"/"+f)).get("tag",f[:-5]),"type":json.load(open(d+"/"+f)).get("type","")})
            except: statics.append({"file":f,"tag":f[:-5],"type":"?"})
print(json.dumps({"running":up=="1","active":now,"subs":subs,"endpoints":eps,"counts":counts,"statics":statics}))
PY
}
case "$1" in
    stage)   [ "$(uci -q get singbox-sub.main.hourly)" = "0" ] && { echo "hourly disabled"; exit 0; }; stage ;;
    stage!)  stage ;;                       # force stage regardless of hourly flag
    apply)   apply ;;
    restart) /etc/init.d/singbox-sub restart; echo restarted ;;
    switch)  switch "$2" ;;
    watchdog) watchdog ;;
    status)  status ;;
    self-update)        "$ETC/oxid-update.sh" ;;          # update panel from GitHub, no core restart
    self-update-apply)  "$ETC/oxid-update.sh" --apply ;;  # update + restart core (the button's arrow)
    static-del)  rm -f "$STATIC/$2.json" && stage; echo "deleted $2" ;;
    awg-import)  # awg-import <tag>  (wg-quick on stdin)
        mkdir -p "$STATIC"
        if ! python3 "$ETC/awg2singbox.py" "$2" > "$STATIC/$2.json"; then
            rm -f "$STATIC/$2.json"; echo "ERR awg parse (need [Interface] PrivateKey + [Peer] PublicKey/Endpoint)"; exit 1; fi
        if stage | grep -q staged; then echo "imported $2"; else rm -f "$STATIC/$2.json"; stage >/dev/null; echo "ERR node rejected by sing-box; removed"; exit 1; fi ;;
    static-add)  # static-add <tag>  (raw sing-box outbound/endpoint JSON on stdin)
        mkdir -p "$STATIC"; cat > "$STATIC/$2.json"
        if ! python3 -c "import json,sys;o=json.load(open('$STATIC/$2.json'));sys.exit(0 if o.get('tag') and o.get('type') else 1)" 2>/dev/null; then
            rm -f "$STATIC/$2.json"; echo "ERR JSON must have tag+type"; exit 1; fi
        if stage | grep -q staged; then echo "added $2"; else rm -f "$STATIC/$2.json"; stage >/dev/null; echo "ERR node rejected by sing-box; removed"; exit 1; fi ;;
    *) echo "usage: ctl.sh {stage|apply|restart|switch <tag>|status|watchdog|self-update|self-update-apply|static-del <n>|awg-import <n>|static-add <n>}" ;;
esac
