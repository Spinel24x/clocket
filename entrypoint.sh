#!/bin/bash
set -e

mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx

# Start Xray
xray run -config /app/configs/xray.json &
sleep 1

# Start API server
python3 /app/main.py &
sleep 1

# Start Nginx
exec nginx -c /app/nginx.conf -g "daemon off;"
