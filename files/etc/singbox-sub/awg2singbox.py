#!/usr/bin/env python3
# Convert an AmneziaWG / WireGuard wg-quick config (stdin) into a sing-box 1.14
# wireguard endpoint JSON (stdout). Usage: awg2singbox.py <tag>
import sys, json, re

def parse(text):
    sec=None; iface={}; peer={}
    for raw in text.splitlines():
        ln=raw.split("#",1)[0].strip()
        if not ln: continue
        low=ln.lower()
        if low.startswith("[interface]"): sec="i"; continue
        if low.startswith("[peer]"):      sec="p"; continue
        if "=" not in ln: continue
        k,v=ln.split("=",1); k=k.strip(); v=v.strip()
        (iface if sec=="i" else peer)[k.lower()]=v
    return iface,peer

def ints(iface,ep,keys):
    for k in keys:
        if iface.get(k):
            try: ep[k]=int(iface[k])
            except ValueError: pass

def main():
    tag=sys.argv[1] if len(sys.argv)>1 else "wg"
    iface,peer=parse(sys.stdin.read())
    ep={"type":"wireguard","tag":tag,"system":False,
        "mtu":int(iface.get("mtu",1280) or 1280),   # 1280 = safe over AWG (avoids browser MTU blackhole)
        "address":[a.strip() for a in iface.get("address","").split(",") if a.strip()],
        "private_key":iface.get("privatekey","")}
    p={"public_key":peer.get("publickey",""),
       "allowed_ips":[a.strip() for a in peer.get("allowedips","0.0.0.0/0, ::/0").split(",") if a.strip()]}
    if peer.get("presharedkey"): p["pre_shared_key"]=peer["presharedkey"]
    m=re.match(r"\[?([^\]]+)\]?:(\d+)$", peer.get("endpoint","").strip())
    if m: p["address"]=m.group(1); p["port"]=int(m.group(2))
    if peer.get("persistentkeepalive"):
        try: p["persistent_keepalive_interval"]=int(peer["persistentkeepalive"])
        except ValueError: pass
    ep["peers"]=[p]
    # AmneziaWG obfuscation params
    ints(iface,ep,["jc","jmin","jmax","s1","s2","s3","s4"])
    for h in ("h1","h2","h3","h4"):
        if iface.get(h): ep[h]=iface[h]            # MagicHeader (may be a range)
    for i in ("i1","i2","i3","i4","i5","id","ip","ib"):
        if iface.get(i): ep[i]=iface[i]
    if not ep["private_key"] or not p.get("address") or not p["public_key"]:
        sys.stderr.write("missing PrivateKey / Endpoint / PublicKey\n"); sys.exit(2)
    json.dump(ep, sys.stdout, ensure_ascii=False, indent=1)

if __name__=="__main__":
    main()
