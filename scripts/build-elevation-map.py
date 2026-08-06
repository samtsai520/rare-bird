#!/usr/bin/env python3
"""
build-elevation-map.py — 產出「全台觀測點海拔對照表」靜態檔

由每日清晨 cron 呼叫。輸出 public/data/elevation-map.json：
  { "lat,lng": elevation, ... }   (座標四捨五入到 5 位)

用途：前端「有鳥快看」處理「今天」的資料時，直接查此表分類 本島山地/平地，
不必在瀏覽器打海拔 API（Open-Elevation 從瀏覽器常失敗 → 山地鳥被誤判平地）。

來源：recent-cumulative.json（近數日全台觀察，含絕大多數熱點座標）。
海拔：opentopodata ASTER 30m（伺服器端可用；Open-Elevation 從本主機回 504）。
"""
import json
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "public" / "data"
OUT_FILE = DATA_DIR / "elevation-map.json"
BATCH = 100  # opentopodata 每批上限


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
    src = DATA_DIR / "recent-cumulative.json"
    if not src.exists():
        print("❌ 缺少 recent-cumulative.json，先跑 build-recent-stats.py")
        return 1

    raw = json.loads(src.read_text(encoding="utf-8"))
    points = {}
    for r in raw:
        if not isinstance(r, dict) or "lat" not in r or "lng" not in r:
            continue
        try:
            k = (round(float(r["lat"]), 5), round(float(r["lng"]), 5))
        except (TypeError, ValueError):
            continue
        points.setdefault(k, k)
    pts = list(points.keys())
    print(f"=== 產出 elevation-map.json ===  去重觀測點：{len(pts)}")

    elev = lookup_elevations(pts)
    print(f"海拔命中：{len(elev)}/{len(pts)}")

    # 字串化 key 存檔
    out = {f"{lat},{lng}": round(e) for (lat, lng), e in elev.items()}
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"✅ 已寫入 {OUT_FILE.relative_to(PROJECT_ROOT)}（{len(out)} 點）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
