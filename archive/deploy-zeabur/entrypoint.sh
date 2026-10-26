#!/bin/sh
set -e

export PORT="${PORT:-8080}"

envsubst '${PORT}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 &

exec nginx -g 'daemon off;'
