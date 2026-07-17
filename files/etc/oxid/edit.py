#!/usr/bin/env python3
# oxid UCI editor: the write-side the manager UI drives. Every mutation goes
# through here so nothing is hand-edited. Reads a verb (argv[1]) + JSON on stdin,
# performs the uci ops, commits, and prints a small JSON result. `dump` is the
# read side: the whole typed model (subs/nodes/groups/chains/statics) as JSON.
import json, os, re, subprocess, sys

def sh(*args):
    return subprocess.run(["uci"]+list(args), capture_output=True, text=True)

def get_model():
    """Parse `uci -q show oxid` -> {sid: {_type, opt: value|list}}."""
    secs={}
    out=subprocess.run(["uci","-q","show","oxid"], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        m=re.match(r"^oxid\.([^.=]+)(?:\.([^=]+))?=(.*)$", ln)
        if not m: continue
        sid,opt,val=m.group(1),m.group(2),m.group(3)
        d=secs.setdefault(sid,{"_id":sid})
        if opt is None:
            d["_type"]=val.strip().strip("'")
        else:
            vals=re.findall(r"'([^']*)'", val)
            nv=vals if len(vals)>1 else (vals[0] if vals else val.strip())
            if opt in d:
                cur=d[opt] if isinstance(d[opt],list) else [d[opt]]
                d[opt]=cur+(nv if isinstance(nv,list) else [nv])
            else:
                d[opt]=nv
    return secs

def by_type(model,t):
    return {k:v for k,v in model.items() if v.get("_type")==t}

def slug(s, fallback):
    s=re.sub(r"[^a-zA-Z0-9_]","_", (s or "").strip())
    s=re.sub(r"_+","_", s).strip("_")
    if not s or s[0].isdigit(): s=(fallback+"_"+s).strip("_")
    return s or fallback

def find_sub(model, name):
    for sid,v in by_type(model,"subscription").items():
        if v.get("name")==name: return sid
    return None

def ok(msg, **extra):
    print(json.dumps({"ok":True,"msg":msg, **extra})); sys.exit(0)
def err(msg):
    print(json.dumps({"ok":False,"msg":msg})); sys.exit(1)

def commit():
    sh("commit","oxid")

def statics():
    out=[]; d="/etc/oxid/static"
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json"): continue
            try:
                o=json.load(open(d+"/"+f))
                out.append({"tag":o.get("tag",f[:-5]),"type":o.get("type","?")})
            except Exception:
                out.append({"tag":f[:-5],"type":"?"})
    return out

# ---- read side ----
def dump():
    m=get_model()
    subs=[{"id":sid,"name":v.get("name",""),"url":v.get("url",""),
           "enabled":v.get("enabled","1")}
          for sid,v in by_type(m,"subscription").items()]
    nodes=[{"id":sid,"label":v.get("label",sid),"type":v.get("type",""),
            "link":v.get("link",""),"server":v.get("server",""),"port":v.get("port",""),
            "username":v.get("username",""),"password":v.get("password",""),
            "method":v.get("method",""),"sni":v.get("sni",""),"tls":v.get("tls","")}
           for sid,v in by_type(m,"node").items()]
    def L(x): return x if isinstance(x,list) else ([x] if x else [])
    groups=[{"id":sid,"label":v.get("label",sid),"kind":v.get("kind","urltest"),
             "members":L(v.get("member")),"tolerance":v.get("tolerance",""),
             "interval":v.get("interval","")}
            for sid,v in by_type(m,"group").items()]
    chains=[{"id":sid,"label":v.get("label",sid),"hops":L(v.get("hop"))}
            for sid,v in by_type(m,"chain").items()]
    main=m.get("main",{})
    print(json.dumps({"subs":subs,"nodes":nodes,"groups":groups,"chains":chains,
                      "statics":statics(),
                      "active":main.get("active",""),
                      "settings":{k:main.get(k,"") for k in
                        ("socks_port","controller","max_nodes","interval",
                         "healthcheck_url","hourly","watchdog")}}))

# ---- write side ----
def read_body():
    raw=sys.stdin.read()
    try: return json.loads(raw) if raw.strip() else {}
    except Exception: err("bad JSON body")

def set_opts(sid, opts):
    """Scalar options only. Empty value deletes the option. List options (member/
    hop) go through add_list() instead."""
    for k,v in opts.items():
        if v in (None,""):
            sh("delete","oxid.%s.%s"%(sid,k))
        else:
            sh("set","oxid.%s.%s=%s"%(sid,k,v))

def add_list(sid, opt, items):
    sh("delete","oxid.%s.%s"%(sid,opt))
    for it in items:
        if str(it).strip(): sh("add_list","oxid.%s.%s=%s"%(sid,opt,str(it)))

# testable tree: every outbound the clash-api can probe, grouped for the Test page.
# Reads the live RAM config so subscriptions expand into their member nodes.
def tags():
    try: cfg=json.load(open("/tmp/oxid/config.json"))
    except Exception: cfg={"outbounds":[],"endpoints":[]}
    obs=cfg.get("outbounds",[])
    def nice(t):  # "reality│Amsterdam #3" -> "Amsterdam #3"
        return t.split("│",1)[1] if "│" in t else t
    subs=[]; bals=[]
    for o in obs:
        t=o.get("tag","")
        mem=[{"tag":x,"label":nice(x)} for x in o.get("outbounds",[]) if x!="direct"]
        if t.startswith("sub:"):  subs.append({"tag":t,"label":t[4:],"members":mem})
        elif t.startswith("grp:"): bals.append({"tag":t,"label":t[4:],"members":mem})
    chains=[{"tag":o["tag"],"label":o["tag"][6:]} for o in obs
            if o.get("tag","").startswith("chain:") and ":h" not in o.get("tag","")]
    nodes=[{"tag":o["tag"],"label":o["tag"][5:]} for o in obs
           if o.get("tag","").startswith("node:")]
    tunnels=[{"tag":s["tag"],"label":s["tag"]} for s in statics()]
    print(json.dumps({"subscriptions":subs,"balancers":bals,"chains":chains,
                      "nodes":nodes,"tunnels":tunnels,"direct":True}))

def main():
    verb=sys.argv[1] if len(sys.argv)>1 else ""
    if verb=="dump": return dump()
    if verb=="tags": return tags()
    b=read_body()
    m=get_model()

    if verb=="sub-add":
        name=(b.get("name") or "").strip(); url=(b.get("url") or "").strip()
        if not name or not url: err("name and url required")
        if find_sub(m,name): err("a subscription named '%s' already exists"%name)
        sid=sh("add","oxid","subscription").stdout.strip()
        if not sid: err("uci add failed")
        set_opts(sid,{"name":name,"url":url,"enabled":"1" if b.get("enabled","1")!="0" else "0"})
        commit(); ok("added subscription "+name)

    if verb=="sub-del":
        sid=find_sub(m,b.get("name",""))
        if not sid: err("no such subscription")
        sh("delete","oxid."+sid); commit(); ok("removed "+b.get("name",""))

    if verb=="sub-toggle":
        sid=find_sub(m,b.get("name",""))
        if not sid: err("no such subscription")
        sh("set","oxid.%s.enabled=%s"%(sid,"1" if b.get("enabled")=="1" else "0"))
        commit(); ok("toggled "+b.get("name",""))

    if verb=="node-set":
        nid=slug(b.get("id") or b.get("label"), "node")
        existing=m.get(nid,{}).get("_type")
        if existing not in (None,"node"):
            err("id '%s' is already a %s — pick another name"%(nid,existing))
        sh("set","oxid.%s=node"%nid)
        opts={"label":b.get("label") or nid}
        link=(b.get("link") or "").strip()
        if link:
            opts.update({"link":link,"type":"","server":"","port":"","username":"",
                         "password":"","method":"","sni":"","tls":""})
        else:
            opts.update({"link":"","type":(b.get("type") or "socks").lower(),
                         "server":(b.get("server") or "").strip(),
                         "port":str(b.get("port") or "").strip(),
                         "username":b.get("username") or "","password":b.get("password") or "",
                         "method":b.get("method") or "","sni":b.get("sni") or "",
                         "tls":"1" if b.get("tls") in (True,"1",1) else ""})
            if not opts["server"] or not opts["port"]: err("server and port required for a manual node")
        set_opts(nid,opts); commit(); ok("saved node "+nid, id=nid)

    if verb=="node-del":
        nid=b.get("id","")
        if m.get(nid,{}).get("_type")!="node": err("no such node")
        sh("delete","oxid."+nid); commit(); ok("removed node "+nid)

    if verb=="group-set":
        gid=slug(b.get("id") or b.get("label"), "grp")
        members=[x for x in (b.get("members") or []) if str(x).strip()]
        if len(members)<1: err("a balancer needs at least one member")
        kind=(b.get("kind") or "urltest").lower()
        sh("set","oxid.%s=group"%gid)
        opts={"label":b.get("label") or gid,"kind":kind,
              "tolerance":str(b.get("tolerance") or ""),"interval":b.get("interval") or ""}
        set_opts(gid,opts); add_list(gid,"member",members)
        commit(); ok("saved balancer "+gid, id=gid)

    if verb=="group-del":
        gid=b.get("id","")
        if m.get(gid,{}).get("_type")!="group": err("no such balancer")
        sh("delete","oxid."+gid); commit(); ok("removed balancer "+gid)

    if verb=="chain-set":
        cid=slug(b.get("id") or b.get("label"), "chain")
        hops=[x for x in (b.get("hops") or []) if str(x).strip()]
        if len(hops)<2: err("a chain needs an entry plus at least one exit node")
        if not all(h.startswith("node:") for h in hops[1:]):
            err("every hop after the entry must be a node (only the entry may be a sub/balancer)")
        sh("set","oxid.%s=chain"%cid)
        set_opts(cid,{"label":b.get("label") or cid}); add_list(cid,"hop",hops)
        commit(); ok("saved chain "+cid, id=cid)

    if verb=="chain-del":
        cid=b.get("id","")
        if m.get(cid,{}).get("_type")!="chain": err("no such chain")
        sh("delete","oxid."+cid); commit(); ok("removed chain "+cid)

    err("unknown verb: "+verb)

if __name__=="__main__":
    main()
