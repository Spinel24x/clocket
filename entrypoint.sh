#!/bin/bash
mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx
cd /app

# Start API (which also starts Xray)
python3 main.py &
sleep 3

# Start Nginx
exec nginx -c /app/nginx.conf -g "daemon off;"
