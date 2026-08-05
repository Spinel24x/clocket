#!/bin/bash
set -e

mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx

# Start API (which also starts Xray internally)
cd /app
python3 main.py &
sleep 3

# Start Nginx (foreground)
exec nginx -c /app/nginx.conf -g "daemon off;"
