#!/bin/bash
set -e

mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx

cd /app
python3 -c "from main import generate_xray_config; generate_xray_config()"

# Start Xray
xray run -config /app/configs/xray.json &
sleep 1

# Start Nginx on 8080
nginx -c /app/nginx.conf &
sleep 2

# Start FastAPI on 8000
exec python3 main.py
