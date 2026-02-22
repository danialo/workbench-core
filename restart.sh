#!/bin/bash
# Restart wb web server
pkill -TERM -f "wb web" 2>/dev/null
sleep 2
# Verify all dead
if pgrep -f "wb web" > /dev/null 2>&1; then
    pkill -TERM -f "wb web" 2>/dev/null
    sleep 2
fi
source /home/d/git/workbench-core/.venv/bin/activate
wb web --host 0.0.0.0 --port 8080 > /tmp/wb-web.log 2>&1 &
echo "PID: $!"
sleep 2
curl -s http://172.239.66.45:8080/api/providers | python3 -m json.tool 2>/dev/null || echo "waiting..."
