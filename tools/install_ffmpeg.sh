#!/usr/bin/env bash
# Install ffmpeg on a GitHub runner without getting stuck on slow apt mirrors.
# 1) use it if it is already there, 2) apt with a hard time limit, 3) static build from GitHub (BtbN).
set -u
if command -v ffmpeg >/dev/null 2>&1; then ffmpeg -version | head -1; exit 0; fi
if timeout 150 bash -c 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg'; then
  ffmpeg -version | head -1; exit 0
fi
echo "apt was too slow, using the static build"
URL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
for i in 1 2 3; do
  curl -fsSL --retry 3 -o /tmp/ffmpeg.tar.xz "$URL" && break
  sleep 5
done
mkdir -p /tmp/ffmpeg && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1
sudo cp /tmp/ffmpeg/bin/ffmpeg /tmp/ffmpeg/bin/ffprobe /usr/local/bin/
ffmpeg -version | head -1
