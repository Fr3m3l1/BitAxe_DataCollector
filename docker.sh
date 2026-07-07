#!/usr/bin/env bash
# Build and push the collector image (ARM64 for Raspberry Pi).
# Run locally with:
#   docker run -d --name bitaxe-collector --restart unless-stopped \
#     --env-file .env -v bitaxe-collector-data:/data \
#     fr3m3l/miner-data-collector:latest
set -euo pipefail
cd "$(dirname "$0")"

docker build --platform linux/arm64 -t fr3m3l/miner-data-collector:latest .
docker push fr3m3l/miner-data-collector:latest
