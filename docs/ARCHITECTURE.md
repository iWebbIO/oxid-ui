# Architecture

## Data flow

```
subscriptions (GitHub/URLs)                 static nodes (disk)
        │  fetch (curl)                             │  /etc/oxid/static/*.json
        ▼                                           ▼
   gen.py  ── parse (sing-box JSON | base64 | share-links) ──► ONE config.json in RAM (/tmp)
        │            + namespaced urltest groups + PROXY selector + endpoints
        ▼
   sing-box-awg  ──► SOCKS 127.0.0.1:7890   (passwall2 node + udp_node point here)
        │        ──► clash API 0.0.0.0:9090  (zashboard dashboard + live switching)
        ▼
   selected outbound → subscription node / AmneziaWG endpoint / direct
```

## Where things live

| Path | On | What |
|---|---|---|
| `/etc/config/oxid` | flash (UCI) | settings + subscription list (small, static) |
| `/etc/oxid/gen.py` | flash | generator + subscription parser |
| `/etc/oxid/oxid` | flash | control surface (stage/apply/switch/status/watchdog/…) |
| `/etc/oxid/awg2singbox.py` | flash | wg-quick → sing-box endpoint converter |
| `/etc/oxid/static/*.json` | flash | static nodes (AmneziaWG, etc.) |
| `/etc/oxid/lastgood/*.json` | flash | last-good per-subscription cache (offline fallback) |
| `/etc/oxid/config.last` | flash | last valid full config (boot safety net) |
| `/etc/oxid/ui/` | flash | zashboard static files |
| `/tmp/oxid/config.json` | **RAM** | the live runtime config (regenerated, never on flash) |
| `/tmp/oxid/cache.db` | **RAM** | sing-box clash cache |

Node lists — the part that grows and churns — only ever exist in RAM. Flash holds a few KB of settings, so refreshing subscriptions doesn't wear storage.

## Config generation

`gen.py` reads settings + enabled subscriptions from UCI, then for each subscription:

1. Fetches the URL (short timeout; on failure uses the on-disk last-good copy).
2. Parses it — tries sing-box JSON first, else base64-blob, else a plaintext list of
   `vless:// vmess:// trojan:// ss:// hysteria2://|hy2:// tuic://` share-links.
3. Sanitizes nodes (drops bad `network`, forces uTLS on reality, normalizes hy2 port ranges) and caps to `max_nodes`.
4. Namespaces tags (`<sub>│<tag>`) and wraps them in a latency-tested `urltest` group `sub:<name>`.

Static nodes are loaded from `static/*.json` and routed by type: `wireguard`/`tailscale` → `endpoints[]`, everything else → `outbounds[]`. All of it hangs off one `selector` tagged `PROXY`, whose default is the `active` setting.

## Lifecycle

- **stage** (`ctl.sh stage` / hourly cron) — regenerate `config.json` in RAM and validate; **no restart**, so nothing disconnects. Fresh nodes are ready for the next switch/apply/reboot.
- **switch** (GUI / clash API) — change the `PROXY` selector live; instant, no restart. Persists to UCI `active`.
- **apply** (GUI save / `ctl.sh apply`) — regenerate, validate, restart the core, re-assert `active`.
- **watchdog** (cron) — probe the active outbound via the clash API; keep the main preferred, fail over to a healthy subscription if it dies, restore when it recovers.
- **boot** — the init script regenerates in RAM; if generation fails, it falls back to `config.last`; 90s later it stages fresh subscriptions once the network is up.

## AmneziaWG

Upstream sing-box has no AmneziaWG, so the core is an AWG-capable fork (`sing-box-awg`, built by `build/build-awg.sh`). An AWG node is a sing-box `wireguard` endpoint with the obfuscation params (`jc/jmin/jmax`, `s1..s4`, `h1..h4`, `i1..i5`) promoted to the endpoint root. `awg2singbox.py` converts a standard `wg-quick` config into that shape. MTU defaults to 1280 to avoid tunnel MTU blackholes (large TCP/UDP stalling while small packets pass).
