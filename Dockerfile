FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN curl -sL https://github.com/XTLS/Xray-core/releases/download/v1.8.21/Xray-linux-64.zip -o /tmp/xray.zip && \
    unzip /tmp/xray.zip -d /usr/local/bin/ && rm /tmp/xray.zip && chmod +x /usr/local/bin/xray

WORKDIR /app
COPY . .
RUN mkdir -p /app/configs /app/data /var/log/nginx /var/lib/nginx /tmp/nginx && chmod +x entrypoint.sh

EXPOSE 8080
CMD ["/app/entrypoint.sh"]
