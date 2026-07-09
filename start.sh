#!/bin/bash
# 腾讯云 Coding Plan 单一租户轮换租用系统
# 用法: bash start.sh

cd "$(dirname "$0")/backend"
PYTHON="/c/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"

echo ""
echo "  ========================================"
echo "    Coding Plan 单租户租用系统 v2.0"
echo "    腾讯云 Lite 40¥/月 | 一次一人 | 防检测"
echo "  ========================================"
echo ""

echo "[启动] 后端 API 服务 (端口 8899)..."
$PYTHON -c "import uvicorn; uvicorn.run('server:app', host='0.0.0.0', port=8899)" &
PID=$!
sleep 2

if kill -0 $PID 2>/dev/null; then
    echo "  ✓ 后端已启动: http://localhost:8899"
    echo "  ✓ API 文档:  http://localhost:8899/docs"
    echo "  ✓ 健康检查:  http://localhost:8899/api/health"
else
    echo "  ✗ 启动失败"
    exit 1
fi

echo ""
echo "  [前端] 直接用浏览器打开:"
echo "  file: $(dirname "$0")/frontend/index.html"
echo ""
echo "  ========================================"
echo "  按 Ctrl+C 停止服务"
echo "  ========================================"

trap "kill $PID 2>/dev/null; echo '已停止'; exit 0" INT
wait
