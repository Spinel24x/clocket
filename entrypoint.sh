#!/bin/bash
set -e

# Create directories
mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx

# Generate initial Xray config
cd /app
python3 -c "from main import generate_xray_config; generate_xray_config()"

# Start Xray in background
xray run -config /app/configs/xray.json &
sleep 1

# Start Nginx in background
nginx -c /app/nginx.conf -g 'daemon off;' &
sleep 1

# Start FastAPI (foreground - Railway needs this)
exec python3 main.py
