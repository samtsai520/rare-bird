#!/usr/bin/env python3
"""
verify_historic.py — 驗證 eBird historic 資料完整性（對照用）

用途：在建置「今年首見」表之前，確認 historic 逐日端點是否漏鳥。
方法：把「指定月份 historic 逐日累積」的鳥種集合，跟 eBird 網站的
「當月鳥種清單」比對。若兩者一致 → historic 可信；若少 → 可能有漏。

※ 網站比對需人工：把本腳本輸出的鳥種數/清單，與 https://ebird.org/lists/TW?yr=cur 的當月清單對照。

用法：
  python3 scripts/verify_historic.py --year 2026 --month 1 [--day 15]
     → 只累積該月 1..15 日（測月份前半，省請求）
  預設跑整個指定月份，逐日循序抓取，顯示每日鳥種數與累積總數。

輸出名欄位：
  date      當日日期
  new       當日新增鳥種數（相對於累積）
  cum       累積到當日之鳥種總數
  rate      cum/date（平均每日新增率，供判斷資料是否平滑）
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_api_key():
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if key:
        return key
    cfg = PROJECT_ROOT / "config.json"
    if cfg.exists():
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                key = json.load(f).get("ebird_api_key", "").strip()
        except Exception:
            key = ""
    if key:
        return key
    raise RuntimeError("找不到 eBird API key（環境變數 EBIRD_API_KEY 或 config.json）")


BASE_URL = "https://api.ebird.org/v2"


def api_fetch(url, key, retries=3):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if "429" in str(e) and attempt < retries:
                wait = 3 * (attempt + 1)
                print(f"  429，等待 {wait}s...", flush=True)
                time.sleep(wait)
                continue
            print(f"  ERROR: {e}", flush=True)
            return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int, required=True)
    ap.add_argument("--day", type=int, default=0, help="只累積到這天（0=整月）")
    ap.add_argument("--delay", type=float, default=2.5, help="每筆間隔秒數")
    args = ap.parse_args()

    key = _load_api_key()
    import calendar
    last_day = calendar.monthrange(args.year, args.month)[1]
    end_day = args.day if args.day else last_day

    print(f"驗證 {args.year}/{args.month:02d}/01..{end_day:02d} historic 資料...", flush=True)
    cumulative = set()
    for d in range(1, end_day + 1):
        url = (
            f"{BASE_URL}/data/obs/TW/historic/{args.year}/{args.month}/{d}"
            f"?includeProvisional=true&detail=simple"
        )
        data = api_fetch(url, key)
        if data is None:
            print(f"{args.month:02d}/{d:02d}  抓取失敗 ✗", flush=True)
        else:
            codes = {x.get("speciesCode") for x in data if x.get("speciesCode")}
            before = len(cumulative)
            cumulative |= codes
            new = len(cumulative) - before
            rate = len(cumulative) / d
            print(
                f"{args.month:02d}/{d:02d}  新增 {new:>3}  累積 {len(cumulative):>3}  "
                f"平均每日新增率 {rate:.2f}",
                flush=True,
            )
        time.sleep(args.delay)

    print("\n===== 驗證結果 =====", flush=True)
    print(f"範圍: {args.year}/{args.month:02d}/01..{end_day:02d}", flush=True)
    print(f"累積鳥種總數: {len(cumulative)}", flush=True)
    print("\n👉 請對照 eBird 網站當月清單 (https://ebird.org/lists/TW?yr=cur)", flush=True)
    print(f"   網站當月鳥種數應 ≈ 此累積數 ({len(cumulative)})。若明顯少 → historic 漏鳥，勿回填。", flush=True)


if __name__ == "__main__":
    main()
