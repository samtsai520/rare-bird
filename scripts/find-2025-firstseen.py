#!/usr/bin/env python3
"""
find-2025-firstseen.py — 找出「去年有今年沒有」鳥種在指定年份的首次出現日期

只追蹤指定的 speciesCode 集合，逐日掃描該年，記錄每種首次出現日期。
全部找到即提前結束（不需掃完整年）。

支援 --year 參數（預設 2025），產出 public/data/year-{YEAR}-firstseen.json
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
    sys.exit("錯誤：找不到 eBird API key")


def fetch_day_codes(y, m, d, key):
    """抓單日全台 historic，回傳 {code: obsDt}（只保留 obsValid=true 且非 hybrid）。"""
    url = (
        f"{BASE_URL}/data/obs/TW/historic/{y}/{m}/{d}"
        f"?includeProvisional=true&detail=simple"
    )
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            out = {}
            for x in data:
                code = x.get("speciesCode")
                if not code:
                    continue
                if not x.get("obsValid"):
                    continue
                out[code] = x.get("obsDt", "")
            return out
        except Exception as e:
            wait = 3 * (attempt + 1)
            if "429" in str(e):
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES:
                print(f"  {y}-{m:02d}-{d:02d} 第{attempt+1}次失敗({e})，{wait}s後重試", flush=True)
                time.sleep(wait)
            else:
                print(f"  ❌ {y}-{m:02d}-{d:02d} 重試耗盡", flush=True)
                return None
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025, help="目標年份（預設 2025）")
    args = ap.parse_args()
    YEAR = args.year
    OUT_FILE = PROJECT_ROOT / "public" / "data" / f"year-{YEAR}-firstseen.json"

    # 去年有今年沒有的鳥種
    last_year_species = json.loads(
        (PROJECT_ROOT / "public" / "data" / f"year-{YEAR}-species.json").read_text(encoding="utf-8")
    )
    this_year_fs = json.loads((PROJECT_ROOT / "public" / "data" / "first-seen.json").read_text(encoding="utf-8"))
    s_last = set(last_year_species["speciesCodes"])
    s_this = set(this_year_fs["species"].keys())
    targets = sorted(s_last - s_this)
    print(f"追蹤 {len(targets)} 種鳥的 {YEAR} 首次出現日期")

    key = _load_api_key()
    found = {}  # code -> firstSeen date
    cur = date(YEAR, 1, 1)
    end = date(YEAR, 12, 31)
    n = 0
    while cur <= end and len(found) < len(targets):
        day_map = fetch_day_codes(cur.year, cur.month, cur.day, key)
        if day_map is not None:
            day_str = cur.isoformat()
            for code in targets:
                if code not in found and code in day_map:
                    found[code] = day_str
            n += 1
            if n % 20 == 0:
                print(f"  [{n}] {day_str}  已找到 {len(found)}/{len(targets)}", flush=True)
        time.sleep(REQUEST_DELAY)
        cur += timedelta(days=1)

    # 補齊未找到的（掃完整年仍未出現）
    missing = [c for c in targets if c not in found]
    for c in missing:
        found[c] = None

    out = {
        "year": YEAR,
        "scannedDays": n,
        "speciesCount": len(found),
        "species": {c: {"firstSeen": found[c]} for c in targets},
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ 完成。掃描 {n} 天，找到 {len(found)-len(missing)}/{len(targets)} 種，寫入 {OUT_FILE.relative_to(PROJECT_ROOT)}")
    if missing:
        print("未找到（掃完整年無紀錄）:", ", ".join(missing))


if __name__ == "__main__":
    main()
