#!/usr/bin/env python3
"""AI 泡沫仪表盘数据抓取脚本（仅用标准库，无需安装依赖、无需 API key）。

用法:  python3 fetch_data.py
产出:  data/data.js  （index.html 直接读取，双击打开也能用）

数据源:
  - FRED 公开 CSV 接口 (fred.stlouisfed.org/graph/fredgraph.csv) —— 信用利差、
    收益率曲线、金融条件指数、VIX、联邦基金利率、标普500
  - multpl.com —— 席勒 CAPE（抓不到就退回 manual.json 里的手填值）
  - data/manual.json —— 季度性/定性指标，手动维护
"""
import json
import re
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) ai-bubble-dashboard/1.0"}

# FRED 序列: id -> (起始日期, 中文备注)
FRED_SERIES = {
    "BAMLH0A0HYM2": ("2019-01-01", "高收益债期权调整利差 %"),
    "BAMLH0A3HYC":  ("2019-01-01", "CCC及以下级利差 %"),
    "T10Y2Y":       ("2019-01-01", "10年-2年期国债利差 %"),
    "NFCI":         ("2019-01-01", "芝加哥联储全国金融条件指数"),
    "VIXCLS":       ("2019-01-01", "VIX 恐慌指数"),
    "DFF":          ("2019-01-01", "联邦基金有效利率 %"),
    "SP500":        ("2019-01-01", "标普500指数"),
}


def http_get(url, timeout=30, ua=None):
    # 注意：FRED 的防火墙会掐掉「自称 Mozilla 但指纹不像浏览器」的请求
    # （表现为超时/HTTP2 RST），对 FRED 必须用 curl 默认 UA（ua=None）。
    # macOS 自带 Python 常缺根证书且连接不稳，优先用系统 curl，
    # 没有 curl 的环境再退回 urllib（可选配合 certifi 证书）。
    try:
        cmd = ["curl", "-sS", "--http1.1", "--max-time", str(timeout)]
        if ua:
            cmd += ["-A", ua]
        out = subprocess.run(cmd + [url], capture_output=True, text=True,
                             check=True)
        return out.stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    ctx = None
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": ua} if ua else {})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_fred(series_id, start):
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id={series_id}&cosd={start}")
    text = http_get(url)
    points = []
    for line in text.splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) != 2 or parts[1] in (".", ""):
            continue
        try:
            points.append([parts[0], float(parts[1])])
        except ValueError:
            continue
    return points


def downsample(points, keep_daily_days=180, step=5):
    """近半年保留日频，更早的每5个交易日取1个点，控制文件体积。"""
    if not points:
        return points
    cutoff = (datetime.strptime(points[-1][0], "%Y-%m-%d")
              - timedelta(days=keep_daily_days)).strftime("%Y-%m-%d")
    old = [p for p in points if p[0] < cutoff]
    recent = [p for p in points if p[0] >= cutoff]
    return old[::step] + recent


def moving_avg(values, window):
    out = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= window:
            s -= values[i - window]
        out.append(s / window if i >= window - 1 else None)
    return out


def fetch_cape():
    """从 multpl.com 抓当前席勒 CAPE，失败返回 None。"""
    try:
        html = http_get("https://www.multpl.com/shiller-pe",
                        ua=UA["User-Agent"])
        m = re.search(r'id="current"[^>]*>.*?([\d.]+)', html, re.S)
        if m:
            v = float(m.group(1))
            if 5 < v < 100:
                return v
    except Exception as e:
        print(f"  multpl 抓取失败（用手填值兜底）: {e}", file=sys.stderr)
    return None


def main():
    manual = json.loads((ROOT / "data" / "manual.json").read_text("utf-8"))

    auto = {}
    for i, (sid, (start, note)) in enumerate(FRED_SERIES.items()):
        if i:
            time.sleep(2)  # 对 FRED 保持礼貌间隔
        for attempt in (1, 2, 3):
            try:
                pts = fetch_fred(sid, start)
                print(f"  FRED {sid}: {len(pts)} 点, "
                      f"最新 {pts[-1] if pts else '无'}", flush=True)
                auto[sid] = pts
                break
            except Exception as e:
                print(f"  FRED {sid} 第{attempt}次失败: {e}", file=sys.stderr)
                auto[sid] = []
                if attempt < 3:
                    time.sleep(30)  # 等限流窗口过去

    values = {}

    def put(ind_id, points, transform=None):
        if not points:
            return
        series = downsample(points)
        if transform:
            series = transform(series)
        if not series:
            return
        values[ind_id] = {
            "value": series[-1][1],
            "asOf": series[-1][0],
            "series": series,
        }

    put("hy_oas", auto["BAMLH0A0HYM2"])
    put("ccc_oas", auto["BAMLH0A3HYC"])
    put("yield_curve", auto["T10Y2Y"])
    put("nfci", auto["NFCI"])
    put("vix", auto["VIXCLS"])
    put("fedfunds", auto["DFF"])

    # 标普500 相对 200 日均线的乖离率（%），趋势健康度指标
    sp = auto["SP500"]
    if len(sp) > 200:
        closes = [p[1] for p in sp]
        ma200 = moving_avg(closes, 200)
        ext = [[sp[i][0], round((closes[i] / ma200[i] - 1) * 100, 2)]
               for i in range(len(sp)) if ma200[i]]
        values["sp500_ext200"] = {
            "value": ext[-1][1],
            "asOf": ext[-1][0],
            "series": downsample(ext),
            "extra": {"close": closes[-1], "ma200": round(ma200[-1], 1)},
        }

    # 席勒 CAPE：优先自动抓取，失败退回手填
    cape = fetch_cape()
    if cape is not None:
        values["shiller_cape"] = {
            "value": cape,
            "asOf": datetime.now().strftime("%Y-%m-%d"),
            "fetched": True,
        }
        print(f"  multpl CAPE: {cape}", flush=True)

    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "values": values,
        "manual": manual["indicators"],
        "events": manual.get("events", []),
    }

    out = ROOT / "data" / "data.js"
    out.write_text(
        "// 由 fetch_data.py 自动生成，勿手改（手动指标请改 data/manual.json）\n"
        "window.BUBBLE_DATA = "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        "utf-8",
    )
    print(f"✅ 已写入 {out}（{out.stat().st_size // 1024} KB），"
          f"自动指标 {len(values)} 个，手动指标 {len(manual['indicators'])} 个")


if __name__ == "__main__":
    main()
