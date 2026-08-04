# 交接任務：eBird 台灣「今年首見鳥種」資料建置 + 新功能（已定案版）

> 自足(self-contained)執行任務，在「不會關機的機器 + Hermes Agent」上執行。
> 執行環境：Python 3、Hermes Agent（用其 cronjob 功能跑排程）、可連網、eBird API key。

---

## 0. 專案背景

現有 Web App「eBird 台灣鳥類查詢」（React + Vite，Vercel，GitHub: samtsai520/rare-bird，https://rare-bird.vercel.app）目前兩個 tab：「稀有種快報」「值得一看的鳥」。

本任務新增一個查詢，基於「**今年首見**」概念：
- **本月今年首見**：列出「本月」首次被觀察到、且是「今年首次見到」的鳥種。
- **最近兩日本月首見**：列出「昨天＋前天」首次被觀察到、且是「本月首次見到」的鳥種（**已決策：採延遲一天、不抓今天、零 live 請求**）。

核心基礎：建一張 **「speciesCode → 今年首見日期」** 的靜態 JSON 對照表 `first-seen.json`。

**年度化設計【決策 R3 — 每年從頭開始】**：
- `first-seen.json` 只代表**單一年度**，並以 `year` 欄位標記。
- 每年 1/1 跨年時，**整張表從零重建**（不沿用去年資料），重新逐日回填新年度的首見。
- 表結構（見 §1）含 `year` 欄位；前端與 cron 依 `year` 判斷是否已跨年、需否重建。

---

## 1. 資料架構（關鍵）

累計對照表 `first-seen.json`（含年度欄位）：

```json
{
  "year": 2026,
  "lastUpdated": "2026-08-03",
  "species": {
    "mouhae1": { "firstSeen": "2026-01-15", "comNameZh": "熊鷹", "comNameEn": "Mountain Hawk-Eagle" },
    "alpacc1": { "firstSeen": "2026-01-23", "comNameZh": "岩鷚", "comNameEn": "Alpine Accentor" }
  }
}
```

- **資料量固定**：台灣單年觀察約 520~550 種，每筆 ~50B，整表 **30~60 KB**。與查詢窗口無關，封頂 550 種。
- **兩個新功能都純讀表、0 API 請求 → 使用者永無 429。**

---

## 2. eBird API 事實（決定架構）

- `/recent` 的 `back` 上限 **30 天** → 查不了今年。
- `/product/spplist/TW` 一次回全部但**含往年、無日期**。
- `/historic/{y}/{m}/{d}` **一次一天**，配 `includeProvisional=true`，供逐日累積。
- **無任何 API 一次回「今年(YTD) + 首見日期」**；網站 bird-list `?yr=cur` 被 Anubis 擋。
- 結論：**今年首見只能靠 historic 逐日累加建表。**

正確 speciesCode（易錯）：熊鷹 `mouhae1`、赤腹山雀 `vartit3`（非 vartit1）、岩鷚 `alpacc1`、褐鷹鴞 `norboo1`（非 booboo1）、黑長尾雉 `mikphe1`。

---

## 3. 步驟 0（先做）：驗證 historic 完整性【決策 3A】

**在開始 216 天回填前，先驗證 historic 資料是否漏鳥**，否則整張表不可信。

1. 用少量請求抓 `historic/2026/8/3?includeProvisional=true`（全台，先前實測約 179 種）。
2. 與 eBird 網站「8/3 清單」比對鳥種數與內容。
3. 若一致 → historic 可信，進入回填。若少回報 → **停止**，改用「逐縣市 `r` 參數」或重新評估，不硬回填。

> 此步驟目的：避免「2 月看過的鳥因回填日資料不完整，被誤判成 8 月首見」的無聲錯誤。

---

## 4. 回填程序（一次性，約 216 天）

從今年 1/1 到「昨天」逐日抓 historic 累積成表。

### 請求
```
GET https://api.ebird.org/v2/data/obs/TW/historic/{y}/{m}/{d}?includeProvisional=true&detail=simple
Header: x-ebirdapitoken: <API_KEY 從環境變數讀取，勿寫死>
```

### 速率與穩定性
- **循序抓，每筆間隔 2~3 秒**，背景跑（216 天約 11~18 分鐘）。
- **每筆重試**：429/網路錯誤 → 等 `3s×(次+1)` 重試，最多 3 次；仍敗記為「此天失敗」。
- **斷點續傳**：每抓一天立即存 `progress.json`；重跑從「完成日下一天」續傳，**不從頭**。

### 合併（首見日取最早）
- `firstSeen = min(既有, 當天)`。補漏天然正確，不重複、不倒退。

### 回填後驗證
- 抽樣比對幾天的「historic 全台」vs「eBird 網站清單」確認一致。

---

## 5. 每日 cron 更新（用 Hermes cronjob）【決策 2】

### 排程
- 用 **Hermes cronjob** 每天跑一次（`no_agent=True`，直接跑 Python 腳本）。
- 排程時間選晚間（當天資料已齊）。

### API key 隱藏【決策 2 重點】
- **不寫死在腳本/不 commit 進 git**。
- 從**環境變數**（如 `EBIRD_API_KEY`）或 gitignored 設定檔讀取。
- 腳本 `~/.hermes/scripts/update-first-seen.py` 內 `os.environ["EBIRD_API_KEY"]` 讀取。
- 確認 `.gitignore` 排除含 key 的檔案。

### 跨年重建【決策 R3 — 每年從頭開始】
- cron 開跑時先比對 `first-seen.json` 的 `year` 與「今年」。
- 若 `year < 今年`（跨年）→ **整張表從零重建**：清空 `species`，`lastUpdated` 設為今年 1/1，`year=今年`，然後走下方補漏流程把今年 1/1..昨天全部補回。
- 例：2027/1/1 首次跑 → 偵測 `year=2026` → 重建為空表 → 回填 2027/1/1 起。
- 確保跨年後不會混到去年資料、首見日不會倒退。

### 補漏自癒
1. 讀 `first-seen.json` 的 `lastUpdated`（跨年時已重置為 1/1）。
2. 缺口 = `lastUpdated 明天` 到 `昨天` 缺哪幾天。
3. 逐日補抓缺口（斷點續傳 + 重試 + 間隔）。
4. `firstSeen = min(既有, 新找到)`；補完 `lastUpdated = 昨天`。
- 漏 5 天=5 請求、漏 30 天=30 請求，皆背景慢跑安全。

### 「今天」不抓（決策 1B）
- cron 只補到**昨天**；「今天」資料不抓、不進表。
- 功能二因此**零 live 請求、零 429**。

### cron 產物 → 部署
- 更新 `public/data/first-seen.json` → build → `npx vercel --prod`。

---

## 6. 兩個新功能的查詢邏輯（前端，全讀表、零請求）

| 功能 | 資料來源 | API 請求 | 429 風險 |
|------|---------|---------|---------|
| 本月今年首見 | 全讀 `first-seen.json` | **0** | **零** |
| 最近兩日（昨天+前天）本月首見 | 全讀 `first-seen.json` | **0** | **零** |

### 功能一「本月今年首見」
- 篩 `firstSeen 月份 == 本月` → 顯示鳥種 + 首見日期 + 地點。

### 功能二「最近兩日本月首見」
- 篩 `firstSeen ∈ {昨天, 前天}` 且 `firstSeen 月份 == 本月` → 顯示。
- **因決策 1B，完全讀表，不發任何 live 請求。**

---

## 7. 前端整合（新增第 3 個 tab）【決策 4A】

- **新增第 3 個 tab**：現有「稀有種快報」「值得一看的鳥」之外，新增「**今年首見**」。
- 新 tab 內提供「本月今年首見」與「最近兩日本月首見」兩個檢視（切換或分區）。
- 沿用現有 UI：中文名+英文名、地點 GPS 連結、accordion 展開收合、本島/外島分區。
- 讀 `public/data/first-seen.json` 靜態檔。

---

## 8. 風險清單與緩解

|| 風險 | 緩解 |
||------|------|
|| 回填 216 請求被 429/中斷 | 循序 + 2~3s + 重試 + **斷點續傳** |
|| historic 漏鳥 → 首見日誤判 | **步驟 0 先驗證完整性**（決策 3A）；用 `scripts/verify_historic.py` |
|| cron 停更 → 表落後 | Hermes cronjob + 補漏自癒；漏跑後重跑自癒 |
|| API key 外洩 | **環境變數/config.json 讀取，不寫死不 commit**（決策 2，已修正） |
|| 跨年後沿用去年資料 | **決策 R3**：`year` 欄位判斷，跨年整表重建、每年從頭 |
|| 功能二 live 429 | 決策 1B 已消除：**零 live 請求** |
|| speciesCode 記錯 | 用第 2 節正確 code |

---

## 9. 驗收標準

- `first-seen.json` 存在、含 `year` 欄位，且 `lastUpdated` = 昨天。
- 功能一、功能二都能列出對應鳥種，與 eBird 網站「本月最早觀察」比對一致。
- **前端兩功能皆零 API 請求**（可於 DevTools Network 確認無 eBird 呼叫）。
- Hermes cronjob 每天正常補漏；漏跑後重跑能自癒補齊。
- 跨年後（year 改變）表能整張重建、從新年 1/1 重新累積。
- git 中無任何 API key 字串（`fetch_birds.py` 無寫死，`config.json` 已 gitignore）。
