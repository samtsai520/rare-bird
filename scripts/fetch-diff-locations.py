#!/usr/bin/env python3
"""
fetch-diff-locations.py — 補查「去年有今年沒有」及「今年有去年沒有」鳥種的首見日期地點

動態計算今年與去年，讀取對應的 year-{lastYear}-species.json + year-{lastYear}-firstseen.json
+ first-seen.json，用 historic API 查首見日當天紀錄，
輸出含地點的結果到 public/data/year-diff-locations.json。

跨年時自動切換：2026 年跑 → 比對 2025 vs 2026；2027 年跑 → 比對 2026 vs 2027。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASE = "https://api.ebird.org/v2"
REQUEST_DELAY = 2.5


def _load_api_key():
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if key:
        return key
    cfg = PROJECT / "config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("ebird_api_key", "")
        except Exception:
            pass
    sys.exit("錯誤：找不到 eBird API key")


API_KEY = _load_api_key()


def fetch_historic(y, m, d, key):
    url = f"{BASE}/data/obs/TW/historic/{y}/{m}/{d}?includeProvisional=true&detail=full"
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            wait = 3 * (attempt + 1)
            print(f"  retry {attempt+1} for {y}-{m:02d}-{d:02d}: {e}, wait {wait}s", flush=True)
            time.sleep(wait)
    print(f"  FAILED {y}-{m:02d}-{d:02d}", flush=True)
    return []


def extract_county(locName):
    keywords = [
        "台北","新北","桃園","台中","台南","高雄","基隆","新竹","嘉義",
        "苗栗","彰化","南投","雲林","屏東","宜蘭","花蓮","台東","澎湖",
        "金門","馬祖","連江","釣魚台","東沙","南海",
        "Taipei","New Taipei","Taoyuan","Taichung","Tainan","Kaohsiung",
        "Keelung","Hsinchu","Chiayi","Miaoli","Changhua","Nantou","Yunlin",
        "Pingtung","Yilan","Hualien","Taitung","Penghu","Kinmen","Matsu",
        "Lienchiang","Dongsha","Pratas"
    ]
    for kw in keywords:
        if kw in locName:
            return kw
    return locName.split(",")[-1].strip() if "," in locName else locName


def main():
    today = date.today()
    this_year = today.year
    last_year = this_year - 1

    # 動態載入去年全年鳥種集合
    last_species_path = PROJECT / f"public/data/year-{last_year}-species.json"
    if not last_species_path.exists():
        print(f"❌ 找不到 {last_species_path}", flush=True)
        print(f"   請先執行: python3 scripts/build-2025-species.py --year {last_year}", flush=True)
        sys.exit(1)

    with open(last_species_path) as f:
        y_last_sp = json.load(f)
    with open(PROJECT / "public/data/first-seen.json") as f:
        y_this_fs = json.load(f)
    with open(PROJECT / "public/data/taiwan-birds.json") as f:
        birds = json.load(f)
    names = birds.get("species", {})

    # 去年首見檔可能不存在（跨年後尚未產生），用空 dict 容錯
    last_firstseen_path = PROJECT / f"public/data/year-{last_year}-firstseen.json"
    y_last_fs = {"species": {}}
    if last_firstseen_path.exists():
        with open(last_firstseen_path) as f:
            y_last_fs = json.load(f)

    # Load static blacklist (常見鳥 + 外來逸鳥排除清單)
    blacklist = set()
    bl_path = PROJECT / "public/data/blacklist.json"
    if bl_path.exists():
        bl_data = json.loads(bl_path.read_text(encoding="utf-8"))
        blacklist = set(bl_data.get("codes", []))

    s_last = set(y_last_sp["speciesCodes"])
    s_this = set(y_this_fs["species"].keys())
    only_last = sorted(s_last - s_this - blacklist)   # 去年有、今年沒有（排除黑名單）
    only_this = sorted(s_this - s_last - blacklist)   # 今年有、去年沒有（排除黑名單）

    print(f"比較 {last_year} vs {this_year}：去年有今年沒有 {len(only_last)} 種，今年有去年沒有 {len(only_this)} 種", flush=True)

    def get_name(code):
        t = names.get(code)
        if t:
            return t.get("comNameZh", code), t.get("comNameEn", code)
        info = y_this_fs.get("species", {}).get(code)
        if info:
            return info.get("comNameZh", code), info.get("comNameEn", code)
        return code, code

    # Collect all unique dates
    dates_to_fetch = set()
    for code in only_last:
        fs = y_last_fs["species"].get(code, {}).get("firstSeen")
        if fs:
            dates_to_fetch.add(fs)
    for code in only_this:
        fs = y_this_fs["species"].get(code, {}).get("firstSeen")
        if fs:
            dates_to_fetch.add(fs)

    print(f"Total dates to fetch: {len(dates_to_fetch)}", flush=True)

    # Fetch all dates
    date_records = {}
    for i, ds in enumerate(sorted(dates_to_fetch)):
        dt = datetime.strptime(ds, "%Y-%m-%d")
        records = fetch_historic(dt.year, dt.month, dt.day, API_KEY)
        filtered = [r for r in records if r.get("obsValid") is not False]
        date_records[ds] = filtered
        if (i+1) % 10 == 0 or i < 3:
            print(f"  [{i+1}/{len(dates_to_fetch)}] {ds}: {len(filtered)} records", flush=True)
        time.sleep(REQUEST_DELAY)

    print(f"\nFetched {len(date_records)} dates. Matching species...", flush=True)

    def match_species(code, first_seen_date):
        recs = date_records.get(first_seen_date, [])
        return [r for r in recs if r.get("speciesCode") == code]

    def build_results(codes, fs_map):
        results = []
        for code in codes:
            zh, en = get_name(code)
            fs = fs_map.get(code, {}).get("firstSeen")
            if not fs:
                results.append({"code": code, "zh": zh, "en": en, "date": "N/A", "locations": []})
                continue
            matches = match_species(code, fs)
            locs = []
            seen = set()
            for m in matches:
                loc = m.get("locName", "")
                county = extract_county(loc)
                key = f"{county}|{loc}"
                if key not in seen:
                    seen.add(key)
                    locs.append({"county": county, "locName": loc, "lat": m.get("lat"), "lng": m.get("lng"), "obsReviewed": m.get("obsReviewed", False)})
            results.append({"code": code, "zh": zh, "en": en, "date": fs, "locations": locs})
        return results

    results_last = [r for r in build_results(only_last, y_last_fs["species"]) if r.get("locations") and len(r["locations"]) > 0]
    results_this = [r for r in build_results(only_this, y_this_fs["species"]) if r.get("locations") and len(r["locations"]) > 0]

    # Save JSON — 鍵名用 onlyLast/onlyThis（年份動態，不用寫死 2025/2026）
    out = {
        "lastYear": last_year,
        "thisYear": this_year,
        "onlyLast": results_last,
        "onlyThis": results_this,
    }
    out_path = PROJECT / "public" / "data" / "year-diff-locations.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved to {out_path}", flush=True)

    # Print tables
    print("\n" + "="*100, flush=True)
    print(f"{last_year} 年有、{this_year} 年還沒出現的鳥種（{len(results_last)} 種）", flush=True)
    print("="*100, flush=True)
    print(f"{'中文名稱':<14} {'英文名稱':<32} {'看見日期':<12} {'地點(縣市)'}", flush=True)
    print("-"*100, flush=True)
    for r in results_last:
        loc_str = "; ".join(f"{l['county']}-{l['locName']}" for l in r["locations"]) if r["locations"] else "(該日無紀錄)"
        print(f"{r['zh']:<14} {r['en']:<32} {r['date']:<12} {loc_str}", flush=True)

    print("\n" + "="*100, flush=True)
    print(f"{this_year} 年有、{last_year} 年沒有的鳥種（{len(results_this)} 種）", flush=True)
    print("="*100, flush=True)
    print(f"{'中文名稱':<14} {'英文名稱':<32} {'看見日期':<12} {'地點(縣市)'}", flush=True)
    print("-"*100, flush=True)
    for r in results_this:
        loc_str = "; ".join(f"{l['county']}-{l['locName']}" for l in r["locations"]) if r["locations"] else "(該日無紀錄)"
        print(f"{r['zh']:<14} {r['en']:<32} {r['date']:<12} {loc_str}", flush=True)


if __name__ == "__main__":
    main()