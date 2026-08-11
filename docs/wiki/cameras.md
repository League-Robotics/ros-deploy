---
title: Fleet Cameras
blurb: Every camera in the garage with its direct browser URL, and why some are viewed on image_color.
order: 32
updated: 2026-08-09
tags: [cameras, viewing]
---

# Fleet Cameras

Every camera in the robot garage, with its direct browser URL. All streams are
MJPEG from each host's `web_video_server` — click and watch, no ROS needed.

| Host | Camera | Sensor | Watch it | Notes |
|------|--------|--------|----------|-------|
| skadi | camera0 | IMX296 global-shutter | <http://skadi:8080/stream_viewer?topic=/skadi/camera0/camera/image_color> | 640x480 @ 10 fps |
| vidar | camera0 | IMX296 global-shutter | <http://vidar:8080/stream_viewer?topic=/vidar/camera0/camera/image_color> | Pi 5, 640x480 |
| vidar | camera1 | IMX296 global-shutter | <http://vidar:8080/stream_viewer?topic=/vidar/camera1/camera/image_color> | Pi 5, 640x480 |
| ali | camera0 | IMX219 (Camera Module v2) | <http://ali:8080/stream_viewer?topic=/ali/camera0/camera/image_raw> | 640x480 |
| vali | camera0 | IMX708 (Camera Module 3) | <http://vali:8080/stream_viewer?topic=/vali/camera0/camera/image_raw> | 640x480 |
| baldur | camera0 | IMX219 (Camera Module v2) | *disabled* | Camera stack off on the drive robot (it destabilized driving); re-enable via `cameras.yml` |

Each host also serves a stream index at `http://<host>:8080/` listing every
available topic.

## Why some URLs say image_color and others image_raw

The R/B channel swap is a property of the **sensor**: IMX296 arrives with red
and blue mislabeled, so those cameras run a correction relay and are viewed on
`image_color`. IMX219 and IMX708 arrive correct and are viewed on `image_raw`.
If a camera ever looks red/blue swapped, check which topic you are viewing
before touching any config. (The cameras role derives all of this from the
sensor declared in `cameras_list` — see `roles/cameras/defaults/main.yml`.)

## Caveats

- Resolutions are capped at 640x480 fleet-wide: the Pis cannot JPEG-encode
  bigger frames fast enough for the viewer page, and `web_video_server` stalls
  for 20-30 s when overloaded. Raise per-host only for single-camera viewing.
- ROS-side topics (domain 42): the same names without the URL wrapper, e.g.
  `/skadi/camera0/camera/image_color` plus `/compressed` variants.
