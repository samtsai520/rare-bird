#!/usr/bin/env python3
"""
build-2025-species.py — 抓取指定年份全年台灣觀察鳥種集合

eBird API 沒有「某年整年鳥種」端點，需逐日查 historic 再取聯集。
本腳本只記錄 speciesCode 集合（不去重計數、不存個別紀錄），輸出靜態檔
public/data/year-{YEAR}-species.json，供後續「去年 vs 今年」差異即時比對。

特性：
- 斷點續傳：已抓到的日期記在進度檔，重跑時跳過（自癒缺口）
- 重試 + 429 退避
- 只保留 speciesCode 集合，檔案小、跑得快
- 支援 --year 參數（預設 2025），產出對應年份的檔案

安全性：API key 從環境變數 EBIRD_API_KEY 或 gitignored config.json 讀取。
用法：
  python3 scripts/build-2025-species.py                       # 預設 2025
  python3 scripts/build-2025-species.py --year 2026           # 指定 2026
  python3 scripts/build-2025-species.py --dry-run             # 只印計劃

產出：public/data/year-{YEAR}-species.json
"""
import json
import os
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://api.ebird.org/v2"
REQUEST_DELAY = 2.5
MAX_RETRIES = 5


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


def api_fetch_day(y, m, d, key):
    """抓單日全台 historic speciesCode 集合；回傳 None 表示失敗（留缺口自癒）。

    過濾規則（比照 update-first-seen.py / 今年累積）：
    - 排除 obsValid=false 的記錄
    - 排除官方 taxonomy 分類為 hybrid 的鳥種
    """
    url = (
        f"{BASE_URL}/data/obs/TW/historic/{y}/{m}/{d}"
        f"?includeProvisional=true&detail=simple"
    )
    cat_map = _load_category_map()
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            out = set()
            for x in data:
                code = x.get("speciesCode")
                if not code:
                    continue
                if not x.get("obsValid"):
                    continue
                if cat_map.get(code) == "hybrid":
                    continue
                out.add(code)
            return out
        except Exception as e:
            wait = 3 * (attempt + 1)
            if "429" in str(e):
                print(f"  429 限速，等待 {wait}s...", flush=True)
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES:
                print(f"  {y}-{m:02d}-{d:02d} 第{attempt+1}次失敗({e})，{wait}s後重試", flush=True)
                time.sleep(wait)
            else:
                print(f"  ❌ {y}-{m:02d}-{d:02d} 重試耗盡，留缺口", flush=True)
                return None
    return None


_CATEGORY_MAP = None


def _load_category_map():
    """載入官方 eBird taxonomy 分類對照表（一次載入，供過濾雜交種）。"""
    global _CATEGORY_MAP
    if _CATEGORY_MAP is not None:
        return _CATEGORY_MAP
    p = PROJECT_ROOT / "public" / "data" / "ebird-category-map.json"
    try:
        _CATEGORY_MAP = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        _CATEGORY_MAP = {}
    return _CATEGORY_MAP


def load_progress(progress_file):
    if progress_file.exists():
        try:
            return json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done_dates": [], "species": []}


def save_progress(progress, progress_file):
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只印計劃，不發 API")
    ap.add_argument("--year", type=int, default=2025, help="目標年份（預設 2025）")
    args = ap.parse_args()
    YEAR = args.year
    OUT_FILE = PROJECT_ROOT / "public" / "data" / f"year-{YEAR}-species.json"
    PROGRESS_FILE = PROJECT_ROOT / "public" / "data" / f".year-{YEAR}-progress.json"
    key = None if args.dry_run else _load_api_key()

    # {YEAR}-01-01 .. {YEAR}-12-31
    start = date(YEAR, 1, 1)
    end = date(YEAR, 12, 31)
    total_days = (end - start).days + 1
    print(f"=== 抓取 {YEAR} 全年鳥種集合（{total_days} 天）===")

    progress = load_progress(PROGRESS_FILE)
    done = set(progress.get("done_dates", []))
    species = set(progress.get("species", []))
    print(f"已累積：{len(species)} 種，已完成 {len(done)}/{total_days} 天（斷點續傳）")

    if args.dry_run:
        remaining = (end - start).days + 1 - len(done)
        print(f"（dry-run）剩餘 {remaining} 天待抓")
        return

    cur = start
    n = 0
    while cur <= end:
        day_str = cur.isoformat()
        if day_str in done:
            cur += timedelta(days=1)
            continue
        day_set = api_fetch_day(cur.year, cur.month, cur.day, key)
        if day_set is not None:
            species |= day_set
            done.add(day_str)
            n += 1
            if n % 10 == 0 or n <= 3:
                print(f"  [{n}] {day_str}  累積 {len(species)} 種", flush=True)
            # 每 20 天存一次進度（防中斷丟失）
            if n % 20 == 0:
                save_progress({"done_dates": sorted(done), "species": sorted(species)}, PROGRESS_FILE)
        time.sleep(REQUEST_DELAY)
        cur += timedelta(days=1)

    # 最終存檔
    save_progress({"done_dates": sorted(done), "species": sorted(species)}, PROGRESS_FILE)
    out = {
        "year": YEAR,
        "fetchedDays": len(done),
        "speciesCount": len(species),
        "speciesCodes": sorted(species),
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    PROGRESS_FILE.unlink(missing_ok=True)  # 完成後清除進度檔
    print(f"\n✅ 已完成。{YEAR} 全年 {len(species)} 種，寫入 {OUT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
