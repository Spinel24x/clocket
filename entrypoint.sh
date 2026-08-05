#!/bin/bash
mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx
cd /app
python3 main.py &
sleep 3
exec nginx -c /app/nginx.conf -g "daemon off;"
