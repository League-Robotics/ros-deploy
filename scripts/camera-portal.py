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
            hosts.append({"name": name, "address": addr})
    return hosts


def port_open(address: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    """TCP-connect test, so we can distinguish 'host down' from 'no cameras'."""
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_topics(address: str) -> list[str]:
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

    topics: set[str] = set()
    for match in re.findall(r"/[A-Za-z0-9_/]*image[A-Za-z0-9_/]*", body):
        topic = re.sub(r"/(compressed|theora)$", "", match)
        topics.add(topic)
    return sorted(topics)


def snapshot_ok(address: str, topic: str) -> tuple[bool, int]:
    """Read the first frame off the MJPEG stream — the real liveness test.

    Deliberately uses /stream rather than /snapshot. web_video_server leaks a
    jpeg_snapshot_streamer per /snapshot request and stops answering them after
    a handful (observed on skadi: two fast replies, then every request hangs
    until the service is restarted), while /stream keeps working the whole time.
    Probing with /snapshot would therefore report healthy cameras as dead AND
    make the leak worse on every rescan.

    We read a bounded prefix and close, rather than draining an endless stream.
    """
    url = f"http://{address}:{VIDEO_PORT}/stream?topic={topic}"
    try:
        with urllib.request.urlopen(url, timeout=SNAPSHOT_TIMEOUT) as resp:
            # Enough to cover the multipart header plus a chunk of real JPEG.
            data = resp.read(65536)
        # A topic with no publisher still opens fine but yields nothing, so
        # require actual payload rather than just a good status code.
        return (len(data) > 1024, len(data))
    except (urllib.error.URLError, OSError, socket.timeout):
        return (False, 0)


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

    topics = list_topics(address)
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
async function scan() {
  document.getElementById('count').textContent = 'scanning…';
  const r = await fetch('/api/cameras');
  const d = await r.json();

  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  document.getElementById('count').textContent =
    d.camera_count + ' camera' + (d.camera_count === 1 ? '' : 's') + ' live';

  for (const h of d.hosts) {
    for (const c of h.cameras) {
      const el = document.createElement('div');
      el.className = 'cam';
      // Cache-bust so a rescan restarts the MJPEG stream instead of reusing a
      // connection whose publisher may have gone away.
      const src = c.stream + '&_=' + Date.now();
      el.innerHTML =
        '<h2>' + h.name + '<span>' + c.topic + '</span></h2>' +
        '<img src="' + src + '" alt="' + h.name + ' ' + c.topic + '">' +
        '<footer><span>' + h.address + '</span>' +
        '<a href="' + c.direct + '" target="_blank">open direct ↗</a></footer>';
      grid.appendChild(el);
    }
  }
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
