#!/bin/bash
set -e

mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx

# Generate Xray config first
python3 -c "from main import generate_xray_config; generate_xray_config()"

# Start Xray
xray run -config /app/configs/xray.json &
sleep 1

# Start API server
python3 /app/main.py &
sleep 2

# Start Nginx
exec nginx -c /app/nginx.conf -g "daemon off;"
