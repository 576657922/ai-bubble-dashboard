#!/bin/zsh
# 双击此文件 = 抓最新数据 + 打开仪表盘
# 数据12小时内抓过就不重复抓（想强制刷新：直接运行 python3 fetch_data.py）
cd "$(dirname "$0")"

if [ -z "$(find data/data.js -mmin -720 2>/dev/null)" ]; then
  echo "📡 数据超过12小时，正在抓取最新数据（约半分钟）……"
  python3 fetch_data.py || echo "⚠️ 抓取失败，将使用上次的数据打开（稍后可重试）"
else
  echo "✅ 数据仍然新鲜（12小时内抓过），直接打开。"
fi

if [ -z "$SKIP_OPEN" ]; then
  open index.html
fi
