# oxid-ui

A Hiddify-style **sing-box subscription manager for OpenWrt**, with a dedicated LuCI GUI.

Point it at one or more subscriptions (sing-box JSON *or* plain `vless://`/`vmess://`/`trojan://`/`ss://`/`hysteria2://`/`tuic://` share-links), and it fetches them into RAM, load-balances each by latency, and exposes a single local **SOCKS** endpoint you can feed into passwall2 (or any transparent proxy). Switch between subscriptions and static nodes live — no reconnect — from a clean web UI, or from the bundled [zashboard](https://github.com/Zephyruso/zashboard) dashboard. First-class **AmneziaWG** support included.

Built to stay light and survive: subscription node lists never touch flash (they live in `tmpfs`), so refreshing a 6,000-node subscription doesn't churn your router's storage or crawl the way a UCI-backed manager does.

> The LuCI app registers under **Services → Sing-Box Subs**.

## Why

Managers that store every subscription node as a UCI section get slow and write-heavy as the node count grows — every refresh rewrites flash and every UI action walks the whole list. oxid-ui keeps only a handful of small settings on disk (the sub URLs, ports, secret) and generates the actual runtime config in RAM. You get subscription load-balancing, live switching, and a GUI, while the flash stays quiet and the core stays fast.

## Features

- **Subscriptions in RAM.** Fetched, converted, and assembled into one sing-box config under `/tmp` (tmpfs). Only tiny settings live on flash (UCI).
- **Any subscription format.** sing-box JSON, base64 link-lists, or plaintext share-links — `vless / vmess / trojan / shadowsocks / hysteria2 (hy2) / tuic`, including reality, uTLS, ws/grpc/http transports.
- **Live switching.** Each subscription is a latency-tested `urltest` group under one selector. Switch subscription or node instantly via the GUI or clash API — the core keeps running.
- **Graceful hourly refresh.** Re-pulls subscriptions into RAM every hour **without restarting the core** — no dropped connections. Fresh nodes stage in and load on the next switch/apply/reboot.
- **Auto-failover watchdog.** Keeps your chosen main config preferred; if it dies, fails over to a healthy subscription, and switches back when it recovers.
- **Offline-resilient.** Each subscription's last-good copy is cached on disk; if the source is unreachable, it falls back to it. A `config.last` safety net lets the core boot even if generation ever fails.
- **AmneziaWG.** Add an AWG 2.0 endpoint by pasting a `wg-quick` config in the GUI — it's converted to a sing-box endpoint automatically. Requires the AWG-capable sing-box fork (see [Building the AmneziaWG core](#building-the-amneziawg-core)).
- **Static nodes.** Add any raw sing-box outbound/endpoint JSON as an always-available node.
- **Beautiful dashboard.** Serves [zashboard](https://github.com/Zephyruso/zashboard) locally via sing-box's clash API — per-node latency, traffic, group switching.
- **Dedicated LuCI GUI.** Manage subscriptions, switch configs, add AmneziaWG / static nodes, and see live status — all from the web UI.

## Requirements

- OpenWrt **23.05+** (developed on 25.12, `apk`-based; works with `opkg` too)
- `python3`, `curl` (with TLS), `luci-base`, `luci-compat`, `luci-lua-runtime`
- A `sing-box` binary with the `clash_api` build tag (the OpenWrt package has it). For AmneziaWG, an AWG-capable fork — see below.
- Optional: `passwall2` (or any tool that can dial a local SOCKS)

## Install

```sh
# on the router
git clone https://github.com/<you>/oxid-ui
cd oxid-ui
sh install.sh
```

`install.sh` copies the files into place, installs missing dependencies, generates a random dashboard secret, downloads the zashboard UI, wires up the hourly-refresh and watchdog cron jobs, and starts the service. Then:

1. Open **`http://<router-ip>:9090/ui/`** and paste the secret it prints (or find it in **Services → Sing-Box Subs → Settings**).
2. Add your subscriptions under **Settings & Subscriptions**.
3. Point passwall2's node (and UDP node) at a SOCKS node → `127.0.0.1:7890`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit together.

## Usage

Everything is in **Services → Sing-Box Subs**:

- **Control** — live status, one-tap graceful switch, subscription pools, static-node manager, "Add AmneziaWG" (paste wg-quick) and "Add static node" (raw JSON), plus Refresh / Apply / Restart and a link to the dashboard.
- **Settings & Subscriptions** — core settings (ports, secret, node cap, health-check, active config, hourly refresh, watchdog) and add/remove/toggle subscriptions.

From the shell, `ctl.sh` is the control surface:

```sh
/etc/singbox-sub/ctl.sh status              # JSON status
/etc/singbox-sub/ctl.sh switch <tag>        # graceful switch (sub name / static tag / direct)
/etc/singbox-sub/ctl.sh stage!              # re-fetch subs into RAM, no restart
/etc/singbox-sub/ctl.sh apply               # regenerate + restart core
/etc/singbox-sub/ctl.sh awg-import <name>   # wg-quick on stdin -> AmneziaWG node
/etc/singbox-sub/ctl.sh static-add <name>   # raw sing-box JSON on stdin -> static node
```

## Building the AmneziaWG core

Upstream sing-box has no AmneziaWG; it lives in forks. `build/build-awg.sh` cross-compiles an AWG-capable binary (from [Leadaxe/sing-box-lx](https://github.com/Leadaxe/sing-box-lx)) with the `with_awg` + `with_clash_api` tags for your router's architecture. Run it on a machine with Go, then copy the result to `/usr/bin/sing-box-awg` on the router. Without it, everything works except AmneziaWG — just point the core at the stock `sing-box` (see install notes).

## How it's laid out

```
files/etc/singbox-sub/       gen.py (generator+parser), ctl.sh, awg2singbox.py, static/
files/etc/init.d/singbox-sub procd service (RAM config, boot safety net)
files/etc/config/singbox-sub UCI: settings + subscription list
files/usr/lib/lua/luci/...    LuCI app (controller, CBI settings, control view)
build/build-awg.sh            build the AmneziaWG sing-box fork
```

## Credits

- [sing-box](https://github.com/SagerNet/sing-box) — the proxy core
- [Leadaxe/sing-box-lx](https://github.com/Leadaxe/sing-box-lx) — AmneziaWG-capable fork
- [zashboard](https://github.com/Zephyruso/zashboard) — the dashboard UI
- [AmneziaWG](https://github.com/amnezia-vpn) — the obfuscated WireGuard

## License

To be added by the repository owner.
