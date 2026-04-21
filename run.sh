#!/usr/bin/env bash
# Linux：后台管理 GoldQuant（Uvicorn，无 --reload）
# 用法: ./run.sh start|stop|restart
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PID_FILE="${ROOT}/goldquant.pid"
LOG_DIR="${ROOT}/logs"
LOG_FILE="${LOG_DIR}/goldquant.log"

usage() {
  echo "用法: $0 {start|stop|restart}" >&2
  echo "  start   - 后台启动，PID 写入 ${PID_FILE}" >&2
  echo "  stop    - 按 PID 停止进程" >&2
  echo "  restart - 先 stop（若存在），再 start" >&2
  exit 1
}

cmd_start() {
  mkdir -p "$LOG_DIR"

  if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
      echo "已在运行中 (PID ${OLD_PID})，请先执行: $0 stop"
      exit 1
    fi
    echo "清理过期的 PID 文件: ${PID_FILE}"
    rm -f "$PID_FILE"
  fi

  local venv_py="${ROOT}/.venv/bin/python"
  if [[ ! -x "$venv_py" ]]; then
    echo "错误: 未找到虚拟环境解释器: ${venv_py}" >&2
    echo "请先执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi

  if [[ -f "${ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT}/.env"
    set +a
  fi

  local host="${GOLDQUANT_HOST:-0.0.0.0}"
  local port="${GOLDQUANT_PORT:-8000}"

  nohup "${venv_py}" -m uvicorn app.main:app \
    --host "${host}" \
    --port "${port}" \
    >> "${LOG_FILE}" 2>&1 &

  echo $! > "${PID_FILE}"
  echo "已启动 PID $(cat "${PID_FILE}")"
  echo "监听: http://${host}:${port}/  （文档: http://127.0.0.1:${port}/docs）"
  echo "日志: ${LOG_FILE}"
}

# 返回值: 0=已停止；1=无 PID 文件或进程已不存在（已清理 pid 文件）
stop_service() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "未找到 PID 文件: ${PID_FILE}（服务可能未通过本脚本启动）" >&2
    return 1
  fi

  local pid
  pid="$(cat "$PID_FILE")"

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "进程 ${pid} 不存在，删除过期 PID 文件" >&2
    rm -f "$PID_FILE"
    return 1
  fi

  echo "正在停止 PID ${pid} ..."
  kill "$pid" || true

  local _
  for _ in {1..50}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "已停止。"
      return 0
    fi
    sleep 0.1
  done

  echo "进程未在超时内退出，发送 SIGKILL ..." >&2
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "已强制结束。"
  return 0
}

cmd_stop() {
  stop_service
}

cmd_restart() {
  set +e
  stop_service
  set -e
  cmd_start
}

case "${1:-}" in
  start)
    cmd_start
    ;;
  stop)
    cmd_stop
    ;;
  restart)
    cmd_restart
    ;;
  *)
    usage
    ;;
esac
