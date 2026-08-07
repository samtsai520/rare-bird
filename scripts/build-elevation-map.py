#!/usr/bin/env python3
"""
build-elevation-map.py — 產出「全台觀測點海拔對照表」靜態檔

由每日清晨 cron 呼叫。輸出 public/data/elevation-map.json：
  { "lat,lng": elevation, ... }   (座標四捨五入到 5 位)

用途：前端「有鳥快看」處理「今天」的資料時，直接查此表分類 本島山地/平地，
不必在瀏覽器打海拔 API（Open-Elevation 從瀏覽器常失敗 → 山地鳥被誤判平地）。

來源：直接從 eBird API 抓近 3 天全台觀測（recent?back=3），去重座標點後
用 opentopodata ASTER 30m 批量查海拔。自足腳本，不依賴其他腳本產出。

安全性：API key 從環境變數 EBIRD_API_KEY 或 gitignored config.json 讀取。
用法：
  python3 scripts/build-elevation-map.py
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "public" / "data"
OUT_FILE = DATA_DIR / "elevation-map.json"
BASE_URL = "https://api.ebird.org/v2"
BATCH = 100  # opentopodata 每批上限
BACK_DAYS = 3  # 抓最近 3 天的觀測點座標
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


def fetch_recent_points(key):
    """抓最近 BACK_DAYS 天全台觀測，回傳去重座標點 list [(lat, lng), ...]。"""
    url = (
        f"{BASE_URL}/data/obs/TW/recent"
        f"?back={BACK_DAYS}&detail=simple&includeProvisional=true"
    )
    data = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            break
        except Exception as e:
            if "429" in str(e) and attempt < MAX_RETRIES:
                wait = 3 * (attempt + 1)
                print(f"  429 限速，等待 {wait}s...", flush=True)
                time.sleep(wait)
                continue
            print(f"  ❌ 抓取 recent 失敗：{e}", flush=True)
            return None

    if not data:
        return None

    points = {}
    for r in data:
        if not isinstance(r, dict) or "lat" not in r or "lng" not in r:
            continue
        try:
            k = (round(float(r["lat"]), 5), round(float(r["lng"]), 5))
        except (TypeError, ValueError):
            continue
        points.setdefault(k, k)
    return list(points.keys())


def lookup_elevations(locs):
    """批量查海拔。回傳 {(lat,lng): elevation}。"""
    elev = {}
    for i in range(0, len(locs), BATCH):
        batch = locs[i:i + BATCH]
        loc_str = "|".join(f"{lat},{lng}" for lat, lng in batch)
        url = f"https://api.opentopodata.org/v1/aster30m?locations={loc_str}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.load(r)
                for res in data.get("results", []):
                    loc = res.get("location", {})
                    key = (round(loc["lat"], 5), round(loc["lng"], 5))
                    elev[key] = res.get("elevation")
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                else:
                    print(f"  ⚠️ 批次 {i//BATCH} 失敗：{e}")
        time.sleep(1)
    return elev


def main():
    key = _load_api_key()

    print(f"=== 產出 elevation-map.json ===  抓取最近 {BACK_DAYS} 天觀測點...", flush=True)
    pts = fetch_recent_points(key)
    if not pts:
        print("❌ 無法取得觀測點座標，未寫入檔案", flush=True)
        return 1

    print(f"  去重觀測點：{len(pts)}", flush=True)
    elev = lookup_elevations(pts)
    print(f"  海拔命中：{len(elev)}/{len(pts)}", flush=True)

    # 字串化 key 存檔
    out = {f"{lat},{lng}": round(e) for (lat, lng), e in elev.items()}
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"✅ 已寫入 {OUT_FILE.relative_to(PROJECT_ROOT)}（{len(out)} 點）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())