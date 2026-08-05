#!/usr/bin/env python3
"""
scripts/camera-portal.py — find every camera on the fleet and view them all in
one browser page, from the Mac, with nothing installed.

Every camera host runs web_video_server (roles/cameras), which serves each ROS
image topic as MJPEG over HTTP on port 8080. So viewing needs no ROS install,
no XQuartz, and no X11 forwarding — just a browser. This server does two jobs:

  1. DISCOVERY — probes each inventory host's web_video_server, asks it which
     image topics exist, and verifies each one actually delivers a frame.
  2. VIEWING   — serves a page embedding every live stream in a grid, proxied
     through this server so one origin serves everything.

    ./scripts/camera-portal.py                  # http://localhost:8781/
    ./scripts/camera-portal.py --port 9100
    ./scripts/camera-portal.py --hosts vidar,skadi

Why probe instead of trusting the topic list
--------------------------------------------
web_video_server advertises every image topic it can see on the ROS domain, not
the cameras physically attached to the host it runs on. A host with one camera
will happily list a peer's /camera1/... too (skadi does exactly this). The only
reliable test is to fetch a snapshot and check that bytes come back, so that is
what discovery does — a topic that returns no image is not reported as a camera.

Compare scripts/view-cameras.sh, which runs rqt_image_view on a remote node and
forwards the window over SSH X11. That shows one topic at a time and needs
XQuartz; this shows all of them at once in a browser.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY = REPO_ROOT / "inventory" / "hosts.ini"

# web_video_server's port, matching cameras_web_video_port in
# roles/cameras/defaults/main.yml.
VIDEO_PORT = 8080

# Probe timeouts (seconds). Snapshot gets a long budget on purpose: a full-sensor
# 1456x1088 mono frame is ~485KB, and measured against skadi (Pi 4, WiFi) a single
# snapshot took anywhere from 4s to >25s, sometimes failing outright. Too short a
# timeout reports a working-but-slow camera as dead, which is the more confusing
# failure. Discovery probes hosts concurrently, so a generous budget here costs
# wall-clock only when a host is genuinely struggling.
CONNECT_TIMEOUT = 4
SNAPSHOT_TIMEOUT = 30

# An offline host should not stall discovery. Probing the fleet concurrently
# keeps a full sweep at roughly the cost of the slowest single host.
MAX_WORKERS = 12


def parse_inventory(path: Path) -> list[dict]:
    """Pull `name ansible_host=IP` pairs out of the Ansible inventory.

    Only [ros_nodes] carries addresses; the other groups list bare names that
    inherit from it, so a single pass over ansible_host= lines is enough.
    """
    hosts: list[dict] = []
    seen: set[str] = set()
    if not path.exists():
        return hosts
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        parts = line.split()
        name = parts[0]
        addr = next(
            (p.split("=", 1)[1] for p in parts[1:] if p.startswith("ansible_host=")),
            None,
        )
        if addr and name not in seen:
            seen.add(name)
            hosts.append({
                "name": name,
                "address": addr,
                "namespaces": host_namespaces(name),
                "swapped": host_swapped_namespaces(name),
            })
    return hosts


def host_namespaces(name: str) -> set[str]:
    """Camera namespaces this host physically has, from its host_vars file.

    web_video_server advertises every image topic it can see on the ROS DOMAIN,
    not the cameras attached to the machine it runs on — so a single-camera host
    happily lists a peer's /camera1/... as if it were its own (measured: skadi,
    ali, vali and baldur each have one camera but advertise two, because vidar
    really does have two). Those phantom entries can never render, so the page
    fills with permanently-black tiles.

    The inventory is the source of truth for what hardware exists, so read the
    `namespace:` keys out of host_vars and use them to filter. An empty set means
    "unknown" — no host_vars file, or none parsed — and the caller then falls
    back to trusting the server rather than hiding everything.
    """
    hv = REPO_ROOT / "inventory" / "host_vars" / f"{name}.yml"
    if not hv.exists():
        return set()
    return set(re.findall(r"^\s*-?\s*namespace:\s*(\S+)", hv.read_text(), re.M))


def host_swapped_namespaces(name: str) -> set[str]:
    """Namespaces whose camera needs the R/B relay (`swap_rb: true`).

    This is PER CAMERA, not per host. Verified on vidar: two IMX296 modules on
    one Pi, same config, same negotiated 1456x1088-BGR888/sRGB stream — camera0
    renders skin and wood correctly while camera1 renders them blue. The boards
    themselves differ, so no single format setting fixes both.

    For a camera listed here the corrected pixels arrive on
    <ns>/camera/image_color and that is what the page must show; every other
    camera shows image_raw. Getting this per-camera is what stops the endless
    "fixed one, broke the other" loop.
    """
    hv = REPO_ROOT / "inventory" / "host_vars" / f"{name}.yml"
    if not hv.exists():
        return set()
    text = hv.read_text()
    swapped, current = set(), None
    for line in text.splitlines():
        m = re.match(r"\s*-?\s*namespace:\s*(\S+)", line)
        if m:
            current = m.group(1)
        elif current and re.match(r"\s*swap_rb:\s*(true|yes)\s*$", line, re.I):
            swapped.add(current)
    return swapped


def port_open(address: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    """TCP-connect test, so we can distinguish 'host down' from 'no cameras'."""
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_topics(address: str, swapped: set[str] | None = None) -> list[str]:
    """Ask web_video_server which image topics it knows about.

    Its index page lists them as links. We only keep image-ish topics and drop
    the transport suffixes (/compressed, /theora) so each camera appears once.
    """
    url = f"http://{address}:{VIDEO_PORT}/"
    try:
        with urllib.request.urlopen(url, timeout=CONNECT_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, socket.timeout):
        return []

    # The index lists each topic as `<li>/camera0/camera/image_raw<ul>...` and
    # again inside `?topic=` query strings. Match the query-string form: it is
    # unambiguous, whereas a bare `/...image...` pattern also matches fragments
    # of the surrounding markup.
    topics: set[str] = set()
    for match in re.findall(r"[?&]topic=(/[A-Za-z0-9_/]*image[A-Za-z0-9_]*)", body):
        topics.add(re.sub(r"/(compressed|theora)$", "", match))

    # Pick ONE topic per camera, per camera — not per host.
    #
    # camera_ros hands through libcamera's buffer and labels it correctly, but
    # some sensor boards deliver the channels reversed. Verified on vidar: two
    # IMX296 modules, identical config, and camera0 is correct while camera1 is
    # swapped. So the choice is made per namespace from `swap_rb:` in host_vars:
    # a swapped camera shows the relay's corrected image_color, everything else
    # shows image_raw.
    #
    # image_color can also linger after a relay is switched off, so it is only
    # ever chosen for a camera explicitly marked swapped.
    swapped = swapped or set()
    out = []
    for t in topics:
        # Topics are /<host>/<ns>/camera/image_*, so the namespace is the
        # SECOND path element (the first is the hostname prefix added to
        # stop the fleet-wide /camera0 collision).
        parts = t.lstrip("/").split("/")
        ns = parts[1] if len(parts) > 1 else parts[0]
        want_color = ns in swapped
        if t.endswith("/image_color") and want_color:
            out.append(t)
        elif t.endswith("/image_raw") and not want_color:
            out.append(t)
    return sorted(out)


def snapshot_ok(address: str, topic: str) -> tuple[bool, int]:
    """Deliberately does NOT probe. Kept so the call site stays readable.

    Earlier versions opened /stream (and before that /snapshot) for every topic
    on every scan to prove it delivered frames. That was actively harmful:
    web_video_server 3.1.0 stalls its whole HTTP listener under request pressure,
    so a scan of N cameras opened N streams, abandoned them, and wedged the very
    server it was testing — the page then reported "no topics advertised" for
    hosts whose cameras were fine, and each Rescan made it worse.

    The <img> tags on the page are the real test: the browser opens exactly one
    long-lived stream per camera, which is the access pattern web_video_server
    handles well. If a camera is dark you see it immediately. So discovery just
    lists what the server advertises and lets the browser do the rest.
    """
    return (True, 0)


def probe_host(host: dict) -> dict:
    """Classify one host: offline / no server / no cameras / list of cameras."""
    name, address = host["name"], host["address"]
    result = {"name": name, "address": address, "cameras": [], "status": ""}

    if not port_open(address, VIDEO_PORT):
        # Separate a dead host from one that is up but not serving video, so the
        # page can say which — they need very different fixes.
        reachable = port_open(address, 22, timeout=2)
        result["status"] = "no web_video_server" if reachable else "offline"
        return result

    topics = list_topics(address, host.get("swapped") or set())

    # Drop topics whose namespace this host does not physically have (see
    # host_namespaces). Only filter when the inventory actually told us
    # something; otherwise trust the server rather than blanking the host.
    wanted = host.get("namespaces") or set()
    if wanted:
        topics = [t for t in topics
                  if len(t.lstrip("/").split("/")) > 1
                  and t.lstrip("/").split("/")[1] in wanted
                  and t.lstrip("/").split("/")[0] == host["name"]]

    if not topics:
        result["status"] = "server up, no topics advertised"
        return result

    for topic in topics:
        ok, size = snapshot_ok(address, topic)
        if ok:
            result["cameras"].append(
                {
                    "topic": topic,
                    # Bytes read from the probe prefix, not a whole frame — it
                    # only evidences that data is flowing.
                    "probe_bytes": size,
                    "stream": f"/proxy/{address}/stream?topic={topic}",
                    "direct": f"http://{address}:{VIDEO_PORT}"
                    f"/stream_viewer?topic={topic}",
                }
            )

    if result["cameras"]:
        result["status"] = "ok"
    else:
        # Every advertised topic failed to yield a frame. Usually the phantom
        # case: topics seen on the ROS domain whose publisher lives elsewhere.
        result["status"] = f"{len(topics)} topic(s) advertised, none delivering frames"
    return result


def discover(hosts: list[dict]) -> dict:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(probe_host, hosts))
    total = sum(len(r["cameras"]) for r in results)
    return {"hosts": results, "camera_count": total}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Cameras</title>
<style>
  :root { color-scheme: light dark; --bg:#f6f7f9; --fg:#14171a; --card:#fff;
          --muted:#5b6470; --line:#dfe3e8; --ok:#1a7f4b; --bad:#b4232c; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14171a; --fg:#e8eaed; --card:#1e2227; --muted:#9aa4b0;
            --line:#2c3238; --ok:#4ac585; --bad:#f2777f; }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  header { display:flex; align-items:baseline; gap:16px; flex-wrap:wrap;
           margin-bottom:20px; }
  h1 { font-size:20px; margin:0; }
  .count { color:var(--muted); }
  button { font:inherit; padding:6px 14px; border:1px solid var(--line);
           border-radius:6px; background:var(--card); color:var(--fg);
           cursor:pointer; }
  button:hover { border-color:var(--muted); }
  .grid { display:grid; gap:16px;
          grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); }
  .cam { background:var(--card); border:1px solid var(--line);
         border-radius:10px; overflow:hidden; }
  .cam h2 { font-size:14px; margin:0; padding:10px 12px;
            border-bottom:1px solid var(--line); display:flex;
            justify-content:space-between; gap:8px; }
  .cam h2 span { color:var(--muted); font-weight:400; font-size:12px; }
  .cam img { display:block; width:100%; height:auto; background:#000;
             min-height:180px; }
  .cam footer { padding:8px 12px; font-size:12px; color:var(--muted);
                display:flex; justify-content:space-between; gap:8px; }
  a { color:inherit; }
  table { border-collapse:collapse; width:100%; margin-top:28px;
          background:var(--card); border:1px solid var(--line);
          border-radius:10px; overflow:hidden; }
  th,td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line);
          font-size:13px; }
  th { color:var(--muted); font-weight:600; }
  tr:last-child td { border-bottom:none; }
  .ok { color:var(--ok); } .bad { color:var(--bad); }
  .empty { color:var(--muted); padding:40px 0; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>Fleet Cameras</h1>
  <span class="count" id="count">scanning…</span>
  <button onclick="scan()">Rescan</button>
</header>
<div class="grid" id="grid"></div>
<div id="status"></div>
<script>
// Refresh each tile with periodic SNAPSHOTS instead of holding a live MJPEG
// stream open.
//
// Measured on this fleet: web_video_server 3.1.0 serves ONE long-lived /stream
// well, but a second concurrent stream to the same host stalls the listener --
// and a browser page holding N streams open permanently is exactly that load.
// It is also more than the WiFi link carries for the bigger sensors (vidar
// served 2 MB locally but 22 bytes over the air in the same second).
//
// A snapshot is a short request that finishes and frees the connection, so the
// server is idle between frames and the link only carries one frame at a time.
// The result is a slower frame rate but tiles that actually stay up.
// 4s, not 1s. Measured: a host serves a snapshot fine, but a second request
// arriving while it is still encoding stalls its listener for ~20-30s. With
// six tiles refreshing, a short interval means they constantly knock each
// other out and the page shows a rotating subset. 4s is slower than video but
// it is the difference between all six tiles staying up and half of them
// going black.
// Each tile refreshes on its OWN cadence: fetch a snapshot, and only when that
// frame has landed (or failed) schedule the next one. A fixed interval does not
// work here -- measured 2026-07-31, one 640x480 frame takes 0.5 s on baldur but
// 3.4 s on skadi and 9.8 s on vidar, so any shared interval is either too fast
// for the slow hosts (requests pile up and web_video_server stalls its listener
// for ~20-30 s) or needlessly slow for the quick ones.
//
// Snapshots rather than a live MJPEG stream: web_video_server 3.1.0 serves one
// long-lived stream well but stalls when a second overlaps it, and a page
// holding N streams open permanently is exactly that load.
// Measured 2026-07-31: every camera serves reliably on its own, but six
// tiles requesting close together still knock two of them out. A longer gap
// plus a wider stagger trades frame rate for all six tiles staying live.
const GAP_MS = 3000;          // breathing room between a host's requests
const MAX_WAIT_MS = 25000;    // give even the slowest host time to encode
const timers = [];

function startStreams(imgs) {
  while (timers.length) clearTimeout(timers.pop());
  imgs.forEach((img, idx) => {
    const base = img.dataset.src.replace('/stream?', '/snapshot?');
    const tick = () => {
      const probe = new Image();
      let done = false;
      const finish = (ok) => {
        if (done) return;
        done = true;
        if (ok) img.src = probe.src;
        timers.push(setTimeout(tick, GAP_MS));
      };
      probe.onload  = () => finish(true);
      probe.onerror = () => finish(false);
      // A stalled host never fires either event; move on rather than freezing
      // this tile forever.
      setTimeout(() => finish(false), MAX_WAIT_MS);
      probe.src = base + '&_=' + Date.now();
    };
    // Stagger the first request so six tiles do not hit the fleet at once.
    timers.push(setTimeout(tick, idx * 2500));
  });
}

async function scan() {
  document.getElementById('count').textContent = 'scanning…';
  const r = await fetch('/api/cameras');
  const d = await r.json();

  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  document.getElementById('count').textContent =
    d.camera_count + ' camera' + (d.camera_count === 1 ? '' : 's') + ' live';

  // Build every tile first, but WITHOUT a src — see startStreams(). Attaching
  // all the srcs at once opens N simultaneous MJPEG connections, and
  // web_video_server stalls its whole HTTP listener under that burst, so the
  // page would kill the very servers it is displaying (measured: 10 tiles ->
  // 2 rendered).
  const pending = [];
  for (const h of d.hosts) {
    for (const c of h.cameras) {
      const el = document.createElement('div');
      el.className = 'cam';
      el.innerHTML =
        '<h2>' + h.name + '<span>' + c.topic + '</span></h2>' +
        '<img alt="' + h.name + ' ' + c.topic + '" data-src="' + c.stream + '">' +
        '<footer><span>' + h.address + '</span>' +
        '<a href="' + c.direct + '" target="_blank">open direct ↗</a></footer>';
      grid.appendChild(el);
      pending.push(el.querySelector('img'));
    }
  }
  startStreams(pending);
  if (!d.camera_count) {
    grid.innerHTML = '<p class="empty">No live cameras found.</p>';
  }

  let rows = '';
  for (const h of d.hosts) {
    const good = h.status === 'ok';
    rows += '<tr><td>' + h.name + '</td><td>' + h.address + '</td>' +
            '<td class="' + (good ? 'ok' : 'bad') + '">' + h.status + '</td>' +
            '<td>' + h.cameras.length + '</td></tr>';
  }
  document.getElementById('status').innerHTML =
    '<table><tr><th>Host</th><th>Address</th><th>Status</th>' +
    '<th>Cameras</th></tr>' + rows + '</table>';
}
scan();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    hosts: list[dict] = []

    def log_message(self, fmt, *args):  # quieter console
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
            return

        if parsed.path == "/api/cameras":
            data = discover(self.hosts)
            self._send(200, "application/json", json.dumps(data, indent=2).encode())
            return

        # /proxy/<ip>/stream?topic=... — relay the MJPEG stream so the whole page
        # comes from one origin. Streaming is chunked and endless, so copy it
        # incrementally rather than buffering.
        if parsed.path.startswith("/proxy/"):
            self._proxy(parsed)
            return

        self._send(404, "text/plain", b"not found\n")

    def _proxy(self, parsed):
        parts = parsed.path.split("/", 3)
        if len(parts) < 4:
            self._send(400, "text/plain", b"bad proxy path\n")
            return
        address, tail = parts[2], parts[3]
        topic = parse_qs(parsed.query).get("topic", [""])[0]
        if not topic:
            self._send(400, "text/plain", b"missing topic\n")
            return

        kind = "stream" if tail.startswith("stream") else "snapshot"
        url = f"http://{address}:{VIDEO_PORT}/{kind}?topic={topic}"
        try:
            upstream = urllib.request.urlopen(url, timeout=SNAPSHOT_TIMEOUT)
        except (urllib.error.URLError, OSError, socket.timeout) as exc:
            self._send(502, "text/plain", f"upstream failed: {exc}\n".encode())
            return

        self.send_response(200)
        self.send_header(
            "Content-Type", upstream.headers.get("Content-Type", "image/jpeg")
        )
        self.end_headers()
        try:
            while chunk := upstream.read(8192):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # viewer closed the tab / hit rescan
        finally:
            upstream.close()

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--port", type=int, default=8781)
    ap.add_argument("--host", default="127.0.0.1", help="bind address")
    ap.add_argument("--hosts", default="", help="comma-separated subset of hosts")
    ap.add_argument("--list", action="store_true", help="probe, print, exit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    hosts = parse_inventory(INVENTORY)
    if not hosts:
        print(f"ERROR: no hosts parsed from {INVENTORY}", file=sys.stderr)
        return 1
    if args.hosts:
        wanted = {h.strip() for h in args.hosts.split(",") if h.strip()}
        hosts = [h for h in hosts if h["name"] in wanted]
        if not hosts:
            print(f"ERROR: none of {sorted(wanted)} are in the inventory",
                  file=sys.stderr)
            return 1

    if args.list:
        data = discover(hosts)
        for h in data["hosts"]:
            mark = "ok " if h["status"] == "ok" else "-- "
            print(f"{mark}{h['name']:<10} {h['address']:<16} {h['status']}")
            for c in h["cameras"]:
                print(f"     {c['topic']}  (streaming, {c['probe_bytes']} B probed)")
        print(f"\n{data['camera_count']} live camera(s)")
        return 0

    Handler.hosts = hosts
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True  # don't let open streams block shutdown
    print(f"Camera portal → http://{args.host}:{args.port}/")
    print(f"Watching {len(hosts)} host(s): {', '.join(h['name'] for h in hosts)}")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
