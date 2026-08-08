#!/usr/bin/env python3
"""
update-first-seen.py — 建置與更新「今年首見」對照表 first-seen.json

依 HANDOFF-first-seen.md（決策 1B / 2 / 3A / R3）實作：

1. 首次執行：從今年 1/1 到「昨天」逐日抓 historic，累積最早首見日期。
2. 每日增量：讀 lastUpdated，補抓缺口（lastUpdated 明天 .. 昨天），自癒。
3. 跨年重建（R3）：偵測 year < 今年 → 整表從零重建，重新回填。
4. 「今天」不抓（決策 1B）：只補到昨天，零 live 請求需求。

安全性：API key 從環境變數 EBIRD_API_KEY 或 gitignored config.json 讀取，
絕不寫死在程式碼。

用法：
  python3 scripts/update-first-seen.py            # 正常執行（回填/增量/跨年）
  python3 scripts/update-first-seen.py --dry-run  # 只印將做的事，不發 API

產出：public/data/first-seen.json
"""
import argparse
import calendar
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIRST_SEEN_FILE = PROJECT_ROOT / "public" / "data" / "first-seen.json"
REQUEST_DELAY = 2.5  # 每筆間隔秒數
MAX_RETRIES = 3


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
    raise RuntimeError(
        "找不到 eBird API key。請設定環境變數 EBIRD_API_KEY，"
        "或建立 gitignored 的 config.json。"
    )


BASE_URL = "https://api.ebird.org/v2"

# 官方 taxonomy 分類對照（code -> category: species/hybrid/issf/form/domestic...）
_CATEGORY_MAP = None


def _load_category_map():
    """載入官方 eBird taxonomy 分類對照表（一次載入，供過濾雜交種）。"""
    global _CATEGORY_MAP
    if _CATEGORY_MAP is not None:
        return _CATEGORY_MAP
    p = PROJECT_ROOT / "public" / "data" / "ebird-category-map.json"
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                _CATEGORY_MAP = json.load(f)
        except Exception:
            _CATEGORY_MAP = {}
    else:
        _CATEGORY_MAP = {}
    return _CATEGORY_MAP


_BLACKLIST = None


def _load_blacklist():
    """載入黑名單（常見鳥 + 外來逸鳥），回傳 set of speciesCode。"""
    global _BLACKLIST
    if _BLACKLIST is not None:
        return _BLACKLIST
    p = PROJECT_ROOT / "public" / "data" / "blacklist.json"
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                _BLACKLIST = set(json.load(f).get("codes", []))
        except Exception:
            _BLACKLIST = set()
    else:
        _BLACKLIST = set()
    return _BLACKLIST


def api_fetch(url, key, dry_run=False):
    if dry_run:
        return None
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-ebirdapitoken": key})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if "429" in str(e) and attempt < MAX_RETRIES:
                wait = 3 * (attempt + 1)
                print(f"  429 限速，等待 {wait}s...", flush=True)
                time.sleep(wait)
                continue
            print(f"  ERROR: {e}", flush=True)
            return None
    return None


def load_first_seen():
    if FIRST_SEEN_FILE.exists():
        try:
            with open(FIRST_SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"year": None, "lastUpdated": None, "species": {}}


def save_first_seen(data):
    FIRST_SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FIRST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    size_kb = FIRST_SEEN_FILE.stat().st_size / 1024
    print(f"  ✅ 已存 {FIRST_SEEN_FILE.name}: {len(data['species'])} 種 ({size_kb:.1f} KB)")


def iter_dates(start, end):
    """Yield datetime.date from start..end inclusive."""
    import datetime
    cur = start
    while cur <= end:
        yield cur
        cur += datetime.timedelta(days=1)


_ONE_DAY = __import__("datetime").timedelta(days=1)


def _load_bird_names():
    """Load Taiwan bird names from static taiwan-birds.json (777 species).

    Returns flat map {speciesCode: {comNameZh, comNameEn, sciName}}.
    """
    tax = PROJECT_ROOT / "public" / "data" / "taiwan-birds.json"
    if not tax.exists():
        print("  ERROR: 找不到 taiwan-birds.json（請先執行 build_taiwan_birds.py）", flush=True)
        return {}
    try:
        with open(tax, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ERROR 讀取 taiwan-birds.json: {e}", flush=True)
        return {}
    species = data.get("species", {})
    out = {}
    for code, info in species.items():
        out[code] = {
            "comNameZh": info.get("comNameZh", ""),
            "comNameEn": info.get("comNameEn", ""),
            "sciName": info.get("sciName", ""),
        }
    return out


def fetch_day(y, m, d, key, dry_run=False):
    """Fetch all species for a single day, return dict code->{firstSeen, comNameEn, locations, obsReviewed}.

    過濾規則（決策 2026-08-04 + 2026-08-08 更新）：
    - 排除非有效觀察：只保留 obsValid=true 的記錄。
    - 排除雜交種：官方 taxonomy 分類為 hybrid 的鳥種不予累積。
    - 排除黑名單：blacklist.json 中的鳥種（常見鳥 + 外來逸鳥）。
    - detail=full：取得 locName, lat, lng, obsReviewed 欄位。
    """
    url = (
        f"{BASE_URL}/data/obs/TW/historic/{y}/{m}/{d}"
        f"?includeProvisional=true&detail=full"
    )
    data = api_fetch(url, key, dry_run=dry_run)
    if data is None:
        return None
    cat_map = _load_category_map()
    blacklist = _load_blacklist()
    day_str = f"{y:04d}-{m:02d}-{d:02d}"
    out = {}
    skipped_invalid = 0
    skipped_hybrid = 0
    skipped_blacklist = 0
    for x in data:
        code = x.get("speciesCode")
        if not code:
            continue
        # 過濾 1: 非有效觀察
        if not x.get("obsValid"):
            skipped_invalid += 1
            continue
        # 過濾 2: 雜交種（官方分類）
        if cat_map.get(code) == "hybrid":
            skipped_hybrid += 1
            continue
        # 過濾 3: 黑名單
        if code in blacklist:
            skipped_blacklist += 1
            continue
        loc = {
            "locName": x.get("locName", ""),
            "lat": x.get("lat"),
            "lng": x.get("lng"),
            "obsReviewed": x.get("obsReviewed", False),
        }
        if code not in out:
            out[code] = {
                "firstSeen": day_str,
                "comNameEn": x.get("comName", ""),
                "locations": [loc],
            }
        else:
            # 同一天同一種鳥多筆觀測，加到 locations
            out[code]["locations"].append(loc)
    if skipped_invalid or skipped_hybrid or skipped_blacklist:
        print(f"    (過濾 非有效={skipped_invalid}, 雜交={skipped_hybrid}, 黑名單={skipped_blacklist})", flush=True)
    return out


def merge_day(species, day_map, day_str, bird_names):
    """Merge a day's species into table; firstSeen = min(existing, day)."""
    added = 0
    for code, info in day_map.items():
        if code not in species:
            tax = bird_names.get(code, {})
            species[code] = {
                "firstSeen": info["firstSeen"],
                "comNameZh": tax.get("comNameZh", ""),
                "comNameEn": info.get("comNameEn") or tax.get("comNameEn", ""),
                "locations": info.get("locations", []),
            }
            added += 1
        else:
            # Backfill names if missing; never change firstSeen if existing is earlier
            if not species[code].get("comNameEn") and info.get("comNameEn"):
                species[code]["comNameEn"] = info["comNameEn"]
            if not species[code].get("comNameZh"):
                tax = bird_names.get(code, {})
                if tax.get("comNameZh"):
                    species[code]["comNameZh"] = tax["comNameZh"]
            if day_str < species[code]["firstSeen"]:
                species[code]["firstSeen"] = day_str
                # 首見日期更新為更早的一天，locations 用更早那天的
                species[code]["locations"] = info.get("locations", [])
            elif day_str == species[code]["firstSeen"]:
                # 同一天的額外觀測點，合併進 locations
                species[code]["locations"].extend(info.get("locations", []))
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只印計劃，不發 API")
    args = ap.parse_args()

    key = _load_api_key()
    if args.dry_run:
        key = None

    import datetime
    today = datetime.date.today()
    # 決策 1B：只補到「昨天」
    yesterday = today - _ONE_DAY

    data = load_first_seen()
    current_year = today.year
    species = data.get("species", {})
    bird_names = _load_bird_names()
    print(f"已載入 bird_names：{len(bird_names)} 種（用於中文名補齊）", flush=True)

    # ---- 跨年重建（R3）：每年從頭開始 ----
    # 也檢查格式：如果 species 中沒有 locations 欄位（舊格式），需重建
    needs_rebuild = False
    if data.get("year") is not None and data["year"] < current_year:
        print(f"🔄 偵測跨年（{data['year']} -> {current_year}），整表從零重建", flush=True)
        needs_rebuild = True
    elif species:
        # 檢查第一個物種是否有 locations 欄位
        first_code = next(iter(species), None)
        if first_code and "locations" not in species[first_code]:
            print(f"🔄 偵測舊格式（無 locations 欄位），整表從零重建", flush=True)
            needs_rebuild = True

    if needs_rebuild:
        species = {}
        data = {"year": None, "lastUpdated": None, "species": {}}

    # ---- 確定回填範圍 ----
    if data.get("year") != current_year:
        # 首次執行或跨年重建後：從今年 1/1 回填到昨天
        start = datetime.date(current_year, 1, 1)
        print(f"🆕 首建/重建：回填 {current_year}/1/1 .. {yesterday}", flush=True)
        if args.dry_run:
            print(f"  （dry-run）會逐日抓取約 {(yesterday - start).days + 1} 天", flush=True)
    else:
        # 增量：lastUpdated 的明天 .. 昨天
        if data.get("lastUpdated"):
            last_dt = datetime.date.fromisoformat(data["lastUpdated"])
            start = last_dt + _ONE_DAY
            print(f"➡️  增量更新：{start} .. {yesterday}", flush=True)
            if args.dry_run:
                print(f"  （dry-run）會補抓約 {(yesterday - start).days + 1} 天缺口", flush=True)
        else:
            start = datetime.date(current_year, 1, 1)

    if start > yesterday:
        print("✅ 表已是最新（lastUpdated 已達昨天），無需更新", flush=True)
        return

    # ---- 逐日回填 ----
    dates = list(iter_dates(start, yesterday))
    for i, d in enumerate(dates):
        day_map = fetch_day(d.year, d.month, d.day, key, dry_run=args.dry_run)
        if day_map is None:
            print(f"  {d}  抓取失敗 ✗（保留為缺口，下次自癒補回）", flush=True)
            continue
        day_str = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
        added = merge_day(species, day_map, day_str, bird_names)
        print(
            f"  [{i+1}/{len(dates)}] {day_str}  新增 {added:>3}  累積 {len(species):>3}",
            flush=True,
        )
        if not args.dry_run:
            time.sleep(REQUEST_DELAY)

    # ---- 存檔 ----
    data["year"] = current_year
    data["lastUpdated"] = yesterday.isoformat()
    data["species"] = species
    if not args.dry_run:
        save_first_seen(data)
    else:
        print(f"\n（dry-run）預期累積鳥種數 ≈ {len(species)}", flush=True)

    print(f"\n🎉 完成。first-seen.json lastUpdated = {yesterday}", flush=True)


if __name__ == "__main__":
    main()
