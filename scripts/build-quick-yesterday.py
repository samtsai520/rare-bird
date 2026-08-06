#!/usr/bin/env python3
"""
build-quick-yesterday.py — 產出「有鳥快看」的昨天靜態清單

由每日清晨 cron 呼叫（建議凌晨 3 點，昨日資料已完整）。
輸出 public/data/quick-yesterday.json，前端純讀（零 live eBird 請求）。

目的：昨天的觀測資料已固定不變，不必讓每個使用者各自向 eBird 下載。
改為伺服器端一次抓取、預先減黑名單、分類海拔/本島/外島，
存成 CDN 靜態檔。使用者端「有鳥快看」只須 live 抓今天的 back=1，
昨天直接讀此檔，eBird 下載量再砍半、首屏秒開。

輸出格式與前端 fetchQuick 產生的 quickList 相同：
[
  {
    "speciesCode": "...",
    "comNameZh": "中文名",
    "comNameEn": "English",
    "cats": { "flat": [sighting...], "mountain": [...], "island": [...] }
  }
]

安全性：API key 從環境變數 EBIRD_API_KEY 或 gitignored config.json 讀取。
用法：
  python3 scripts/build-quick-yesterday.py
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "public" / "data"
OUT_FILE = DATA_DIR / "quick-yesterday.json"
MAX_RETRIES = 3

MOUNTAIN_ELEV = 300  # 本島山地門檻（海拔 ≥300m），與 App.jsx 一致
ISLAND_KEYWORDS = ["金門", "馬祖", "澎湖", "蘭嶼", "綠島", "小琉球"]


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
    """抓單日全台 historic 觀察，含重試。回傳 raw records list 或 None。"""
    y, m, d = date_str.split("-")
    url = f"https://api.ebird.org/v2/data/obs/TW/historic/{y}/{int(m)}/{int(d)}?includeProvisional=true"
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 3 * (attempt + 1)
                print(f"  警告：{date_str} 第 {attempt + 1} 次失敗（{e}），{wait}s 後重試")
                time.sleep(wait)
            else:
                print(f"  ❌ {date_str} 重試耗盡")
                return None
    return None


def is_island(name):
    return any(k in (name or "") for k in ISLAND_KEYWORDS)


def lookup_elevations(locs):
    """批次查海拔（opentopodata ASTER 30m，每批 100 點）。回傳 {(lat,lng): elevation}。

    注意：Open-Elevation API 從此主機常回 504，故改用 opentopodata（免 key、免費、支援批量）。
    與前端瀏覽器端仍用 Open-Elevation 不衝突——前端只處理「今天」的資料。
    """
    elev = {}
    for i in range(0, len(locs), 100):
        batch = locs[i:i + 100]
        loc_str = "|".join(f"{lat},{lng}" for lat, lng in batch)
        url = f"https://api.opentopodata.org/v1/aster30m?locations={loc_str}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            for res in data.get("results", []):
                loc = res.get("location", {})
                key = (round(loc["lat"], 5), round(loc["lng"], 5))
                elev[key] = res.get("elevation")
        except Exception:
            pass  # 海拔查詢失敗時該點視為平地
    return elev


def main():
    key = _load_api_key()

    # 昨天（完整整天）
    yesterday = datetime.now() - timedelta(days=1)
    yest_str = yesterday.strftime("%Y-%m-%d")
    print("=== 產出 quick-yesterday.json ===")
    print(f"昨天：{yest_str}")

    records = fetch_historic(key, yest_str)
    if not records:
        sys.exit(f"錯誤：抓取昨天（{yest_str}）失敗，未寫入檔案")

    # 載入中文名 + 黑名單
    names_data = json.loads((DATA_DIR / "taiwan-birds.json").read_text(encoding="utf-8"))
    names = names_data.get("species", {})
    blacklist_data = json.loads((DATA_DIR / "blacklist.json").read_text(encoding="utf-8"))
    blacklist = set(blacklist_data.get("codes", []))

    print(f"  raw 紀錄：{len(records)}")
    before = len(records)
    records = [r for r in records if r.get("speciesCode") not in blacklist]
    print(f"  減黑名單後：{before} → {len(records)}")

    # 去重觀測點 → 海拔查詢
    loc_set = {}
    for r in records:
        try:
            lat = float(r.get("lat"))
            lng = float(r.get("lng"))
        except (TypeError, ValueError):
            continue
        key_loc = (round(lat, 5), round(lng, 5))
        loc_set.setdefault(key_loc, (lat, lng))
    elev_map = lookup_elevations(list(loc_set.values()))
    print(f"  去重觀測點：{len(loc_set)}（海拔命中 {len(elev_map)}）")

    # 分類
    cat_recs = {}  # code -> {cat: [records]}
    for r in records:
        try:
            lat = float(r.get("lat"))
            lng = float(r.get("lng"))
        except (TypeError, ValueError):
            continue
        name = r.get("locName") or ""
        key_loc = (round(lat, 5), round(lng, 5))
        elev = elev_map.get(key_loc)
        if is_island(name):
            cat = "island"
        elif elev is not None and elev >= MOUNTAIN_ELEV:
            cat = "mountain"
        else:
            cat = "flat"
        code = r["speciesCode"]
        cat_recs.setdefault(code, {"flat": [], "mountain": [], "island": []})
        cat_recs[code][cat].append(r)

    # 轉成 list（與前端 quickList 同格式）
    out_list = []
    for code, cats in cat_recs.items():
        t = names.get(code, {})
        out_list.append({
            "speciesCode": code,
            "comNameZh": (t.get("comNameZh") if t else None) or (cats.get("flat") or cats.get("mountain") or cats.get("island"))[0].get("comName"),
            "comNameEn": (t.get("comNameEn") if t else None) or (cats.get("flat") or cats.get("mountain") or cats.get("island"))[0].get("comName"),
            "cats": cats,
        })

    out = {
        "date": yest_str,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "speciesCount": len(out_list),
        "species": out_list,
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 已寫入 {OUT_FILE.relative_to(PROJECT_ROOT)}，共 {len(out_list)} 種")


if __name__ == "__main__":
    main()
