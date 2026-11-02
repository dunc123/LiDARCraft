#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  LiDARCraft - 1:1 Real-World LiDAR & OpenStreetMap Server"
echo "============================================================"

python3 -m pip install -q -r requirements.txt

LOC="${1:-}"
if [ -z "$LOC" ]; then
    read -r -p "Enter a place name, postcode, or Google Maps URL (default: Glastonbury): " LOC
fi
LOC="${LOC:-Glastonbury}"

python3 build_map.py "$LOC" --spawn --run
