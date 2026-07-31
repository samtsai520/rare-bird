import React, { useState, useEffect } from 'react';
import { 
  Compass, 
  MapPin, 
  Calendar, 
  RotateCw, 
  Navigation, 
  Moon, 
  Sun, 
  AlertTriangle,
  CheckCircle,
  Hash,
  ChevronDown,
  ChevronUp,
  Settings,
  Key
} from 'lucide-react';

const STORAGE_API_KEY_KEY = "taiwan_birds_ebird_api_key";
const STORAGE_OBS_KEY = "taiwan_birds_obs_cache";
const STORAGE_DAYS_KEY = "taiwan_birds_days_setting";
const STORAGE_TIME_KEY = "taiwan_birds_last_fetch_time";
const STORAGE_THEME_KEY = "taiwan_birds_theme";

export default function App() {
  const [days, setDays] = useState(() => {
    return Number(localStorage.getItem(STORAGE_DAYS_KEY)) || 7;
  });
  
  // Load API Key from localStorage
  const [apiKey, setApiKey] = useState(() => {
    return localStorage.getItem(STORAGE_API_KEY_KEY) || '';
  });
  const [inputApiKey, setInputApiKey] = useState(apiKey);
  const [apiKeySavedMsg, setApiKeySavedMsg] = useState('');

  const [observations, setObservations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem(STORAGE_THEME_KEY) || 'dark';
  });
  const [lastUpdated, setLastUpdated] = useState(() => {
    return localStorage.getItem(STORAGE_TIME_KEY) || null;
  });
  
  // Accordion state: Set of expanded speciesCodes
  const [expandedSpecies, setExpandedSpecies] = useState(new Set());
  const [showSettings, setShowSettings] = useState(false);

  // Initialize Theme
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_THEME_KEY, theme);
  }, [theme]);

  // Register Service Worker for offline asset caching
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', async () => {
        try {
          const registration = await navigator.serviceWorker.register('/sw.js');
          console.log('[App] ServiceWorker registered successfully: ', registration.scope);
        } catch (err) {
          console.error('[App] ServiceWorker registration failed: ', err);
        }
      });
    }
  }, []);

  // Main API Fetch function
  const fetchObservations = async (selectedDays, isManualSearch = false, activeKey = apiKey) => {
    if (!activeKey) {
      setError("請先設定您的 eBird API Key。");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    
    try {
      const urlEn = `https://api.ebird.org/v2/data/obs/TW/recent/notable?back=${selectedDays}&detail=simple`;
      const urlZh = `https://api.ebird.org/v2/data/obs/TW/recent/notable?back=${selectedDays}&detail=simple&sppLocale=zh`;
      const headers = { 'x-ebirdapitoken': activeKey };

      const [resEn, resZh] = await Promise.all([
        fetch(urlEn, { headers }),
        fetch(urlZh, { headers })
      ]);

      if (!resEn.ok || !resZh.ok) {
        throw new Error(`eBird API returned error status: ${resEn.status} / ${resZh.status}`);
      }

      const dataEn = await resEn.ok ? await resEn.json() : [];
      const dataZh = await resZh.ok ? await resZh.json() : [];

      // Create a map of speciesCode -> Chinese Name
      const zhNamesMap = {};
      dataZh.forEach(item => {
        if (item.speciesCode) {
          zhNamesMap[item.speciesCode] = item.comName;
        }
      });

      // Merge Chinese names into English results
      const mergedList = dataEn.map(item => ({
        ...item,
        comNameZh: zhNamesMap[item.speciesCode] || item.comName,
        comNameEn: item.comName
      }));

      setObservations(mergedList);
      
      const nowStr = new Date().toISOString();
      setLastUpdated(nowStr);
      
      // Save to localStorage
      localStorage.setItem(STORAGE_OBS_KEY, JSON.stringify(mergedList));
      localStorage.setItem(STORAGE_TIME_KEY, nowStr);
      localStorage.setItem(STORAGE_DAYS_KEY, selectedDays.toString());

    } catch (err) {
      console.error("Fetch observations failed:", err);
      setError("無法取得觀測資料，請確認您的 eBird API Key 是否正確且有效。");
    } finally {
      setLoading(false);
    }
  };

  // Caching & Auto-fetch on component mount or API key change (once a day cache logic)
  useEffect(() => {
    if (!apiKey) {
      setObservations([]);
      return;
    }

    const cachedObs = localStorage.getItem(STORAGE_OBS_KEY);
    const cachedTime = localStorage.getItem(STORAGE_TIME_KEY);
    const cachedDays = localStorage.getItem(STORAGE_DAYS_KEY);

    let needsFetch = true;

    if (cachedObs && cachedTime && cachedDays === days.toString()) {
      const parsedTime = new Date(cachedTime);
      const today = new Date();
      
      // If cached on the same calendar day, use cached data
      if (
        parsedTime.getFullYear() === today.getFullYear() &&
        parsedTime.getMonth() === today.getMonth() &&
        parsedTime.getDate() === today.getDate()
      ) {
        const parsedObs = JSON.parse(cachedObs);
        setObservations(parsedObs);
        needsFetch = false;
      }
    }

    if (needsFetch) {
      fetchObservations(days, false, apiKey);
    }
  }, [days, apiKey]);

  // Handle manual Search trigger
  const handleSearchClick = () => {
    if (!apiKey) {
      setShowSettings(true);
      return;
    }
    fetchObservations(days, true, apiKey);
  };

  // Save API Key Function
  const saveApiKey = (keyToSave = inputApiKey) => {
    const trimmed = keyToSave.trim();
    setApiKey(trimmed);
    localStorage.setItem(STORAGE_API_KEY_KEY, trimmed);
    setApiKeySavedMsg("API Key 儲存成功！正在獲取觀測資料...");
    setTimeout(() => setApiKeySavedMsg(''), 3000);
    if (trimmed) {
      fetchObservations(days, true, trimmed);
    }
  };

  // Clear API Key Function
  const clearApiKey = () => {
    setApiKey('');
    setInputApiKey('');
    setObservations([]);
    localStorage.removeItem(STORAGE_API_KEY_KEY);
    localStorage.removeItem(STORAGE_OBS_KEY);
    localStorage.removeItem(STORAGE_TIME_KEY);
    setApiKeySavedMsg("API Key 已清除。觀測功能已停用。");
    setTimeout(() => setApiKeySavedMsg(''), 3000);
  };

  // Accordion Toggle
  const toggleSpecies = (speciesCode) => {
    const nextExpanded = new Set(expandedSpecies);
    if (nextExpanded.has(speciesCode)) {
      nextExpanded.delete(speciesCode);
    } else {
      nextExpanded.add(speciesCode);
    }
    setExpandedSpecies(nextExpanded);
  };

  // Group observations by species
  const getGroupedSpeciesList = () => {
    const grouped = {};
    observations.forEach(obs => {
      const code = obs.speciesCode;
      if (!grouped[code]) {
        grouped[code] = {
          speciesCode: obs.speciesCode,
          comNameZh: obs.comNameZh,
          comNameEn: obs.comNameEn,
          sciName: obs.sciName,
          sightings: []
        };
      }
      grouped[code].sightings.push(obs);
    });

    const groupedArray = Object.values(grouped);

    // Sort each species' sightings: observation date descending (most recent first)
    groupedArray.forEach(sp => {
      sp.sightings.sort((a, b) => {
        return new Date(b.obsDt) - new Date(a.obsDt);
      });
    });

    // Sort species by the most recent observation date among their sightings
    groupedArray.sort((a, b) => {
      const aLatest = new Date(a.sightings[0].obsDt);
      const bLatest = new Date(b.sightings[0].obsDt);
      return bLatest - aLatest;
    });

    return groupedArray;
  };

  const formatTime = (isoString) => {
    if (!isoString) return '無紀錄';
    const date = new Date(isoString);
    return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  };

  const cleanLocationName = (locName) => {
    if (!locName) return '';
    
    // Remove coordinates like (24.1234, 121.5678)
    let clean = locName.replace(/\s*\([\d.-]+,\s*[\d.-]+\)/g, '');
    clean = clean.replace(/\s*\([\d.-]+,[\d.-]+\)/g, '');
    
    // Check if there are Chinese characters
    const hasChinese = /[\u4e00-\u9fa5]/.test(clean);
    if (!hasChinese) {
      return clean.trim();
    }

    // Try to extract Chinese from parentheses e.g. "Hsinchu City--Xiangshan (新竹市香山)"
    const parenMatches = clean.match(/\(([^)]*[\u4e00-\u9fa5][^)]*)\)/);
    if (parenMatches && parenMatches[1]) {
      return parenMatches[1].trim();
    }
    
    // Remove English in parentheses, e.g. "關渡 (Guandu)"
    clean = clean.replace(/\([^)]*[a-zA-Z]{2,}[^)]*\)/g, '');

    // Split by common separators like "/", "--"
    const parts = clean.split(/\s*(?:\/|--)\s*/);
    for (let part of parts) {
      if (/[\u4e00-\u9fa5]/.test(part)) {
        const chineseMatch = part.match(/[\u4e00-\u9fa5]+/g);
        if (chineseMatch) {
          return chineseMatch.join('');
        }
        return part.trim();
      }
    }

    // Extract all contiguous Chinese characters
    const chineseBlocks = clean.match(/[\u4e00-\u9fa5]+/g);
    if (chineseBlocks) {
      return chineseBlocks.join('');
    }

    return clean.trim();
  };

  const groupedSpecies = getGroupedSpeciesList();

  return (
    <div className="app-container">
      {/* Header Section */}
      <header className="glass-panel app-header">
        <div className="brand-wrapper">
          <div className="logo-icon">
            <Compass size={28} />
          </div>
          <div className="title-section">
            <div className="title-row-container">
              <h1>eBird罕見鳥類查詢</h1>
            </div>
            <p>eBird 2.0 即時資料庫同步</p>
          </div>
        </div>

        <div className="header-actions">
          {/* Settings Toggle */}
          <button 
            className={`btn-icon ${showSettings ? 'active-btn' : ''}`}
            onClick={() => setShowSettings(!showSettings)}
            title="設定 API Key"
          >
            <Settings size={20} />
          </button>
          
          {/* Light/Dark Toggle */}
          <button 
            className="btn-icon" 
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="切換主題"
          >
            {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
          </button>
        </div>
      </header>

      {/* Settings Panel */}
      {showSettings && (
        <section className="glass-panel settings-panel">
          {/* API Key Configuration */}
          <div className="settings-section">
            <div className="panel-title-row">
              <Key size={18} style={{ color: 'var(--accent-secondary)' }} />
              <h3>eBird API 金鑰設定</h3>
            </div>
            <p className="panel-desc">
              請輸入您的個人 eBird API Key。您可以在{' '}
              <a 
                href="https://ebird.org/api/keygen" 
                target="_blank" 
                rel="noopener noreferrer" 
                className="inline-link"
              >
                eBird 官方網站申請金鑰
              </a>
              （需註冊 eBird 免費帳號）。
            </p>
            
            <div className="api-key-input-group">
              <input 
                type="password" 
                placeholder="請貼上您的 eBird API Key..."
                value={inputApiKey}
                onChange={(e) => setInputApiKey(e.target.value)}
                className="api-key-input"
              />
              <button 
                className="btn-save-api-key" 
                onClick={() => saveApiKey()}
              >
                儲存金鑰
              </button>
              {apiKey && (
                <button 
                  className="btn-clear-api-key" 
                  onClick={clearApiKey}
                  title="從本地快取移除金鑰並清空資料"
                >
                  清除金鑰
                </button>
              )}
            </div>
            
            {apiKeySavedMsg && (
              <div className="save-success-msg">
                <CheckCircle size={14} style={{ color: 'var(--accent-primary)' }} />
                <span>{apiKeySavedMsg}</span>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Warning State if API Key is missing */}
      {!apiKey ? (
        <section className="glass-panel warning-overlay-panel">
          <div className="warning-content">
            <AlertTriangle size={54} className="pulse-warning" style={{ color: 'var(--accent-warning)' }} />
            <h2>需要 eBird API 金鑰</h2>
            <p>
              為了載入台灣罕見鳥類觀測清單，請先點擊右上方設定圖示（<Settings size={18} style={{ display: 'inline', verticalAlign: 'middle', margin: '0 0.2rem' }} />）輸入您的個人 eBird API Key。
            </p>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '-0.5rem' }}>
              金鑰申請完全免費，並只會安全地儲存在您目前的瀏覽器本地快取中。
            </p>
          </div>
        </section>
      ) : (
        <>
          {/* Control Panel Section */}
          <section className="glass-panel">
            <div className="controls-grid">
              <div className="select-container">
                <label htmlFor="date-select" className="select-label">觀測日期區間</label>
                <select 
                  id="date-select" 
                  value={days} 
                  onChange={(e) => setDays(Number(e.target.value))}
                  className="custom-select"
                >
                  <option value="1">過去 1 天</option>
                  <option value="3">過去 3 天</option>
                  <option value="5">過去 5 天</option>
                  <option value="7">過去 7 天 (預設)</option>
                  <option value="10">過去 10 天</option>
                  <option value="14">過去 14 天</option>
                  <option value="21">過去 21 天</option>
                  <option value="30">過去 30 天</option>
                </select>
              </div>

              <button 
                className="btn-primary" 
                onClick={handleSearchClick}
                disabled={loading}
              >
                <RotateCw size={18} className={loading ? "spin" : ""} />
                <span>{loading ? "更新中..." : "更新鳥類名單"}</span>
              </button>
            </div>
          </section>

          {/* Statistics Counter */}
          <div className="stats-ribbon">
            <div className="stats-text">
              在過去 <span className="stats-count">{days}</span> 天內，共觀測到 
              <span className="stats-count">{groupedSpecies.length}</span> 種罕見鳥類（共 {observations.length} 筆觀測紀錄）。
            </div>
            <div className="status-indicator">
              <span className="pulse-dot"></span>
              <span>最後更新: {formatTime(lastUpdated)}</span>
            </div>
          </div>

          {/* Collapsible Accordion List Section */}
          {loading && observations.length === 0 ? (
            <div className="loading-state">
              <div className="radar-loader">
                <div className="radar-circle"></div>
                <div className="radar-circle"></div>
                <div className="radar-circle"></div>
                <div className="radar-center"></div>
              </div>
              <p>正在搜尋並整合台灣地區的最新鳥況紀錄...</p>
            </div>
          ) : error ? (
            <div className="error-state">
              <AlertTriangle size={48} style={{ color: '#ef4444' }} />
              <h3>載入失敗</h3>
              <p>{error}</p>
              <button className="btn-primary" onClick={handleSearchClick}>重試</button>
            </div>
          ) : groupedSpecies.length === 0 ? (
            <div className="empty-state">
              <Compass size={48} style={{ color: 'var(--text-muted)' }} />
              <h3>查無罕見鳥類觀測紀錄</h3>
              <p>在過去 {days} 天內，eBird 系統尚未在台灣地區登錄任何核實或待審的罕見鳥種。</p>
            </div>
          ) : (
            <main className="species-list-container">
              {groupedSpecies.map((sp) => {
                const isExpanded = expandedSpecies.has(sp.speciesCode);
                
                return (
                  <div 
                    className={`species-accordion-item ${isExpanded ? 'is-open' : ''}`} 
                    key={sp.speciesCode}
                  >
                    {/* Accordion Trigger Header */}
                    <div 
                      className="species-accordion-header"
                      onClick={() => toggleSpecies(sp.speciesCode)}
                    >
                      <div className="species-primary-info">
                        <div className="species-name-row">
                          <span className="species-chinese">{sp.comNameZh}</span>
                          <span className="species-english">{sp.comNameEn}</span>
                          <span className="species-scientific">({sp.sciName})</span>
                        </div>
                        
                        <div className="species-subtitle-row">
                          <span className="species-latest-date">
                            {sp.sightings[0].obsDt}
                          </span>
                          <span className="species-latest-separator">·</span>
                          <span className="species-latest-loc" title={sp.sightings[0].locName}>
                            {cleanLocationName(sp.sightings[0].locName)}
                          </span>
                        </div>
                      </div>
                      
                      <div className="species-meta-info">
                        <span className="sightings-counter-pill">
                          {sp.sightings.length} 次觀測點
                        </span>
                        <button className="accordion-arrow-btn">
                          {isExpanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
                        </button>
                      </div>
                    </div>

                    {/* Collapsible Sighting Details */}
                    {isExpanded && (
                      <div className="species-accordion-content">
                        <div className="sightings-table-container">
                          <table className="sightings-table">
                            <thead>
                              <tr>
                                <th>觀測日期</th>
                                <th>發現地點 (點擊導航)</th>
                                <th style={{ textAlign: 'right' }}>數量</th>
                              </tr>
                            </thead>
                            <tbody>
                              {sp.sightings.map((sighting, sIdx) => {
                                const mapUrl = `https://www.google.com/maps/dir/?api=1&destination=${sighting.lat},${sighting.lng}`;
                                return (
                                  <tr key={`${sighting.subId}-${sIdx}`}>
                                    <td className="td-date">
                                      <Calendar size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle', color: 'var(--text-muted)' }} />
                                      <span>{sighting.obsDt}</span>
                                    </td>
                                    <td className="td-location" title={sighting.locName}>
                                      <a 
                                        href={mapUrl}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="location-link"
                                      >
                                        <MapPin size={14} className="location-nav-icon" />
                                        <span className="location-name-text">{sighting.locName}</span>
                                      </a>
                                    </td>
                                    <td className="td-count" style={{ textAlign: 'right' }}>
                                      <span className="count-tag">
                                        {sighting.howMany ? `${sighting.howMany} 隻` : '出現'}
                                      </span>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </main>
          )}
        </>
      )}

      {/* Inline styles for loading / spin animations */}
      <style>{`
        .spin {
          animation: spin-anim 1s linear infinite;
        }
        @keyframes spin-anim {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .pulse-warning {
          animation: warning-pulse 2s infinite;
        }
        @keyframes warning-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.08); opacity: 0.8; }
        }
      `}</style>
    </div>
  );
}
