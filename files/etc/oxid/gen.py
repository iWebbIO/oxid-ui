#!/usr/bin/env python3
# oxid generator: read settings + subscriptions from UCI, fetch and parse
# each subscription, and merge everything into ONE runtime config in RAM (/tmp),
# with a per-sub last-good disk cache for when a source is unreachable. Switching
# subs is done live via clash_api (no regen); this script runs on refresh / boot.
import json, re, subprocess, os, sys, hashlib, base64

ETC   = "/etc/oxid"
RUN   = "/tmp/oxid"
LAST  = ETC + "/lastgood"          # disk cache (tiny, change-gated -> no flash churn)
UIDIR = ETC + "/ui"
CFG   = RUN + "/config.json"

PROXY_TYPES = ("vless","vmess","trojan","shadowsocks","hysteria2","tuic",
               "shadowtls","anytls","wireguard","http","socks")

def log(*a): print("[gen]", *a, file=sys.stderr)

os.makedirs(RUN, exist_ok=True)   # tmpfs is empty after reboot; ensure it exists early

_UCI_CACHE=None
def uci_model():
    """Parse `uci -q show oxid` into {section_id: {_type, opt: value...}}.
    List options (member/hop) become Python lists. Cached per process."""
    global _UCI_CACHE
    if _UCI_CACHE is not None: return _UCI_CACHE
    secs={}
    try:
        out=subprocess.check_output(["uci","-q","show","oxid"],
                                    stderr=subprocess.DEVNULL).decode("utf-8","replace")
    except Exception:
        _UCI_CACHE=secs; return secs
    for ln in out.splitlines():
        m=re.match(r"^oxid\.([^.=]+)(?:\.([^=]+))?=(.*)$", ln)
        if not m: continue
        sec,opt,val=m.group(1),m.group(2),m.group(3)
        d=secs.setdefault(sec,{})
        if opt is None:
            d["_type"]=val.strip().strip("'")
        else:
            vals=re.findall(r"'([^']*)'", val)
            nv=vals if len(vals)>1 else (vals[0] if vals else val.strip())
            if opt in d:  # repeated option (list rendered across lines) -> accumulate
                cur=d[opt] if isinstance(d[opt],list) else [d[opt]]
                d[opt]=cur+(nv if isinstance(nv,list) else [nv])
            else:
                d[opt]=nv
    _UCI_CACHE=secs; return secs

def by_type(t):
    """Sections of a given type, keyed by section id (insertion order preserved)."""
    return {k:v for k,v in uci_model().items() if v.get("_type")==t}

def aslist(x):
    return x if isinstance(x,list) else ([x] if x else [])

def load_settings():
    d = {"controller":"0.0.0.0:9090","secret":"","socks_port":7890,
         "healthcheck_url":"https://www.gstatic.com/generate_204",
         "interval":"3m0s","tolerance":100,"active":"","max_nodes":120,
         "stable_switch":"1"}
    main=uci_model().get("main",{})
    for k in ("controller","secret","socks_port","healthcheck_url",
              "interval","max_nodes","active","stable_switch","tolerance"):
        if main.get(k): d[k]=main[k]
    return d

def load_subs():
    subs=[]
    for v in by_type("subscription").values():
        if v.get("enabled","1")=="1" and v.get("name") and v.get("url"):
            subs.append((v["name"], v["url"]))
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

def stable(d, st):
    # keep live connections when a selector/urltest changes its pick
    if str(st.get("stable_switch","1"))!="0":
        d["interrupt_exist_connections"]=False
    return d

def emit_node(nid, n):
    """Typed manual node -> sing-box outbound (tag node:<id>). A node may carry a
    share-link (any protocol) or typed fields for the common exit types."""
    tag="node:"+nid
    link=n.get("link")
    if link:
        o=parse_link(link)
        if o: o=dict(o); o["tag"]=tag; return sanitize(o)
        log("node",nid,"bad link"); return None
    typ=(n.get("type") or "socks").lower()
    server=n.get("server")
    try: port=int(n.get("port") or 0)
    except ValueError: port=0
    if not server or not port: log("node",nid,"missing server/port"); return None
    if typ=="socks":
        o={"type":"socks","tag":tag,"server":server,"server_port":port,"version":"5"}
        if n.get("username"): o["username"]=n["username"]
        if n.get("password"): o["password"]=n["password"]
    elif typ=="http":
        o={"type":"http","tag":tag,"server":server,"server_port":port}
        if n.get("username"): o["username"]=n["username"]
        if n.get("password"): o["password"]=n["password"]
        if n.get("tls")=="1": o["tls"]={"enabled":True,"server_name":n.get("sni") or server}
    elif typ in ("shadowsocks","ss"):
        o={"type":"shadowsocks","tag":tag,"server":server,"server_port":port,
           "method":n.get("method") or "aes-128-gcm","password":n.get("password") or ""}
    else:
        log("node",nid,"unsupported typed protocol",typ,"- use a share-link"); return None
    return o

def resolve_member(m, subnames):
    """A UI member ref (sub:/node:/group:/chain:/static: or a bare name) -> config tag."""
    m=(m or "").strip()
    if m in ("direct","block"): return m
    if ":" in m:
        pre,rest=m.split(":",1)
        return {"sub":"sub:"+rest,"node":"node:"+rest,"group":"grp:"+rest,
                "chain":"chain:"+rest,"static":rest}.get(pre, m)
    return ("sub:"+m) if m in subnames else m

def emit_group(gid, g, st, subnames):
    kind=(g.get("kind") or "urltest").lower()
    members=[resolve_member(x,subnames) for x in aslist(g.get("member"))]
    members=[x for x in members if x] or ["direct"]
    tag="grp:"+gid
    if kind in ("select","selector","manual"):
        return stable({"type":"selector","tag":tag,"outbounds":members,"default":members[0]}, st)
    return stable({"type":"urltest","tag":tag,"outbounds":members,
                   "url":g.get("test_url") or st["healthcheck_url"],
                   "interval":g.get("interval") or st["interval"],
                   "tolerance":int(g.get("tolerance") or st["tolerance"]),
                   "idle_timeout":"30m0s"}, st)

def emit_chain(cid, c, node_out, subnames, st):
    """Proxy chain: hop[0]=entry (any selectable), hop[1..]=proxy nodes; each hop
    dials THROUGH the previous one, so the LAST hop is the exit. Tag chain:<id>."""
    hops=aslist(c.get("hop"))
    if not hops: return None,[]
    prev=resolve_member(hops[0], subnames); extra=[]
    for i,h in enumerate(hops[1:],1):
        if not h.startswith("node:"):
            log("chain",cid,"hop must be a node (only the entry can be a sub/group):",h); return None,[]
        base=node_out.get(h.split(":",1)[1])
        if not base: log("chain",cid,"unknown node",h); return None,[]
        clone=dict(base); clone["detour"]=prev
        clone["tag"]="chain:"+cid if i==len(hops)-1 else f"chain:{cid}:h{i}"
        extra.append(clone); prev=clone["tag"]
    if len(hops)==1:  # single hop: wrap so chain:<id> is a valid selectable tag
        extra.append(stable({"type":"selector","tag":"chain:"+cid,"outbounds":[prev],"default":prev}, st))
    return prev,extra

def build():
    st=load_settings(); subs=load_subs()
    static_eps,static_obs=load_static()
    maxn=int(st.get("max_nodes",120) or 0)
    subnames={n for n,_ in subs}
    report=[]

    # 1) subscriptions -> per-sub urltest group + their nodes
    all_nodes=[]; sub_groups=[]; sub_tags=[]
    for name,url in subs:
        px,src=get_sub_proxies(name,url,maxn)
        node_tags=[]
        for o in px:
            o=dict(o); o["tag"]=f"{name}│{o['tag']}"   # namespace: name│origtag
            all_nodes.append(o); node_tags.append(o["tag"])
        gtag="sub:"+name; sub_tags.append(gtag)
        if node_tags:
            sub_groups.append(stable({"type":"urltest","tag":gtag,"outbounds":node_tags,
                "url":st["healthcheck_url"],"interval":st["interval"],
                "tolerance":int(st["tolerance"]),"idle_timeout":"30m0s"}, st))
        else:
            sub_groups.append({"type":"selector","tag":gtag,"outbounds":["direct"]})
        report.append(f"{name}:{len(node_tags)}({src})")

    # 2) typed manual nodes -> outbounds
    node_out={}; node_obs=[]
    for nid,n in by_type("node").items():
        o=emit_node(nid,n)
        if o: node_out[nid]=o; node_obs.append(o)

    # 3) custom groups (balancers / manual selectors)
    grp_obs=[emit_group(gid,g,st,subnames) for gid,g in by_type("group").items()]

    # 4) chains (proxy chaining via detour; last hop = exit)
    chain_obs=[]; chain_tags=[]
    for cid,c in by_type("chain").items():
        final,extra=emit_chain(cid,c,node_out,subnames,st)
        if final: chain_obs+=extra; chain_tags.append("chain:"+cid)

    ep_tags=[x["tag"] for x in static_eps+static_obs]
    grp_tags=[g["tag"] for g in grp_obs]
    node_sel=["node:"+nid for nid in node_out]
    selectable=sub_tags+grp_tags+chain_tags+node_sel+ep_tags+["direct"]
    valid=set(selectable)

    # resolve the active/default outbound against everything selectable
    active=st.get("active") or (subs[0][0] if subs else "direct")
    default_tag=resolve_member(active, subnames)
    if default_tag not in valid:
        default_tag=("sub:"+active) if ("sub:"+active) in valid else (selectable[0] if selectable else "direct")
    top=stable({"type":"selector","tag":"PROXY","outbounds":selectable,"default":default_tag}, st)

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
      "outbounds":[top]+sub_groups+grp_obs+chain_obs+all_nodes+node_obs+static_obs+[
        {"type":"direct","tag":"direct"}],
      "route":{"final":"PROXY","auto_detect_interface":True,
               "default_domain_resolver":{"server":"local"},"rules":[]}
    }
    if node_obs:   report.append(f"nodes:{len(node_obs)}")
    if grp_obs:    report.append(f"groups:{len(grp_obs)}")
    if chain_tags: report.append("chains["+",".join(t.split(':',1)[1] for t in chain_tags)+"]")
    if ep_tags:    report.append("static["+",".join(ep_tags)+"]")
    os.makedirs(RUN,exist_ok=True)
    open(CFG,"w").write(json.dumps(cfg,ensure_ascii=False,indent=1))
    log("built:", " ".join(report), "-> default",default_tag)
    print(" ".join(report))

if __name__=="__main__":
    build()
