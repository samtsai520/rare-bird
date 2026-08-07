#!/usr/bin/env python3
"""
build-worth-diff.py — 產出「本月精彩推薦」5日差分靜態清單

由每日清晨 cron 呼叫。輸出 public/data/worth-diff.json：

邏輯：
  1. 抓最近 5 天 historic（昨天~5天前）= 近期窗口
  2. 抓前 5 天 historic（6天前~10天前）= 基線窗口
  3. 差分 = 近期窗口鳥種 - 基線窗口鳥種 - 黑名單
  4. 收集差分鳥種在 5 天內的所有觀測紀錄
  5. 存成靜態 JSON，前端純讀 + 1 次 recent?back=1 補今天

安全性：API key 從環境變數 EBIRD_API_KEY 或 gitignored config.json 讀取。
用法：
  python3 scripts/build-worth-diff.py
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
OUT_FILE = DATA_DIR / "worth-diff.json"
BASE_URL = "https://api.ebird.org/v2"
MAX_RETRIES = 3
REQUEST_DELAY = 2.5
RECENT_DAYS = 5   # 近期窗口：昨天 ~ 5天前
BASELINE_DAYS = 5  # 基線窗口：6天前 ~ 10天前


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
    """抓單日全台 historic 觀察（detail=full），含重試。"""
    y, m, d = date_str.split("-")
    url = (
        f"{BASE_URL}/data/obs/TW/historic/{y}/{int(m)}/{int(d)}"
        f"?includeProvisional=true&detail=full"
    )
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


def main():
    key = _load_api_key()
    today = datetime.now()
    yesterday = today - timedelta(days=1)

    # 近期窗口：昨天 ~ 5天前
    recent_dates = [
        (yesterday - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(RECENT_DAYS)
    ]
    # 基線窗口：6天前 ~ 10天前
    baseline_dates = [
        (yesterday - timedelta(days=RECENT_DAYS + i)).strftime("%Y-%m-%d")
        for i in range(1, BASELINE_DAYS + 1)
    ]

    print("=== 產出 worth-diff.json ===")
    print(f"近期窗口：{recent_dates[-1]} ~ {recent_dates[0]}")
    print(f"基線窗口：{baseline_dates[-1]} ~ {baseline_dates[0]}")

    # 抓近期窗口（保留完整紀錄以收集 sighting 細節）
    recent_records = []
    for d in recent_dates:
        data = fetch_historic(key, d)
        if data:
            recent_records.extend(data)
            codes = set(r.get("speciesCode") for r in data if r.get("speciesCode"))
            print(f"  {d}: {len(data)} 筆, {len(codes)} 種")
        time.sleep(REQUEST_DELAY)

    # 抓基線窗口（只需鳥種集合）
    baseline_species = set()
    for d in baseline_dates:
        data = fetch_historic(key, d)
        if data:
            for r in data:
                if r.get("speciesCode"):
                    baseline_species.add(r["speciesCode"])
            print(f"  {d}: {len(data)} 筆, {len(set(r.get('speciesCode') for r in data if r.get('speciesCode')))} 種")
        time.sleep(REQUEST_DELAY)

    # 載入黑名單
    blacklist = set()
    bl_file = DATA_DIR / "blacklist.json"
    if bl_file.exists():
        bl_data = json.loads(bl_file.read_text(encoding="utf-8"))
        blacklist = set(bl_data.get("codes", []))

    # 載入鳥名
    birds = {}
    birds_file = DATA_DIR / "taiwan-birds.json"
    if birds_file.exists():
        birds_data = json.loads(birds_file.read_text(encoding="utf-8"))
        birds = birds_data.get("species", {})

    # 近期窗口鳥種
    recent_species = set(r["speciesCode"] for r in recent_records if r.get("speciesCode"))

    # 差分：在近期窗口但不在基線窗口，且不在黑名單
    diff_codes = recent_species - baseline_species - blacklist

    print(f"\n近期窗口鳥種：{len(recent_species)}")
    print(f"基線窗口鳥種：{len(baseline_species)}")
    print(f"差分（新增，排除黑名單）：{len(diff_codes)}")

    # 收集差分鳥種的觀測紀錄
    by_code = {}
    for r in recent_records:
        code = r.get("speciesCode")
        if code not in diff_codes:
            continue
        if code not in by_code:
            info = birds.get(code, {})
            by_code[code] = {
                "speciesCode": code,
                "comNameZh": info.get("comNameZh", r.get("comName", "")),
                "comNameEn": info.get("comNameEn", r.get("comName", "")),
                "sightings": [],
            }
        by_code[code]["sightings"].append({
            "subId": r.get("subId", ""),
            "obsDt": r.get("obsDt", ""),
            "locName": r.get("locName", ""),
            "lat": r.get("lat"),
            "lng": r.get("lng"),
            "howMany": r.get("howMany"),
        })

    species_list = list(by_code.values())
    # 每種鳥的 sightings 依日期降序
    for s in species_list:
        s["sightings"].sort(key=lambda x: x["obsDt"], reverse=True)

    # 種間依緯度北→南排序
    species_list.sort(key=lambda s: (
        -(s["sightings"][0]["lat"] or 0) if s["sightings"] else 0,
        s["sightings"][0]["obsDt"] if s["sightings"] else ""
    ), reverse=False)
    # 緯度降序（北先），日期降序為次要
    species_list.sort(
        key=lambda s: (
            -(s["sightings"][0]["lat"] if s["sightings"] and s["sightings"][0]["lat"] else -999),
        )
    )

    out = {
        "date": yesterday.strftime("%Y-%m-%d"),
        "recentWindow": {"start": recent_dates[-1], "end": recent_dates[0]},
        "baselineWindow": {"start": baseline_dates[-1], "end": baseline_dates[0]},
        "speciesCount": len(species_list),
        "species": species_list,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 已寫入 {OUT_FILE.relative_to(PROJECT_ROOT)}，共 {len(species_list)} 種")


if __name__ == "__main__":
    main()