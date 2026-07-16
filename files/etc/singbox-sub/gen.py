#!/usr/bin/env python3
# singbox-sub generator: fetch 3 sing-box-format subscriptions, merge into ONE
# runtime config in RAM (/tmp), with per-sub last-good disk fallback for when
# GitHub is unreachable. Switching subs is done live via clash_api (no regen);
# this script only runs on refresh / boot.
import json, re, subprocess, os, sys, hashlib, base64

ETC   = "/etc/singbox-sub"
RUN   = "/tmp/singbox-sub"
LAST  = ETC + "/lastgood"          # disk fallback (tiny, change-gated -> no flash churn)
UIDIR = ETC + "/ui"
CFG   = RUN + "/config.json"
SUBS  = ETC + "/subs.conf"         # lines: name|url
SETT  = ETC + "/settings.json"

PROXY_TYPES = ("vless","vmess","trojan","shadowsocks","hysteria2","tuic",
               "shadowtls","anytls","wireguard","http","socks")

def log(*a): print("[gen]", *a, file=sys.stderr)

os.makedirs(RUN, exist_ok=True)   # tmpfs is empty after reboot; ensure it exists early

def read_uci():
    """Parse `uci show singbox-sub` -> (main_dict, [subscription_dicts]).
    Returns (None,None) if the UCI config is absent (falls back to files)."""
    try:
        out=subprocess.check_output(["uci","-q","show","singbox-sub"],
                                    stderr=subprocess.DEVNULL).decode("utf-8","replace")
    except Exception:
        return None,None
    main={}; subs={}
    for ln in out.splitlines():
        if "=" not in ln: continue
        k,v=ln.split("=",1); v=v.strip().strip("'")
        if k.startswith("singbox-sub.main."):
            main[k.split(".",2)[2]]=v
        else:
            m=re.match(r"singbox-sub\.@subscription\[(\d+)\]\.(\w+)",k)
            if m: subs.setdefault(int(m.group(1)),{})[m.group(2)]=v
    return main,[subs[i] for i in sorted(subs)]

def load_settings():
    d = {"controller":"0.0.0.0:9090","secret":"","socks_port":7890,
         "healthcheck_url":"https://www.gstatic.com/generate_204",
         "interval":"3m0s","tolerance":100,"active":"","max_nodes":120}
    main,_=read_uci()
    if main:
        for k in ("controller","secret","socks_port","healthcheck_url",
                  "interval","max_nodes","active"):
            if main.get(k): d[k]=main[k]
    else:
        try: d.update(json.load(open(SETT)))
        except Exception as e: log("settings default:", e)
    return d

def load_subs():
    _,sub_list=read_uci()
    if sub_list is not None:
        return [(s["name"],s["url"]) for s in sub_list
                if s.get("enabled","1")=="1" and s.get("name") and s.get("url")]
    subs=[]
    try:
        for ln in open(SUBS):
            ln=ln.strip()
            if not ln or ln.startswith("#") or "|" not in ln: continue
            name,url=ln.split("|",1); subs.append((name.strip(),url.strip()))
    except Exception as e: log("subs file:", e)
    return subs

def strip_jsonc(s):
    s=re.sub(r'^\s*//.*$','',s,flags=re.M)
    s=re.sub(r'/\*.*?\*/','',s,flags=re.S)
    return s

def fetch(url):
    # short timeouts so a slow/blocked GitHub falls back to disk fast (boot safety)
    return subprocess.check_output(
        ["curl","--connect-timeout","8","-m","15","-sS","-fL",url],
        stderr=subprocess.DEVNULL).decode("utf-8","replace")

def sanitize(o):
    # top-level `network` may only be tcp/udp; some subs wrongly put "ws"/"grpc"
    # (that belongs under transport) -> drop the bogus value, keep the node.
    if o.get("network") not in ("tcp","udp"): o.pop("network",None)
    o.pop("detour",None)          # keep nodes self-contained
    # port-hopping ranges: sing-box wants "start:end", some subs emit "start-end"
    sp=o.get("server_ports")
    if isinstance(sp,str): sp=[sp]
    if isinstance(sp,list): o["server_ports"]=[str(x).replace("-",":") for x in sp]
    # reality client REQUIRES utls; some sub entries omit the flag -> force it on.
    tls=o.get("tls")
    if isinstance(tls,dict) and isinstance(tls.get("reality"),dict) and tls["reality"].get("enabled"):
        u=tls.get("utls") if isinstance(tls.get("utls"),dict) else {}
        tls["utls"]={"enabled":True,"fingerprint":u.get("fingerprint") or "chrome"}
    return o

# ---- share-link (vless:// vmess:// trojan:// ss:// hysteria2:// tuic://) parsing ----
def _unquote(s):
    out=[]; i=0
    while i < len(s):
        if s[i]=="%" and i+2 < len(s):
            try: out.append(chr(int(s[i+1:i+3],16))); i+=3; continue
            except ValueError: pass
        out.append(s[i]); i+=1
    return "".join(out)

def _qs(q):
    d={}
    for kv in q.split("&"):
        if not kv: continue
        k,_,v=kv.partition("=")
        d[k.lower()]=_unquote(v)
    return d

def _b64d(s):
    s=re.sub(r"\s+","",s).replace("-","+").replace("_","/")
    return base64.b64decode(s+"="*(-len(s)%4)).decode("utf-8","replace")

def _hostport(hp,default=443):
    if hp.startswith("["):                       # [ipv6]:port
        host,_,port=hp[1:].partition("]"); port=port.lstrip(":")
    else:
        host,_,port=hp.rpartition(":")
        if not host: host=port; port=""
    return host, int(port) if port.isdigit() else default

def _split(u):
    scheme,rest=u.split("://",1)
    frag=""
    if "#" in rest: rest,frag=rest.split("#",1); frag=_unquote(frag)
    query=""
    if "?" in rest: rest,query=rest.split("?",1)
    userinfo=""
    if "@" in rest: userinfo,hostport=rest.rsplit("@",1)
    else: hostport=rest
    host,port=_hostport(hostport)
    return scheme.lower(), userinfo, host, port, _qs(query), frag

def _tls_from(p, host):
    sec=(p.get("security") or "").lower()
    if sec not in ("tls","reality","xtls") and not p.get("pbk"): return None
    tls={"enabled":True}
    sni=p.get("sni") or p.get("peer") or p.get("host") or host
    if sni: tls["server_name"]=sni
    if p.get("alpn"): tls["alpn"]=[a for a in p["alpn"].split(",") if a]
    if p.get("insecure") in ("1","true") or p.get("allowinsecure") in ("1","true"): tls["insecure"]=True
    fp=p.get("fp") or "chrome"
    if p.get("pbk"):
        tls["reality"]={"enabled":True,"public_key":p["pbk"]}
        if p.get("sid"): tls["reality"]["short_id"]=p["sid"]
        tls["utls"]={"enabled":True,"fingerprint":fp}
    elif p.get("fp"):
        tls["utls"]={"enabled":True,"fingerprint":fp}
    return tls

def _transport_from(p):
    net=(p.get("type") or p.get("net") or "tcp").lower()
    if net in ("ws","httpupgrade"):
        t={"type":"ws" if net=="ws" else "httpupgrade"}
        if p.get("path"): t["path"]=p["path"]
        if p.get("host"): t["headers"]={"Host":p["host"]}
        return t
    if net=="grpc":
        return {"type":"grpc","service_name":p.get("servicename") or p.get("path") or ""}
    if net in ("http","h2"):
        t={"type":"http"}
        if p.get("host"): t["host"]=[h for h in p["host"].split(",") if h]
        if p.get("path"): t["path"]=p["path"]
        return t
    return None

def parse_link(u):
    u=u.strip()
    try:
        scheme=u.split("://",1)[0].lower()
        if scheme=="vless":
            _,ui,host,port,q,name=_split(u)
            o={"type":"vless","tag":name or host,"server":host,"server_port":port,"uuid":ui,"packet_encoding":"xudp"}
            if q.get("flow"): o["flow"]=q["flow"]
            t=_tls_from(q,host);  o["tls"]=t if t else o.get("tls")
            tr=_transport_from(q)
            if tr: o["transport"]=tr
            return o
        if scheme=="trojan":
            _,ui,host,port,q,name=_split(u)
            o={"type":"trojan","tag":name or host,"server":host,"server_port":port,"password":ui}
            o["tls"]=_tls_from(q,host) or {"enabled":True,"server_name":q.get("sni") or host}
            tr=_transport_from(q)
            if tr: o["transport"]=tr
            return o
        if scheme in ("hysteria2","hy2"):
            _,ui,host,port,q,name=_split(u)
            o={"type":"hysteria2","tag":name or host,"server":host,"server_port":port,"password":ui}
            tls={"enabled":True,"server_name":q.get("sni") or host}
            if q.get("insecure") in ("1","true"): tls["insecure"]=True
            o["tls"]=tls
            ob=q.get("obfs")
            if ob and ob.lower()!="none":
                o["obfs"]={"type":ob,"password":q.get("obfs-password") or q.get("obfs_password") or ""}
            return o
        if scheme=="tuic":
            _,ui,host,port,q,name=_split(u)
            uuid,_,pw=ui.partition(":")
            o={"type":"tuic","tag":name or host,"server":host,"server_port":port,"uuid":uuid,"password":_unquote(pw)}
            tls={"enabled":True,"server_name":q.get("sni") or host}
            if q.get("alpn"): tls["alpn"]=[a for a in q["alpn"].split(",") if a]
            if q.get("allow_insecure") in ("1","true") or q.get("insecure") in ("1","true"): tls["insecure"]=True
            o["tls"]=tls
            if q.get("congestion_control"): o["congestion_control"]=q["congestion_control"]
            if q.get("udp_relay_mode"): o["udp_relay_mode"]=q["udp_relay_mode"]
            return o
        if scheme=="vmess":
            body=u.split("://",1)[1].split("#",1)[0]
            j=json.loads(_b64d(body))
            host=j.get("add"); port=int(j.get("port",443) or 443)
            o={"type":"vmess","tag":j.get("ps") or host,"server":host,"server_port":port,
               "uuid":j.get("id"),"security":j.get("scy") or "auto","alter_id":int(j.get("aid",0) or 0)}
            p={"type":j.get("net","tcp"),"host":j.get("host",""),"path":j.get("path",""),
               "sni":j.get("sni",""),"alpn":j.get("alpn",""),"fp":j.get("fp",""),"servicename":j.get("path",""),
               "security":"tls" if str(j.get("tls","")).lower() in ("tls","1","true","reality") else ""}
            t=_tls_from(p,host)
            if t: o["tls"]=t
            tr=_transport_from(p)
            if tr: o["transport"]=tr
            return o
        if scheme=="ss":
            body=u.split("://",1)[1]; frag=""
            if "#" in body: body,frag=body.split("#",1); frag=_unquote(frag)
            body=body.split("?",1)[0]
            if "@" in body:
                ui,hp=body.rsplit("@",1)
                try: method,pw=_b64d(ui).split(":",1)
                except Exception: method,pw=ui.split(":",1)
                host,port=_hostport(hp)
            else:
                dec=_b64d(body); mp,hp=dec.rsplit("@",1)
                method,pw=mp.split(":",1); host,port=_hostport(hp)
            return {"type":"shadowsocks","tag":frag or host,"server":host,"server_port":port,"method":method,"password":pw}
    except Exception as e:
        log("link parse skip:", u[:36], str(e)[:60])
    return None

def extract_proxies(raw):
    # 1) sing-box JSON (tolerates // jsonc headers)
    try:
        d=json.loads(strip_jsonc(raw))
        obs=d.get("outbounds",d) if isinstance(d,dict) else d
        if isinstance(obs,list):
            got=[sanitize(o) for o in obs
                 if isinstance(o,dict) and o.get("type") in PROXY_TYPES and o.get("tag")]
            if got: return got
    except Exception: pass
    # 2) base64 blob OR plaintext list of share links
    text=raw.strip()
    if "://" not in text:
        try:
            dec=_b64d(text)
            if "://" in dec: text=dec
        except Exception: pass
    out=[]; seen=set()
    for ln in re.split(r"[\r\n\s]+", text):
        if "://" not in ln: continue
        o=parse_link(ln)
        if not (o and o.get("server") and o.get("tag")): continue
        t=o["tag"]
        if t in seen:
            k=2
            while f"{t}-{k}" in seen: k+=1
            t=f"{t}-{k}"; o["tag"]=t
        seen.add(t); out.append(sanitize(o))
    return out

def write_if_changed(path, data):
    new=data if isinstance(data,(bytes,)) else data.encode()
    try:
        if open(path,"rb").read()==new: return False
    except FileNotFoundError: pass
    os.makedirs(os.path.dirname(path),exist_ok=True)
    tmp=path+".tmp"; open(tmp,"wb").write(new); os.replace(tmp,path)
    return True

def cap(px, name, maxn):
    if maxn and len(px) > maxn:
        log(name, "capped", len(px), "->", maxn, "nodes (raised max_nodes to change)")
        return px[:maxn]
    return px

def get_sub_proxies(name,url,maxn):
    """Return (proxies, source). Try network -> update disk lastgood; else disk."""
    lg=LAST+"/"+name+".json"
    try:
        raw=fetch(url)
        px=cap(extract_proxies(raw), name, maxn)
        if px:
            changed=write_if_changed(lg, json.dumps(px,ensure_ascii=False))
            return px, ("net"+("*" if changed else ""))
        log(name,"fetched but 0 proxies; falling back")
    except Exception as e:
        log(name,"fetch failed:",str(e)[:120])
    try:
        return cap(json.load(open(lg)), name, maxn), "disk"
    except Exception as e:
        log(name,"no lastgood:",e); return [], "empty"

ENDPOINT_TYPES=("wireguard","tailscale")   # sing-box 'endpoints[]'; everything else is an outbound

def load_static():
    """Static nodes from disk (always available, even with GitHub down). Routes
    each by type: wireguard/tailscale -> endpoints[], the rest -> outbounds[]."""
    eps=[]; obs=[]; d=ETC+"/static"
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"): continue
            try:
                o=json.load(open(d+"/"+fn))
                if not (isinstance(o,dict) and o.get("tag") and o.get("type")):
                    log("static",fn,"missing tag/type; skipped"); continue
                (eps if o["type"] in ENDPOINT_TYPES else obs).append(o)
            except Exception as e: log("static",fn,"skip:",e)
    return eps,obs

def build():
    st=load_settings(); subs=load_subs()
    static_eps,static_obs=load_static()
    ep_tags=[x["tag"] for x in static_eps+static_obs]
    maxn=int(st.get("max_nodes",120) or 0)
    all_nodes=[]; groups=[]; sub_tags=[]; report=[]
    for name,url in subs:
        px,src=get_sub_proxies(name,url,maxn)
        node_tags=[]
        for o in px:
            o=dict(o); o["tag"]=f"{name}│{o['tag']}"   # namespace: name│origtag
            all_nodes.append(o); node_tags.append(o["tag"])
        gtag=f"sub:{name}"; sub_tags.append(gtag)
        if node_tags:
            groups.append({"type":"urltest","tag":gtag,"outbounds":node_tags,
                "url":st["healthcheck_url"],"interval":st["interval"],
                "tolerance":st["tolerance"],"idle_timeout":"30m0s"})
        else:
            # empty sub still needs a valid outbound; point at direct so config stays valid
            groups.append({"type":"selector","tag":gtag,"outbounds":["direct"]})
        report.append(f"{name}:{len(node_tags)}({src})")

    # active = the main/default outbound. Accepts a sub name (-> sub:<name>),
    # a static endpoint tag (e.g. amnezia), a raw sub:<name>, or direct.
    sub_names={n for n,_ in subs}
    active=st.get("active") or st.get("default_sub") or (subs[0][0] if subs else "direct")
    if active in sub_names:            default_tag="sub:"+active
    elif active.startswith("sub:"):    default_tag=active
    elif active in ep_tags or active=="direct": default_tag=active
    else:                              default_tag=("sub:"+subs[0][0]) if subs else "direct"
    top={"type":"selector","tag":"PROXY",
         "outbounds":sub_tags+ep_tags+["direct"],
         "default":default_tag}

    cfg={
      "log":{"level":"warn","timestamp":True},
      "experimental":{
        "clash_api":{"external_controller":st["controller"],"secret":st["secret"],
                     "external_ui":UIDIR,"default_mode":"rule"},
        "cache_file":{"enabled":True,"path":RUN+"/cache.db"}
      },
      "dns":{"servers":[{"type":"local","tag":"local"}],"final":"local"},
      "inbounds":[{"type":"mixed","tag":"mixed-in","listen":"127.0.0.1",
                   "listen_port":int(st["socks_port"])}],
      "endpoints":static_eps,
      "outbounds":[top]+groups+all_nodes+static_obs+[
        {"type":"direct","tag":"direct"}],
      "route":{"final":"PROXY","auto_detect_interface":True,
               "default_domain_resolver":{"server":"local"},"rules":[]}
    }
    if ep_tags: report.append("static["+",".join(ep_tags)+"]")
    os.makedirs(RUN,exist_ok=True)
    open(CFG,"w").write(json.dumps(cfg,ensure_ascii=False,indent=1))
    log("built:", " ".join(report), "-> default",default_tag)
    print(" ".join(report))

if __name__=="__main__":
    build()
