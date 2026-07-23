#!/usr/bin/env bash
# 在已 clone 好的 wechat_crawler 目录内运行。
# 期望同级目录已有 wechat-download-api（见 README 第 2 步）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(cd "$ROOT/.." && pwd)"
cd "$ROOT"

echo "==> 工作目录: $ROOT"
echo "==> 上级目录: $PARENT"

if [[ ! -d "$PARENT/wechat-download-api" ]]; then
  echo "警告: 未找到同级目录 $PARENT/wechat-download-api"
  echo "请先在上级目录执行:"
  echo "  git clone https://github.com/tmwgsicp/wechat-download-api.git"
fi

if [[ ! -d .venv ]]; then
  echo "==> 创建虚拟环境 .venv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run.py init-db

if [[ ! -f "$PARENT/name_list.xlsx" ]]; then
  echo "==> 生成示例名单 $PARENT/name_list.xlsx"
  python scripts/create_name_list_example.py --out "$PARENT/name_list.xlsx"
else
  echo "==> 已存在 $PARENT/name_list.xlsx ，跳过示例生成"
fi

echo
echo "下一步："
echo "  1) cd $PARENT/wechat-download-api && cp env.example .env"
echo "     编辑 .env 设置 SITE_URL=http://127.0.0.1:5000"
echo "     bash start.sh  并在浏览器扫码登录"
echo "  2) 回到本目录，激活 .venv 后执行:"
echo "     python run.py import-platform-from-download-api"
echo "     python run.py platform-status"
echo "  3) 编辑上级 name_list.xlsx 后："
echo "     python run.py import-list && python run.py resolve --limit 2"
echo "     python run.py history --limit 1 && python run.py content --limit 10"
