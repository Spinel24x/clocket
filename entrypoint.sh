#!/bin/bash

# Create required directories
mkdir -p /app/configs /app/data

# Generate initial Xray config
python3 -c "from main import generate_xray_config; generate_xray_config()"

# Start Xray in background
xray run -config /app/configs/xray.json &

# Start Nginx in background
nginx -c /app/nginx.conf &

# Start FastAPI
python3 main.py
