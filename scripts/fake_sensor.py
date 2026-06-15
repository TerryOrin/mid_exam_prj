"""選修 Workshop — 模擬感測器：POST 一筆水質讀數到你 Django。

用法（Win/Mac/Linux 通用，只要有 Python 3）：

    python scripts/fake_sensor.py --pond 1
    python scripts/fake_sensor.py --pond 1 --loop 5      # 每 5 秒打一次
    python scripts/fake_sensor.py --pond 1 --status danger --do 2.5

讀 env：
    SHUIJING_URL              預設 http://127.0.0.1:8000
    SENSOR_INGEST_TOKEN       必填，從 .env 拿
"""

import argparse
import os
import random
import sys
import time
import urllib.error
import urllib.request
import json
from pathlib import Path


DEFAULT_URL = os.environ.get("SHUIJING_URL", "http://127.0.0.1:8000")


def load_dotenv(path: Path) -> None:
    """讀同目錄 .env 進 os.environ（簡化版，不裝 python-dotenv 也能跑）。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def fake_reading(args) -> dict:
    rng = random.Random()
    return {
        "pond_code": args.pond,
        "water_temperature": round(rng.uniform(26.0, 31.0), 1) if args.temp is None else args.temp,
        "ph": round(rng.uniform(7.0, 8.4), 1) if args.ph is None else args.ph,
        "dissolved_oxygen": round(rng.uniform(3.5, 7.5), 1) if args.do is None else args.do,
        "salinity": round(rng.uniform(15.0, 22.0), 1) if args.salinity is None else args.salinity,
        "status": args.status,
        "alert_message": args.alert,
    }


def post_one(base_url: str, token: str, payload: dict, verbose: bool = True) -> bool:
    url = base_url.rstrip("/") + "/api/sensor/"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Sensor-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")
            if verbose:
                print(f"[{resp.status}] {payload} -> {data}")
            return True
    except urllib.error.HTTPError as e:
        print(f"[{e.code}] {e.read().decode('utf-8','ignore')}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"[ERR] 連不到 {url} — {e}", file=sys.stderr)
        return False


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    load_dotenv(here / ".env")

    p = argparse.ArgumentParser(description="模擬感測器 → POST 進 Django Reading model")
    p.add_argument("--url", default=os.environ.get("SHUIJING_URL", DEFAULT_URL),
                   help="Django 站台 base URL（預設 %(default)s）")
    p.add_argument("--token", default=os.environ.get("SENSOR_INGEST_TOKEN"),
                   help="X-Sensor-Token；預設讀 env / .env")
    p.add_argument("--pond", default="1", help="pond_code（預設 1）")
    p.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                   help="迴圈每 N 秒打一次（0=只打 1 次）")
    p.add_argument("--temp", type=float, default=None, help="水溫，不指定 → 隨機 26~31")
    p.add_argument("--ph", type=float, default=None, help="pH，不指定 → 隨機 7.0~8.4")
    p.add_argument("--do", type=float, default=None, help="溶氧，不指定 → 隨機 3.5~7.5")
    p.add_argument("--salinity", type=float, default=None, help="鹽度，不指定 → 隨機 15~22")
    p.add_argument("--status", default="normal", choices=["normal", "warning", "danger"])
    p.add_argument("--alert", default="", help="警示訊息（status≠normal 時建議填）")
    args = p.parse_args()

    if not args.token:
        print("ERROR: 沒有 token — 設 SENSOR_INGEST_TOKEN env 或用 --token", file=sys.stderr)
        return 2

    if args.loop <= 0:
        return 0 if post_one(args.url, args.token, fake_reading(args)) else 1

    print(f"loop 模式：每 {args.loop}s 打一筆到 {args.url}，Ctrl+C 停")
    try:
        while True:
            post_one(args.url, args.token, fake_reading(args))
            time.sleep(args.loop)
    except KeyboardInterrupt:
        print("\n已停止")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
