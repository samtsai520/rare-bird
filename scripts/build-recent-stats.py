#!/usr/bin/env python3
"""
build-recent-stats.py — 產出「最近 4 個完整整天」的觀察統計 JSON

由每日 cron 呼叫（建議凌晨 3 點執行，此時前一整天資料已完整）。
輸出 public/data/recent-stats.json，前端純讀（零 live API 請求）。

「3 個完整整天」＝ 昨天、前天、大前天（不含今天，因凌晨今天無資料）。
與「值得一看」的目標窗口對應，但改用完整整天避免缺資料。

安全性：API key 從環境變數 EBIRD_API_KEY 或 gitignored config.json 讀取。
用法：
  python3 scripts/build-recent-stats.py
產出：public/data/recent-stats.json
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = PROJECT_ROOT / "public" / "data" / "recent-stats.json"
REQUEST_DELAY = 2.5
MAX_RETRIES = 3


def _load_api_key():
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if key:
        return key
    cfg = PROJECT_ROOT / "config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("ebird_api_key", "")
        except Exception:
            pass
    sys.exit("錯誤：找不到 eBird API key（環境變數 EBIRD_API_KEY 或 config.json）")


def fetch_historic(key, date_str):
    """抓單日全台 historic 觀察，含重試。回傳 raw records list。"""
    y, m, d = date_str.split("-")
    url = f"https://api.ebird.org/v2/data/obs/TW/historic/{y}/{int(m)}/{int(d)}?includeProvisional=true"
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 3 * (attempt + 1)
                print(f"  警告：{date_str} 第 {attempt + 1} 次失敗（{e}），{wait}s 後重試")
                time.sleep(wait)
            else:
                print(f"  ❌ {date_str} 重試耗盡，跳過")
                return None
    return None


def fmt(dt):
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d}"


def main():
    key = _load_api_key()

    # 最近 3 個完整整天：昨天往前推 3 天
    from datetime import datetime, timedelta
    days = []
    today = datetime.now()
    for i in range(1, 4):
        days.append(fmt(today - timedelta(days=i)))

    print("=== 產出 recent-stats.json ===")
    print(f"窗口：{days[0]}（昨天）~ {days[-1]}（大大前天）\n")

    per_day = []
    all_checklists = set()
    all_species = set()
    for d in days:
        data = fetch_historic(key, d)
        time.sleep(REQUEST_DELAY)
        if data is None:
            continue
        subs = set(r.get("subId") for r in data if r.get("subId"))
        codes = set(r.get("speciesCode") for r in data if r.get("speciesCode"))
        per_day.append({
            "date": d,
            "checklists": len(subs),
            "species": len(codes),
            "records": len(data),
        })
        all_checklists |= subs
        all_species |= codes
        print(f"  {d}: 列表={len(subs):>3}  鳥種={len(codes):>3}  紀錄={len(data)}")

    if not per_day:
        sys.exit("錯誤：4 天皆抓取失敗，未寫入檔案")

    out = {
        "lastUpdated": today.strftime("%Y-%m-%d"),
        "windowStart": days[-1],
        "windowEnd": days[0],
        "days": per_day,
        "total": {
            "checklists": len(all_checklists),
            "species": len(all_species),
        },
    }

    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 已寫入 {OUT_FILE.relative_to(PROJECT_ROOT)}")
    print(f"   合計(4天去重): 觀察列表={out['total']['checklists']}  鳥種={out['total']['species']}")


if __name__ == "__main__":
    main()
