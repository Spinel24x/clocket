#!/bin/bash
set -e

mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx

# Start Xray first
xray run -config /app/configs/xray.json &
sleep 2

# Start API
cd /app
python3 main.py &
sleep 3

# Start Nginx last
exec nginx -c /app/nginx.conf -g "daemon off;"
