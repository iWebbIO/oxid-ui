# oxid — Overhaul Roadmap

> **The best polished OpenWRT v2ray experience.** A Hiddify-class manager that turns
> subscriptions, nodes, balancers, chains and routes into one lightweight sing-box
> engine — and hands the result to passwall2 (or anything) as a clean SOCKS.

This document is the north star for taking oxid from "working scripts + a CBI form"
to a mature product. It is opinionated on purpose.

---

## 0. What's wrong with the current panel (honest audit)

| Problem | Why it hurts |
|---|---|
| Raw sing-box JSON / raw AmneziaWG fields exposed in the UI | Users must understand sing-box internals. Not "normal." |
| Classic LuCI CBI look | Functional, but reads as a config form, not a product. |
| Flat model: `subscription` + `static JSON files` | No concept of nodes, groups, balancers, chains, or routes. |
| One global `active` selector | Can't compose ("balance sub A, exit through socks5 B"). |
| Node switching can interrupt live flows | Load-balancer changes hurt the connection. |
| `singbox-sub` naming everywhere | No product identity. |
| Tarball install | No clean install/update/removal story. |

The engine underneath (RAM config, graceful staging, offline cache, watchdog, AWG
fork) is solid. **We keep the engine and rebuild the model, the API, and the UI on top.**

---

## 1. Product principles

1. **Guided, never raw.** You paste a share-link / subscription URL / `wg-quick`
   block, or fill a typed form. You never hand-edit sing-box JSON or AWG magic
   headers to get a working node. (An "Expert" drawer stays for power users.)
2. **Nodes → Groups → Routes.** The mental model every v2ray user already has.
3. **Switching never drops you.** Changing a balancer's pick or failing over must
   preserve existing connections. This is a hard guarantee, not a nicety.
4. **RAM-first, flash-frugal.** Node lists live in tmpfs; flash holds only the tiny
   typed model. Refreshes never churn flash.
5. **Reboot-safe & self-healing.** Survives reboots, source outages, and bad configs
   without manual recovery.
6. **Plays nice with passwall2.** oxid is the brain (compose + balance + chain +
   route → SOCKS). passwall2 stays the traffic-capture layer. No nftables fights.

---

## 2. Target architecture

```
            ┌──────────────────── LuCI JS app (luci-app-oxid) ────────────────────┐
            │  Dashboard · Nodes · Subscriptions · Groups · Routing · Settings     │
            │  (polished client-side views, design system, latency bars, wizards)  │
            └───────────────────────────────┬─────────────────────────────────────┘
                                            │ ubus/rpcd  (typed JSON API)
                                            ▼
                       ┌──────────────  oxid backend  ──────────────┐
                       │  rpcd object `oxid`  +  `oxid` CLI          │
                       │  · model CRUD (UCI)   · import/parse        │
                       │  · compile → config   · switch/apply/stage  │
                       │  · live status/latency (clash API proxy)    │
                       └───────────────┬─────────────────┬──────────┘
                                       │ compile         │ live control
                                       ▼                 ▼
                    /etc/config/oxid (typed model)   clash API :9090
                                       │                 ▲
                                       ▼                 │
                       oxid-gen  ──►  ONE config.json (RAM)  ──►  sing-box-awg
                                                                    │  SOCKS :7890 ─► passwall2
                                                                    │  clash API :9090 ─► zashboard
                                                                    ▼
                              nodes / balancers / chains / rules → internet
```

Two surfaces, one engine:
- **LuCI JS app** for humans (polished, integrated, authenticated by LuCI).
- **zashboard** stays as the live "power" telemetry view (traffic graph, conns).

---

## 3. Data model (typed UCI `/etc/config/oxid`)

The whole product is a compiler over this model. Subscription **nodes never enter
UCI** (RAM only); everything below is small and static.

```
config oxid 'main'
    option socks_port '7890'
    option controller '127.0.0.1:9090'
    option secret '…'
    option active_route 'default'        # which route/profile is live
    option refresh_interval '60'         # minutes; graceful staging
    option watchdog '1'
    option stable_switch '1'             # interrupt_exist_connections = false

# ── Subscriptions ────────────────────────────────────────────────
config subscription 'psg_reality'
    option label 'PSG Reality'
    option url 'https://…/reality.json'
    option enabled '1'
    option max_nodes '120'
    # runtime (written back by engine, shown in UI):
    option last_update '…' ; option node_count '31'
    option traffic_used '…' ; option traffic_total '…' ; option expire '…'

# ── Manual nodes (typed, NEVER raw JSON in UI) ───────────────────
config node 'my_socks_exit'
    option label 'DE socks5 exit'
    option type 'socks'                  # vless|vmess|trojan|ss|hysteria2|tuic|wireguard|socks|http
    option server '1.2.3.4' ; option port '1080'
    option username '…' ; option password '…'

config node 'home_awg'
    option label 'AmneziaWG home'
    option type 'wireguard'
    option awg '1'                       # AmneziaWG obfuscation on
    # typed WG fields + AWG params stored structured, edited via form/preset
    …

# ── Groups (balancers / selectors) ───────────────────────────────
config group 'balanced_reality'
    option label 'Reality (balanced)'
    option kind 'urltest'                # urltest|select|fallback|roundrobin
    list member 'sub:psg_reality'        # a subscription…
    list member 'node:home_awg'          # …and/or nodes, and/or other groups
    option test_url 'http://gstatic.com/generate_204'
    option tolerance '80'                # anti-flap
    option interval '3m'

# ── Chains (proxy chaining via detour) ───────────────────────────
config chain 'reality_via_socks'
    option label 'Reality → socks5 exit'
    # ordered hops, first = entry (closest to client), last = exit
    list hop 'group:balanced_reality'
    list hop 'node:my_socks_exit'        # ← exit node

# ── Routes / rules (optional internal routing) ───────────────────
config route 'default'
    option label 'Default'
    option final 'chain:reality_via_socks'    # default outbound
config rule 'ir_direct'
    option route 'default'
    option match_geosite 'ir'
    option match_geoip 'ir'
    option outbound 'direct'
config rule 'ads_block'
    option route 'default'
    option match_geosite 'category-ads-all'
    option outbound 'block'
```

Everything the user builds in the UI serializes to this. The compiler resolves
`sub:`, `node:`, `group:`, `chain:` references into a valid sing-box config.

---

## 4. The compiler (`oxid-gen`, gen.py v2)

Turns the model into **one** RAM sing-box config, deterministically.

- **Nodes**: subscription nodes fetched + parsed + sanitized (as today) → namespaced
  outbounds. Manual nodes → typed→outbound emitters (one per protocol).
- **Groups**: `urltest`/`selector`/`fallback`/`loadbalance` over resolved members
  (flatten subs to their node tags; nest groups by tag reference).
- **Chains**: emit hops as outbounds where `hop[i].detour = hop[i-1]` so the **last
  hop is the exit**. Worked example in §7.
- **Routes**: `route.rules[]` from `rule` sections (geosite/geoip/domain/port/process)
  → outbound; `route.final` = the route's `final`. Rule-sets auto-managed.
- **Stability flags** injected everywhere (see §6).
- **Validation**: `sing-box-awg check` before anything is applied; invalid → keep
  running config, surface the error to the UI.

Output stays a single `/tmp/oxid/config.json`. Same graceful-staging contract as now.

---

## 5. Backend API (rpcd object `oxid`)

Replace ad-hoc `ctl.sh` string parsing with a typed ubus/rpcd surface the JS UI calls
directly (CLI wraps the same):

```
oxid.status()                       → running, active, socks/clash ports, uptime
oxid.nodes({group?})                → resolved node list + live latency (clash API)
oxid.test({tag})                    → on-demand delay probe
oxid.switch({target})               → live selector change (no restart)
oxid.refresh({sub?})                → graceful stage (no restart)
oxid.apply()                        → recompile + restart + re-assert active
oxid.sub_add/edit/del/update(...)   → subscription CRUD (+ fetch userinfo)
oxid.node_add/edit/del(...)         → typed node CRUD
oxid.group_add/edit/del(...)        → balancer CRUD
oxid.chain_add/edit/del(...)        → chain CRUD
oxid.rule_add/edit/del(...)         → routing rule CRUD
oxid.import({kind, payload})        → parse share-link | wg-quick | clash | sub-json
oxid.export({kind})                 → share-links / sing-box json (expert)
```

Backed by ucode (preferred on modern OpenWrt) or shell, reusing the parsers. ACL
entries scope read vs write. This is what makes the UI feel instant and typed.

---

## 6. Stability contract — "switching never drops you"

The headline reliability feature. Concretely:

1. **`interrupt_exist_connections: false`** on every `selector`/`urltest`/`loadbalance`
   group and the top selector. Old flows finish on the old node; only *new* flows use
   the new pick. (Gated by `stable_switch`, default on.)
2. **Anti-flap `tolerance`** (default 80 ms) so a balancer only re-picks when a node is
   meaningfully better — no constant churn.
3. **Selection persistence** via `cache_file` so a restart restores the last pick
   instead of snapping back to default mid-session.
4. **Graceful staging** (already built): hourly refresh regenerates the RAM config and
   validates it **without restarting** — fresh nodes wait for the next natural apply.
5. **Watchdog** (already built): keeps the preferred outbound, fails over to a healthy
   group only when the active path is actually dead, restores when it recovers — all
   via live selector switches (no restart).
6. **Apply = last resort.** Only explicit config changes recompile+restart; everything
   day-to-day is a live selector move. Restarts re-assert the active pick.

Result: balancer node changes, failovers, and refreshes are seamless to the user.

---

## 7. Worked example — "balance a subscription, exit through a socks5"

User flow in the UI (zero JSON):
1. **Nodes → Add → Paste share-link** the socks5 (or fill server/port/user/pass) →
   node `my_socks_exit`.
2. **Groups → New balancer** → pick subscription *PSG Reality*, kind *urltest* →
   group `balanced_reality`.
3. **Groups → New chain** → drag `balanced_reality` then `my_socks_exit` → chain
   `reality_via_socks` (UI shows: *entry → … → exit*).
4. **Routing → set Default final = `reality_via_socks`** (or just "Use for everything").

Compiler emits (abridged):
```json
{ "outbounds": [
  { "type":"selector","tag":"PROXY","outbounds":["chain:reality_via_socks","direct"],
    "default":"chain:reality_via_socks","interrupt_exist_connections":false },

  { "type":"socks","tag":"chain:reality_via_socks","server":"1.2.3.4","server_port":1080,
    "username":"…","password":"…","detour":"balanced_reality" },   // ← exit hop, dials THROUGH the balancer

  { "type":"urltest","tag":"balanced_reality","outbounds":["psg_reality│node1", …],
    "url":"…/generate_204","tolerance":80,"interrupt_exist_connections":false },

  { "type":"vless","tag":"psg_reality│node1", "detour": null, … }
]}
```
Traffic path: client → urltest picks best Reality node → connects to the socks5 server
**through** that node → socks5 → internet. socks5 is the exit; the entry is
load-balanced; switching Reality nodes doesn't drop the socks5 session.

---

## 8. UI / UX design

**Framework:** modern **LuCI client-side JS** (`L.view`, `ui.js`, `rpc.declare`,
custom `E()` views) — the same stack the polished apps use (homeproxy, nikki). Native
LuCI integration (auth, menu, theming) with a real design system on top. Not CBI.

**Design system (`oxid.css` + shared widgets):**
- Card-based layout, consistent spacing scale, rounded surfaces, subtle shadows.
- Light/dark aware (follows LuCI theme + explicit toggle).
- Status color language (healthy/degraded/down), latency bars, protocol chips,
  country flags from node names.
- Loading skeletons, empty states, toast notifications, confirm dialogs.
- Fully responsive (usable from a phone on the LAN).

**Views:**
1. **Dashboard** — core status, active route, quick-switch, live up/down speed &
   traffic (clash API), the current chain drawn, one-tap connect-test, link to zashboard.
2. **Nodes** — grouped list (by subscription / manual), latency bars, sort/filter,
   per-node test & copy, multi-select. **Add** = wizard: *Paste link · Scan QR · Manual
   form · Import file*. Per-protocol typed forms. No raw JSON on the happy path.
3. **Subscriptions** — cards showing title, node count, **traffic used/total + expiry**
   (parsed from `subscription-userinfo`), last update, update-now, auto-update toggle.
4. **Groups** — balancer builder: name, strategy (urltest/select/fallback/round-robin),
   member picker (subs/nodes/groups), test URL, tolerance. Live preview of current pick.
5. **Routing** — the route/chain builder: compose chains (drag hops, see entry→exit),
   and rule table (geosite/geoip/domain/port/process → outbound) with presets
   (Iran-direct, ads-block, streaming-via-X). "Use for everything" simple mode + advanced.
6. **Settings** — ports, secret, refresh interval, stability toggles, watchdog, backup/
   restore (export/import whole model), core update, logs, uninstall.
7. **Expert drawer** (hidden by default) — raw sing-box JSON view/import, per-node
   overrides, AWG magic-header editing.

**AmneziaWG, done right:** Add-WG wizard = *Paste `wg-quick`* (primary, uses
`awg2singbox`) **or** typed fields; obfuscation params live under an **Advanced**
accordion with **presets** (e.g. "Amnezia default", "manual") and MTU default 1280.
No magic-header hex in your face.

---

## 9. Parsers (guided import engine)

One import pipeline, many formats → typed nodes/subs:
- **Share-links**: `vless:// vmess:// trojan:// ss:// ssr:// hysteria2://|hy2://
  tuic:// wireguard://|wg://` (+ `#name`, query params). Already partly in gen.py;
  promote to a shared, tested module.
- **wg-quick / AmneziaWG**: `awg2singbox.py` (built) → typed WG node.
- **Clash / Clash.Meta YAML** and **sing-box JSON** subscriptions.
- **base64 blobs** and raw link lists.
- **QR** (client-side decode in the browser, then same pipeline).

Round-trip **export** (node → share-link, model → sing-box JSON) for portability.

---

## 10. Rebrand: `singbox-sub` → `oxid`

Maturity = one name. Migrate:
- Paths `/etc/singbox-sub` → `/etc/oxid`, `/tmp/singbox-sub` → `/tmp/oxid`.
- UCI `singbox-sub` → `oxid` (with a one-shot migration in `uci-defaults`).
- Service `singbox-sub` → `oxid`; CLI `ctl.sh` → `oxid`.
- Binary stays `sing-box-awg` (engine), wrapped by the `oxid` product.
- LuCI `luci-app-singbox-sub` → `luci-app-oxid`.
- Idempotent migration script; keep back-compat symlinks for one release.

---

## 11. Packaging & distribution

Turn the tarball into real OpenWrt packages so install/update/remove is clean:
- `oxid` — core: `oxid-gen`, `oxid` CLI, rpcd backend, init, cron, uci-defaults, parsers.
- `luci-app-oxid` — the JS UI + menu + ACL (depends on `oxid`).
- `sing-box-awg` — the AWG-capable core (built by `build/build-awg.sh`), shipped as a
  package or a documented reproducible build + verified checksum.
- A **package feed** (`src-git`) users can add, plus a one-line installer that also
  drops the reproducible-build recipe. `apk`/`opkg` aware (OpenWrt 24→25 split).
- CI (GitHub Actions) cross-compiling the core for common targets (armv7/arm64/mips/x86)
  with pinned Go toolchain + checksums.

---

## 12. Reliability & testing

- **Config fuzz**: feed messy real subscriptions; assert `sing-box check` passes and
  node counts are sane.
- **Golden configs**: model → expected JSON snapshot tests for groups/chains/rules.
- **Switch-stability test**: hold a long download, flip the balancer pick, assert the
  transfer survives (`interrupt_exist_connections` working).
- **Boot test**: cold boot with network down → falls back to `config.last`/lastgood →
  stages fresh when network returns.
- **Parser corpus**: a fixture set of share-links/clash/sing-box/wg for each protocol.
- **Upgrade test**: `singbox-sub` → `oxid` migration on a populated box.

---

## 13. Delivery plan (phased, each phase shippable)

**Phase 1 — Foundation & rebrand (engine-compatible).**
Rename to `oxid`; introduce typed UCI model (subscription+node+group) alongside a
compiler refactor (`oxid-gen`) that still produces today's behavior; migration script;
stability flags (`interrupt_exist_connections:false`, tolerance, cache persistence).
*Ships: same features, new foundation, seamless switching.*

**Phase 2 — Backend API + typed node/sub management.**
rpcd `oxid` object; typed node CRUD; share-link + wg parsers as a shared module;
subscription userinfo parsing. *Ships: a real API and typed nodes (still on CBI or a
thin JS shell).*

**Phase 3 — Polished LuCI JS app (Dashboard, Nodes, Subscriptions).**
Design system; the three core views; latency bars; add-node wizard; AWG wizard with
presets. *Ships: the "Hiddify feel" for the common flows.*

**Phase 4 — Groups & the route/chain builder.**
Balancer builder; chain composer (the socks5-exit example); routing rules with presets.
*Ships: the compose-your-own-topology superpower.*

**Phase 5 — Packaging, CI, feed, docs.**
Real packages, cross-compiled core, feed + installer, screenshots, README, this doc
folded into user docs. *Ships: installable product others can adopt.*

**Phase 6 — Polish pass & extras.**
QR import/export, backup/restore, per-app/rule presets, i18n (EN/FA), theming, empty/
error states, accessibility, telemetry-free health page.

---

## 14. Open decisions (need your call)

1. **UI stack** — recommend **LuCI JS** (native, polished, integrated). Alternative:
   a bespoke SPA served locally (max polish, but heavier + auth/rebuild work). 
2. **Rebrand now vs later** — recommend **Phase 1** (cheapest before the UI hardcodes
   names).
3. **Routing scope** — oxid owns outbound composition (groups/chains) for sure; how much
   *domain routing* should it do vs leaving that to passwall2? Recommend: full internal
   routing available but "Use for everything" default, so it complements passwall2.
4. **Core distribution** — ship a prebuilt `sing-box-awg` per target, or reproducible
   build only? Recommend: **both** — CI prebuilts + verifiable recipe.

---

*Engine today (keep): RAM config, graceful staging, offline lastgood, boot fallback,
watchdog, AmneziaWG fork, zashboard. Everything above builds on it.*
