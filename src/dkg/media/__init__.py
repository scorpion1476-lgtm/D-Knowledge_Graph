"""Media plane: image and video ingestion into the shared knowledge-graph model.

Every capability here is optional and capability-detected. The core installs and
runs without any media tool or model present; this package degrades cleanly with
a clear message when a tool is absent. Copyleft tools (ffprobe, ffmpeg, libheif
behind HEIC) are used only as external binaries via subprocess, never linked.
"""

from __future__ import annotations
