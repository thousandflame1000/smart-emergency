# 災害資源配置 / 人道物流 學術與實務文獻研究報告

**目的**：為「鄰里守望平台」（Neighborhood Watch Platform）現行的 `app/services/dispatch.py` 優先權貪婪排程（priority-based greedy scheduler：urgency + vulnerability + type affinity − distance penalty − load penalty，貪婪指派＋依 urgency/vulnerability/FIFO 排序）尋找有實證依據的學術與業界基礎，而不是憑空杜撰引用。

**研究方法與誠實聲明（重要）**：
- 每一筆聲稱「這篇論文做了什麼」的內容，都來自實際擷取到的頁面（DOI landing page、出版社摘要頁、ResearchGate/SSRN/arXiv 頁面、或實際下載的 PDF 全文）。
- 有兩篇論文（D8 Pérez-Rodríguez & Holguín-Veras 2016；E12 Peters et al. Nutritious Supply Chain）我實際下載並閱讀了 PDF 全文的前數頁（含摘要、前言、方法論章節），內容標記為「全文已讀」。
- 其餘論文多數只能取得摘要層級內容（出版社摘要頁、Google Scholar/ResearchGate 摘要、或搜尋引擎對摘要的整理），標記為「摘要層級／付費牆」。我沒有付費資料庫存取權，多數 Elsevier/Informs/Taylor & Francis 全文被 403 擋下。
- 任何欄位若在可存取的來源中找不到依據，一律標註「**不可從現有來源驗證**」，不用臆測填補。
- 全部搜尋於 2026-09-13 進行，透過 Google/Bing 搜尋引擎索引結果、出版社頁面、ResearchGate/SSRN/arXiv/PMC 等，未使用任何盜版或未授權鏡像站。

---

## 目錄
1. [13 篇指定論文總表](#1-13-篇指定論文總表)
2. [13 篇指定論文詳細說明](#2-13-篇指定論文詳細說明)
3. [額外關鍵字搜尋補充論文（15+ 篇）](#3-額外關鍵字搜尋補充論文)
4. [商用/實務系統調查](#4-商用實務系統調查)
5. [實體關係綜合圖：Person / Rescue Team / Ambulance / … ](#5-實體關係綜合)
6. [方法比較表](#6-方法比較表)
7. [本專案能實際採用的東西，及其代價](#7-本專案能實際採用的東西及其代價)
8. [統一災害資源優化架構提案（願景版 vs. 3 週可行版）](#8-統一架構提案)

---

## 1. 13 篇指定論文總表

圖例：✅ = 已取得可驗證的實質內容（全文或詳細摘要）；🟡 = 只有摘要層級／二手摘要整理；🔒 = 確認為付費牆，僅摘要可讀。

| # | 標題（簡） | 作者 | 年份 | 期刊 | DOI | 開放版本 | 存取狀態 |
|---|---|---|---|---|---|---|---|
| A1 | Simultaneous location & fleet sizing of relief DCs + VRP | Chang, Chiang, Chang | 2024(2023) | Computers & OR 161:106404 | 10.1016/j.cor.2023.106404 | SSRN 摘要頁存在，PDF 下載連結回傳 403 | 🟡 摘要+二手整理 |
| A2 | Emergency logistics distribution for quick response | Sheu | 2007 | Transportation Research Part E 43(6):687-709 | 10.1016/j.tre.2006.04.004 | Semantic Scholar 列有 PDF 連結，未實際擷取全文 | 🟡 摘要層級 |
| A3 | Real-time relief distribution — rolling horizon | Lu, Ying, Chen | 2016 | Transportation Research Part E | 10.1016/j.tre.2016.05.002 | 無開放全文找到 | 🟡 摘要層級（校方學術系統摘要頁確認） |
| A4 | Stochastic ambulance dispatching & routing under road vulnerability | Chang, Chen, Lee, Chang | 2025 | J. of the Operational Research Society 76(1):34-60 | 10.1080/01605682.2024.2325051 | Taylor&Francis 全文頁面存在但為付費 | 🟡 摘要層級（頗詳細） |
| B5 | Integrated scheduling of repair crew & relief vehicle | Shin, Kim, Moon | 2019 | Computers & OR 105:237-247 | 10.1016/j.cor.2019.01.015 | 首爾大學 SCM Lab 有開放 PDF (scm.snu.ac.kr)，但擷取時連線被重置，未讀到全文 | 🟡 摘要層級 |
| B6 | Hierarchical compromise model — RecHADS | Liberatore, Vitoriano, Ortuño, Tirado, Scaparra | 2014 | Computers & OR 42:3-13 | 10.1016/j.cor.2012.03.019 | 未找到開放全文 | 🟡 摘要層級 |
| C7 | Ant colony optimization for disaster relief operations | Yi, Kumar | 2007 | Transportation Research Part E 43(6):660-672 | 10.1016/j.tre.2006.05.004 | 未找到開放全文；但被 D8 全文引用交叉驗證真實存在 | 🟡 摘要層級（交叉驗證） |
| D8 | Inventory-Allocation Distribution Models with Deprivation Costs | Pérez-Rodríguez, Holguín-Veras | 2016 (刊物標示 Nov 2016, Vol 50 No 4; 線上 Articles-in-Advance 2015-03) | Transportation Science 50(4):1261-1285 | 10.1287/trsc.2014.0565 | Bilkent 大學課程網站掛有全文 PDF | ✅ **全文已讀**（前 6 頁） |
| D9 | Appropriate objective function for post-disaster humanitarian logistics | Holguín-Veras, Pérez, Jaller, Van Wassenhove, Aros-Vera | 2013 | Journal of Operations Management 31(5):262-280 | 10.1016/j.jom.2013.06.002 | 未找到開放全文 | 🟡 摘要層級（核心論點被 D8 全文大量引用交叉驗證） |
| D10 | Equity and deprivation costs in humanitarian logistics | Gutjahr, Fischer | 2018 | European Journal of Operational Research 270(1):185-197 | 10.1016/j.ejor.2018.03.019 | 未找到開放全文 | 🟡 摘要層級 |
| D11 | Deprivation costs in facility location models | Cotes, Cantillo | 2019 | Socio-Economic Planning Sciences 65:89-100 | 10.1016/j.seps.2018.03.002 | 未找到開放全文 | 🟡 摘要層級（個案地區細節未獨立驗證，見下） |
| E12 | The Nutritious Supply Chain（WFP Optimus） | Peters, Silva, Gonçalves, Kavelj, Fleuren, den Hertog, Ergun, Freeman | 2021 | INFORMS Journal on Optimization 3(2):200-226 | 10.1287/ijoo.2019.0047 | Optimization Online 掛有工作論文全文 PDF | ✅ **全文已讀**（前 8 頁：摘要、前言、模型框架） |
| E13 | UN WFP: Toward Zero Hunger with Analytics | Peters, Silva, Wolter, Anjos, van Ettekoven, Combette, Melchiori, Fleuren, den Hertog, Ergun | 2022 | INFORMS Journal on Applied Analytics 52(1):8-26 | 10.1287/inte.2021.1097 | 未找到開放全文，僅 INFORMS 摘要頁 | 🟡 摘要層級 |

**小結**：13 篇中，2 篇（D8、E12）取得可驗證全文內容；其餘 11 篇為摘要層級／二手摘要整理，其中 C7、D9 的核心內容因被 D8 全文直接引用討論而獲得交叉驗證，可信度較高；其餘 9 篇（A1-A4, B5, B6, D10, D11, E13）僅能以摘要頁/搜尋引擎整理為準，任何細節如與摘要不符請以「不可驗證」處理。

---

## 2. 13 篇指定論文詳細說明

### A1 — Chang, Chiang, Chang（Computers & Operations Research 161:106404）
- **驗證等級**：🟡 摘要 + SSRN 列表頁 + 二手搜尋整理（作者服務單位：國立清華大學工業工程與工程管理系）。PDF 直接下載連結回傳 HTTP 403，SSRN abstract 頁面也回傳 403，故細節僅能來自搜尋引擎對摘要的重述,以及 ScienceDirect 摘要頁的存在性確認。
- **真實災害/資料**：與台灣國家災害防救科技中心（NCDR）合作，使用其對台北市大安區地震情境下道路網況與車速（作為地震規模、發生時間函數）的模擬資料做案例研究。
- **問題**：救災物資配送中心的選址與車隊規模，同時決定救災第一時間窗內的車輛與存貨路徑。
- **實體**：配送中心（待選址）、車隊、道路網（受震損影響的速度/可行性）、需求點。
- **決策變數**：（第一階段）配送中心開設位置、各配送中心車輛數；（第二階段，不確定性揭露後）車輛路徑、存貨配送量。
- **目標函數**：不可從現有摘要精確驗證數學式，摘要描述為兩階段隨機規劃，目標應與總配送/回應時間或成本相關（**具體目標函數式不可從現有來源驗證**）。
- **限制式**：不可從現有來源驗證細節（僅知含容量、車隊規模、路網可行性等一般性限制）。
- **演算法**：論文稱為「an efficient simulation optimization algorithm with feedback」（帶回饋的模擬最佳化演算法）。
- **是否實際部署**：否，為與 NCDR 合作之學術案例研究，非營運系統。
- **弱點**：不可從現有來源驗證（僅能推測：模擬最佳化在大規模路網下的即時性未知）。
- **對本專案的可能借鏡**：概念上「先分類災害情境（依規模/位置）→ 對應調整可用資源與最大服務半徑」與本專案 `URGENCY_MAX_KM` 依緊急度分級設定服務半徑上限的精神類似，但此篇的路網車速建模、車隊規模最佳化都需要真實路網圖與 NCDR 等級的地震模擬資料，超出本專案規模。

### A2 — Sheu（Transportation Research Part E 43(6):687-709）
- **驗證等級**：🟡 摘要層級（Semantic Scholar、EconPapers 摘要）。
- **問題**：災害後緊急救援需求的快速反應動態物流配送。
- **方法描述（摘要層級）**：一個「混合階層模糊模型」（heuristic hybrid hierarchical fuzzy model），處理三層動態物流問題：救援隊伍運送、配送中心、受災地區。區分兩類緊急物資：日常消耗品（水、餐盒）與日常使用裝備（睡袋、帳篷）。
- **真實災害/資料**：任務簡述指向台灣 921 大地震，但**我在可存取的摘要中沒有直接看到 921 地震個案的明確敘述**——這點請視為未獨立驗證，僅為任務提示中的既有資訊，未在我實際看到的摘要文字中被證實或推翻。
- **實體/決策變數/目標函數/限制式/演算法細節**：**不可從現有摘要層級來源精確驗證**（模糊層級架構暗示使用模糊集合處理不確定性，但無法取得公式）。
- **是否部署**：不可驗證，推測為學術模型。
- **對本專案的借鏡**：三層架構（救援隊→配送中心→受災地區）的分層思路，與本專案「CommunityResource（個人物資）／ResourcePoint（固定資源點）／CommunityNeed（需求）」三層資料模型在概念上相近，但這只是命名巧合層級的呼應，不構成技術移植依據。

### A3 — Lu, Ying, Chen（Transportation Research Part E, 2016）
- **驗證等級**：🟡 摘要層級，但來源包含作者所屬大學（陽明交通大學、台北科技大學）學術典藏摘要頁，內容具體，可信度較高。
- **真實災害/資料**：以 1999 年台灣 921 大地震為數值案例（摘要明確提及 "the large-scale earthquake that occurred on September 21, 1999 in Taiwan"）。
- **問題**：災後即時（real-time）救災物資配送，因應不確定且隨時間變化的需求與配送時間資訊。
- **方法架構**：Rolling horizon 框架，含兩模組——(1) 狀態估計與預測模組：預測救災需求與配送時間；(2) 救災配送模組：求解最佳配送流量，目標是縮短總配送時間並考慮不確定資料下的風險趨避決策。
- **決策變數/目標函數/限制式**：**具體數學式不可從摘要層級驗證**；已知目標方向為「最小化總配送時間」，並納入風險趨避（risk-averse）考量。
- **是否部署**：不可驗證，屬學術數值案例研究。
- **對本專案的借鏡**：「每隔一段時間重新評估、只執行當期決策」的 rolling horizon 精神，與本專案 `auto_dispatch()` 每 30 分鐘執行一次的排程模式高度呼應——這是本研究中少數「概念可以直接對應到現有程式碼設計」的論文，詳見第 7 節。

### A4 — Chang, Chen, Lee, Chang（JORS 76(1):34-60, 2025）
- **驗證等級**：🟡 摘要層級，但 Taylor & Francis 摘要頁內容詳細具體。
- **問題**：大量傷患事件（mass casualty incident）下，多個傷患集結點（casualty collection points）的救護車派遣與路徑規劃，考量地震造成的道路脆弱性與交通壅塞的不確定性。
- **實體**：傷患集結點、救護車、道路網（含脆弱性/壅塞不確定性）。
- **目標函數**：最大化所有傷患的預期存活人數（expected number of survivors）——這是一個**非線性**目標（存活機率通常是時間的非線性遞減函數）。
- **模型類型**：兩階段隨機混合整數非線性規劃（two-stage stochastic MINLP）。
- **演算法**：整合式模擬最佳化方法，結合 (1) 資料驅動的旅行時間情境生成演算法、(2) 樣本平均近似法（Sample Average Approximation, SAA）、(3) 以欄位生成（column generation）為基礎的啟發式方法。
- **真實災害/資料**：任務簡述指出使用台灣 NCDR 地震情境資料；此與作者群 Chang（同 A1 作者之一）長期與 NCDR 合作的模式一致，但**我在直接擷取到的摘要文字中未看到「NCDR」字樣的明確確認**，故此點視為「與作者已知合作模式高度一致，但未在本次可存取摘要中逐字驗證」。
- **是否部署**：不可驗證，屬學術模型論文。
- **弱點**：**不可從摘要驗證**；MINLP+SAA+column generation 的組合通常意味著求解時間長，不適合小規模即時系統。
- **對本專案的借鏡**：「用存活機率（而非簡單分數）作為目標函數的非線性成分」概念上很吸引人，但 SAA + column generation 的求解基礎設施遠超本專案範疇（見第 7 節「不建議採用」清單）。

### B5 — Shin, Kim, Moon（Computers & OR 105:237-247, 2019）
- **驗證等級**：🟡 摘要層級（ScienceDirect 摘要頁 + 搜尋引擎整理；首爾大學 SCM Lab 開放 PDF 存在但本次擷取因連線中斷未讀到全文）。
- **問題**：災後同時排程「道路搶修隊」與「救災車輛」，兩者互相依賴（路修好前救災車無法通行）。
- **目標函數**：最小化受災節點的總救援時間（total relief time）與受損節點的修復時間（recovery time）之和/組合。
- **方法**：混合整數規劃（MIP）模型，用於排程與路徑規劃。
- **是否部署**：不可驗證。
- **對本專案的借鏡**：「救援與修復兩種資源互相依賴、需聯合排程」的問題結構，若本專案未來要納入「道路/通行狀況」作為派遣限制條件，這是概念參考點，但目前本專案沒有道路搶修隊這個實體，暫不適用。

### B6 — Liberatore, Vitoriano, Ortuño, Tirado, Scaparra（Computers & OR 42:3-13, 2014，RecHADS）
- **驗證等級**：🟡 摘要層級。
- **真實災害**：2010 年海地大地震案例研究。
- **問題**：聯合最佳化「受損配送網路節點的修復順序」與「緊急物資的配送計畫」——即修復規劃如何影響後續配送效益。
- **方法**：階層式折衷模型（hierarchical compromise model），模型名稱 RecHADS。
- **是否部署**：不可驗證，屬學術案例研究（以海地震災資料為基礎的事後分析，而非震災當下實際使用的系統）。
- **對本專案的借鏡**：與 B5 類似，屬於「修復 × 配送」聯合最佳化家族，需要道路網路圖資，本專案目前不具備。

### C7 — Yi, Kumar（Transportation Research Part E 43(6):660-672, 2007）
- **驗證等級**：🟡 摘要層級，但**內容被 D8 論文全文直接引用交叉驗證**（D8 第 1264 頁列出 "Yi and Kumar 2007" 為「懲罰基礎模型」（penalty-based models）陣營的代表作之一，確認其真實存在且屬於此類別）。
- **問題**：災害救援物流中，物資從主要供應中心運送到受災區配送中心，以及傷患從受災區運送到醫療中心的協調問題。
- **方法**：螞蟻演算法（Ant Colony Optimization, ACO）元啟發式。
- **實體**：供應中心、配送中心、受災地區、傷患、醫療中心。
- **是否部署**：不可驗證，屬學術模型。
- **對本專案的借鏡**：ACO 屬於組合最佳化元啟發式，適合求解大型 VRP/路徑問題，但本專案目前的媒合問題規模（單一社區、少量候選）用不到元啟發式，貪婪法已足夠且更易解釋。

### D8 — Pérez-Rodríguez, Holguín-Veras（Transportation Science 50(4):1261-1285）✅ 全文已讀
- **驗證等級**：✅ **全文已讀**（第 1-6 頁：標題頁、摘要、前言、文獻回顧、方法論基礎）。
- **問題**：災後人道物流（PD-HL）中的存貨—配置—路徑（inventory-allocation-routing）問題，核心創新是**明確納入剝奪成本（deprivation cost）**。
- **核心概念（原文引用）**：目標函數是「社會成本」（social costs）= 物流成本（logistical costs，救援方承擔）+ 剝奪成本（deprivation costs，受益人承擔）。剝奪成本定義為「因缺乏取得財貨/服務而導致的人類受苦的經濟評價」，其大小是「剝奪時間」（deprivation time, δ）的函數 γ(θ, δ, Z)。論文區分「非遲滯性」（nonhysteretic：物資送達後傷害完全消除）與「遲滯性」（hysteretic：傷害無法完全消除）兩種剝奪成本函數形狀，本篇聚焦較簡單的非遲滯性版本。
- **實體**：配送中心（DC）、需求節點/配送點（points of distribution, PODs）、（多商品版本擴充）多種物資。
- **決策變數**：一個有序配送序列 X = {(i₁,d₁,t₁), (i₂,d₂,t₂), …}，其中 iⱼ 是第 j 次配送造訪的節點、dⱼ 是配送量、tⱼ 是配送時間。
- **目標函數**：最小化 Ω_T(X,T)（總物流成本）+ Γ*_T(X,T)（總剝奪成本），兩者皆為配送序列 X 在規劃期間 T 內的函數。單點剝奪成本 Γᵢ(X,t) = γ_g(θ_g, δ_it)·π_it（π 為社經特徵加權）。
- **限制式**：文中提及供給可用性、需求滿足（但允許未滿足需求並承擔剝奪成本，而非硬性限制）；問題性質為 NP-hard。
- **演算法**：文中提到「設計合適的啟發式解法」，區分單商品與多商品情境（單商品在第 5 節、多商品在第 6 節分別處理），但**第 1-6 頁範圍內未讀到具體啟發式演算法名稱**（此為超出擷取頁數範圍，不可從已讀部分驗證）。
- **是否部署**：不可驗證，屬學術模型（文中批評「多數人道物流研究只是把商業物流模型套用過來,沒有真正處理 PD-HL 的獨特現象」，暗示這是理論性貢獻而非已部署系統）。
- **弱點（原文自陳）**：路徑相依（path-dependent）最佳化問題，NP-hard；量化剝奪成本本身很困難，因為大災害會摧毀物資交易的市場機制，使得「剝奪成本」變成需要用經濟評價技術（economic valuation）估計的外部性；論文承認遲滯性（更貼近真實）版本計算上更困難，因此先做非遲滯性版本。
- **對本專案的借鏡**：這是本次研究中**最值得參考的一篇**，因為它提出了一個具體、簡單、可落地的公式化想法——用「等待時間」作為懲罰因子。本專案的 `vulnerability` 分數目前只反映「靜態脆弱度」（過去 7 天的打卡/警報/孤立狀態），並沒有「這個需求已經等了多久沒被處理」這個動態剝奪成本概念。詳見第 7 節的具體建議（cheap tie-breaking rule）。

### D9 — Holguín-Veras, Pérez, Jaller, Van Wassenhove, Aros-Vera（Journal of Operations Management 31(5):262-280, 2013）
- **驗證等級**：🟡 摘要層級，但**核心論點被 D8 全文大量引用與復述**（D8 第 1262-1264 頁多次引用並總結此文論點），故可信度高。
- **核心論點（經 D8 引用交叉驗證）**：主張「社會成本」（logistic cost + deprivation cost 之和）應是災後人道物流模型的正確目標函數，而非單純物流成本最小化；批評「懲罰基礎模型」（penalty-based，如硬性公平限制、固定/變動懲罰因子）與「未滿足需求模型」都無法保證解的存在性或正確反映受苦程度；批評「等量對待所有未滿足需求」的模型忽略了剝奪時間的非疊加性（同一人被剝奪 3 天水，不等於被剝奪 3 倍的痛苦）。
- **實體/決策變數/目標函數/限制式數學式**：**不可從現有摘要層級精確驗證**（此篇為概念性/經濟學基礎論文而非數學建模論文，D8 引用內容也偏向論述而非公式）。
- **是否部署**：不可驗證，屬理論性論文。
- **對本專案的借鏡**：概念上支持「不要只看物資有沒有配到，要看等多久」這個原則，呼應第 7 節建議。

### D10 — Gutjahr, Fischer（EJOR 270(1):185-197, 2018）
- **驗證等級**：🟡 摘要層級。
- **真實災害**：2015 年尼泊爾大地震案例。
- **核心發現**：單純最小化總剝奪成本可能導致「任意不公平」的解（例如犧牲少數人的極端等待時間換取總和最小）；建議在目標函數中納入剝奪成本的吉尼係數（Gini index）以兼顧公平性。
- **對本專案的借鏡**：這是對 D8/D9 剝奪成本框架的重要修正——純粹「加總最小化」可能犧牲弱勢者，這與本專案把 vulnerability 直接加分（而非只看總體效率）的設計精神相符，是支持現有設計選擇的文獻佐證。

### D11 — Cotes, Cantillo（Socio-Economic Planning Sciences 65:89-100, 2019）
- **驗證等級**：🟡 摘要層級。
- **問題**：在設施選址模型中納入剝奪成本，同時考慮私有成本（運輸、設施、存貨）與剝奪成本，特別聚焦災後「關鍵最初幾小時」的決策。
- **真實災害/資料**：任務簡述指出為 2010-2011 年哥倫比亞加勒比海地區水患，**但我在可存取的摘要中沒有看到此具體案例的文字確認**（僅能從作者所屬機構 Universidad del Norte, Barranquilla 位於哥倫比亞加勒比海岸這點做間接推論）——此為**不可完全驗證**項目，請以任務簡述為準但知悉未經獨立核實。
- **對本專案的借鏡**：選址模型需要真實候選點清單與運輸成本矩陣，超出本專案範疇（本專案的 ResourcePoint 是既有清單，不涉及「選址決策」本身）。

### E12 — Peters, Silva, Gonçalves, Kavelj, Fleuren, den Hertog, Ergun, Freeman（INFORMS J. on Optimization 3(2):200-226, 2021）✅ 全文已讀
- **驗證等級**：✅ **全文已讀**（前 8 頁：標題頁、摘要、前言、文獻回顧、WFP 供應鏈背景介紹）。
- **作者機構確認**：WFP 供應鏈規劃單位（Koen Peters, Sérgio Silva, Rui Gonçalves, Mirjana Kavelj）、Tilburg University 計量經濟與作業研究系（Hein Fleuren, Dick den Hertog）、Northeastern University 工業工程系（Ozlem Ergun）、UPS Advanced Technology Group（Mallory Freeman）。
- **摘要原文重點**：WFP 是全球最大人道組織，每年在 80 國幫助約 9000 萬人。本文提出一個混合整數線性規劃（MILP）模型，**同時**最佳化：(1) 要配送的食物籃組成（food basket）、(2) 採購來源計畫（sourcing plan）、(3) 配送路徑計畫（routing plan）、(4) 轉移模式（transfer modality：實物 vs. 現金/憑證），時間範圍為每月、涵蓋一個預先定義的規劃期。
- **已確認部署國家/成果（原文明載）**：
  - **伊拉克（Iraq）**：使用最佳化將營運成本降低 **12%**，且未犧牲營養價值。
  - **葉門（Yemen）**：協助將既有營運從 300 萬受益人規模擴大到 600 萬受益人規模。
  - **El Niño 應變**：文中提及作為第三個應用案例（**具體成效數字在已讀頁數範圍內未列出，不可從已讀部分驗證細節**）。
- **問題脈絡**：文獻回顧指出人道物流研究多半只處理單一子問題（設施選址、配送、存貨控制三選一），本文試圖填補「長期復原階段」（long-term recovery，WFP 主要業務型態，而非急難初期 72 小時）整合式決策的研究空白，並首次將「營養需求」直接作為需求定義（而非預先固定的食物籃），讓模型可以優化食物籃本身。
- **決策變數（架構層級）**：食物籃組成（各營養素/商品配比）、採購來源分配（International/Regional/Local suppliers）、轉移模式選擇（Commodity Vouchers / Value Vouchers / Cash，本文核心模型聚焦 Commodity Vouchers，第 6 節再擴充）、配送路徑與時程。
- **目標函數**：**不可從已讀的前 8 頁精確驗證數學式**（後續章節 3 才會展開 MILP 公式，超出已讀範圍），但摘要與前言明確指出目標同時涵蓋成本最小化與營養/需求滿足。
- **是否部署**：**是，已在 WFP 實際營運中部署**（Iraq/Yemen/El Niño 為真實營運案例，非模擬）。此系統即業界所稱的 "Optimus"。
- **弱點**：**不可從已讀頁數精確驗證**（開頭章節未列出限制與討論部分）。
- **對本專案的借鏡**：這篇的規模（跨國、月度規劃、多商品、多採購來源）遠超本專案，但其「同時決策食物籃內容 + 配送方式 + 轉移模式」的**整合式思維**，可以簡化成一個小啟示：本專案的媒合分數也是「多因子同時決策」，這在方向上是一致的，只是規模天差地遠——詳見第 7 節「不建議採用」清單。

### E13 — Peters et al.（INFORMS Journal on Applied Analytics 52(1):8-26, 2022）
- **驗證等級**：🟡 摘要層級（INFORMS 摘要頁、Tilburg University 研究入口網站）。
- **內容架構（摘要層級確認）**：描述 WFP 的三個分析工具：(1) **Supply Chain Management Dashboard** — 描述性與預測性分析,提供端到端能見度並預警營運問題；(2) **Optimus** — 即 E12 的 MILP 模型，同時優化食物籃組成與供應鏈規劃；(3) **DOTS** — 資料整合平台，協助自動化與同步複雜的資料流。
- **真實案例**：任務簡述提及伊拉克/南蘇丹/COVID-19 應變，但**我在可存取的摘要內容中僅明確驗證了「Dashboard + Optimus + DOTS」三工具架構本身，未逐一驗證每個國家案例的具體文字**——此點列為不可完全驗證，以任務簡述為準但未逐字核實。
- **獎項/實務地位**：此系列工作曾入圍 INFORMS Edelman Award（依 ORMS Today 報導），顯示其為受業界認可、實際落地的分析系統，而非純學術模型。
- **對本專案的借鏡**：DOTS「資料整合/自動化」的角色，對應到本專案就是「LINE Bot 打卡/警報資料 → 自動轉換成 vulnerability 分數」這條管線，本專案已經做到這件事的簡化版（`_vulnerability_pts()`），方向正確，只是規模與資料來源複雜度天差地遠。

---

## 3. 額外關鍵字搜尋補充論文

以下依任務指定的關鍵字搜尋取得，均為透過搜尋引擎索引到的真實論文（可在 ScienceDirect / Springer / arXiv / PMC / ResearchGate / SSRN / IEEE Xplore 等平台核實其存在），但**除非特別註明「全文」，否則以下全部為摘要層級或搜尋引擎摘要整理**，未逐篇下載全文核實方法細節。這是誠實的限制，不代表這些論文不存在或不相關。

| 主題 | 論文 | 來源/驗證程度 |
|---|---|---|
| 多商品網路流（奠基之作） | Haghani & Oh (1996), multi-commodity multi-modal network flow for disaster relief | 🟡 未直接讀取，但被 D8 全文（Transportation Science 2016）與 D8 引用的其他論文多次交叉引用，確認真實存在且為此領域奠基文獻 |
| 隨機兩階段運輸規劃 | Barbarosoğlu & Arda (2004) | 🟡 被 D8 全文列為「懲罰基礎模型」代表作之一（交叉驗證） |
| 未滿足需求模型 | Özdamar, Ekinci, Küçükyazici (2004) | 🟡 同上，被 D8 全文引用 |
| 撤離+配送整合選址模型 | Yi & Özdamar (2007) | 🟡 被 D8 全文引用 |
| 物資預置隨機模型 | Rawls & Turnquist (2010) | 🟡 被 D8 全文引用（Pre-Positioning of Relief Supplies） |
| 最後一哩配送 | Balcik, Beamon, Smilowitz (2008), last mile distribution | 🟡 被 D8 全文引用 |
| 多資源選址-配置線性規劃 | "Solving multi-resource allocation and location problems in disaster management through linear programming" (arXiv 1812.01228) | ✅ **部分全文已讀**（透過 WebFetch 擷取，含目標函數方向、simplex + 全單模矩陣求解說明） |
| 災害應變規劃技術綜述 | "Recent Advances in Disaster Emergency Response Planning: Integrating Optimization, Machine Learning, and Simulation" (arXiv 2505.03979, 2025) | 🟡 摘要層級（PDF 已下載但內容為圖片/壓縮流，文字擷取失敗）；摘要顯示涵蓋 MILP、穩健最佳化、MDP/RL、模擬等方法的整合綜述 |
| 隨機動態存貨配置＋UAV | "The Stochastic Dynamic Post-Disaster Inventory Allocation Problem with Trucks and UAVs" (arXiv 2312.00140 / Transportation Science) | 🟡 摘要層級 |
| 地震物流選址路徑多目標模型 | "A Multi-Objective Simultaneous Routing, Facility Location and Allocation Model for Earthquake Emergency Logistics" (arXiv 2503.22487) | 🟡 摘要層級 |
| 修復隊資源排程 | "Repair resources scheduling for attention of transitory road disruptions in humanitarian aid networks" (ScienceDirect, 2025) | 🟡 摘要層級 |
| 修復隊+配送聯合排程 | "Network Repair Crew Scheduling and Routing for Emergency Relief Distribution Problem" (ResearchGate) | 🟡 摘要層級 |
| 地震救援分配演算法（新方法對比匈牙利演算法） | "Application of a new assignment algorithm based on the minimax difference in earthquake emergency rescue" (Nature Scientific Reports / PMC, 2026) | 🟡 摘要層級——明確提及提出 minimax difference submatrix (MDS) + k-means 分群，計算負擔優於傳統匈牙利演算法 |
| 醫生-傷患不平衡指派 | "A Modified Hungarian Method for Unbalanced Assignment Doctor Problem in Disaster Management" (ResearchGate) | 🟡 摘要層級——修改匈牙利法以避免虛擬變數，讓所有傷患都能被服務 |
| 救護車重新部署（經典 ADP） | Maxwell et al., "Approximate Dynamic Programming for Ambulance Redeployment" (INFORMS Journal on Computing) | 🟡 摘要層級（此為 EMS 領域經典 ADP 應用，非災害專屬但方法論相關） |
| 救護車派遣公平性 MDP | Luan, Pan, Chen, Bing, Yang, Jin, "Markov-Decision-Process-Based RL for Emergency Vehicles Dispatch... Efficiency and Fairness" (2024, 中國 EMS 資料) | 🟡 摘要層級 |
| 事件驅動救護車派遣 Transformer+RL | "Event-driven dynamic ambulance dispatch: A transformer-based reinforcement learning approach with model explainability" (ScienceDirect, 2026) | 🟡 摘要層級 |
| GNN+穩健最佳化 應急物流網路 | "AI-driven emergency logistics network"（Nature Scientific Reports，Event-Driven STGNN + Adaptive Robust Optimization），驗證資料含 COVID-19、暴風雪等真實情境 | 🟡 摘要層級 |
| Benders 分解＋人道物流 | "Capacity reservation for humanitarian relief: A logic-based Benders decomposition method with subgradient cut" (EJOR) | 🟡 摘要層級 |
| Benders 分解＋備災規劃 | "Nested logic-based Benders decomposition for disaster preparedness planning with horizontal coordination" (IISE Transactions, 2025) | 🟡 摘要層級 |
| ALNS＋救災物流網路設計 | "An extended version of adaptive large neighborhood search for a relief commodities distribution network design under uncertainty" (Scientia Iranica) | 🟡 摘要層級 |
| 多階段隨機規劃＋滾動視野 | "Multi-stage Stochastic Programming Methods for Adaptive Disaster Relief Logistics Planning" (arXiv 2201.10678) | 🟡 摘要層級 |
| 螞蟻演算法（近期） | "A New Ant Colony-Based Methodology for Disaster Relief" (MDPI Mathematics) | 🟡 摘要層級 |
| 台灣 921/Chi-Chi 地震災害管理綜合檢討 | "Disaster management following the Chi-Chi earthquake in Taiwan" (PubMed/ResearchGate) | 🟡 摘要層級——非最佳化論文，但確認 921 地震後暴露的問題：指揮中心失能、通訊不良、軍民協調不足、到院前救護延遲、醫院超載、人力不足、公衛管理失序，是本研究中對「NCDR 為何存在」的背景確認 |

**誠實總結**：以上補充論文清單超過 15 篇（實際約 22 篇），滿足任務要求的「至少 15 篇」，但**沒有一篇是我下載全文逐句核實方法細節**（唯一兩篇全文核實的是 D8、E12，已列在第 2 節）。若要在計畫書中引用以上補充論文的具體方法細節（例如 MDS 演算法的精確步驟、Benders 分解的子問題結構），**必須先取得全文**，我目前只能保證「論文標題、作者、期刊、大致主題方向」為真實可查證，無法保證方法細節轉述完全準確。

---

## 4. 商用/實務系統調查

以下針對其**公開文件中揭露的架構**做整理，不採信行銷語言中的模糊承諾。

### One Concern（災害數位分身）
公開資訊顯示其核心概念是「數位分身」（digital twin）架構：以物理模型（physics-based）搭配資料驅動的誤差修正模型（data-driven error correction），用歷史災害的相似度評估來訓練修正模型（similarity-based hybrid modeling）。另有研究描述其分散式運算框架，將災害語意資料切分給多個運算節點以加速大規模模擬（在野火情境中據稱可將預測誤差降低約 50%，惟此數字來自學術論文而非官方一手數據，**不可視為 One Concern 官方驗證數字**）。**與本專案相關性**：本專案沒有物理模擬能力，也沒有感測器網路，這類數位分身架構完全超出範疇；唯一可借鏡的概念是「用歷史相似案例做校正」，但本專案連歷史案例資料庫都還沒有（見 MEMORY 中「決賽前工作範圍」所述：不做校正是刻意排除項目）。

### WebEOC（Juvare）
公開文件顯示這是雲端化的事件管理平台，核心賣點是**共同作戰圖像（common operating picture）**與**免程式碼工作流程建構**：使用者可自行拼裝表單、儀表板、角色制首頁（不同角色登入看到不同資訊），並透過 REST/SOAP API 的「Connectors」串接第三方資料源。近期加入「Juvare AI Assistant」聲稱用即時分析輔助資源配置決策（**具體演算法未公開，屬行銷語言，不可驗證**）。**相關性**：WebEOC 的「角色制介面 + 免程式碼表單」模式對小型專案有參考價值——用最小工程成本讓不同角色（志工/管理員）看到對應資訊，這點本專案的管理員後台已經部分做到。

### ArcGIS Mission（Esri）
公開文件明確定位為「共同作戰圖像」工具，強調地理空間圖層疊加（即時天氣、動態圖資、影像）以提供情境感知，並有「Mission Manager」角色追蹤團隊與資產的位置與活動。Esri 另外發布了專門的「Emergency Management Lens」架構指南，強調的是**跨工作流程、跨團隊、跨技術的整合方式**，而非單一產品規格。**相關性**：本專案已有地圖顯示功能（`dashboard_overview.png` 等截圖顯示），ArcGIS Mission 的「地理圖層疊加多來源資料」思路是本專案地圖模組可參考的方向，但完整的 GIS 平台建置超出範疇。

### Veoci
公開文件顯示其定位為「虛擬應變中心」（virtual EOC），核心賣點同樣是免程式碼工作流程建構與「一鍵啟動」預先設定好的應變計畫（樣板化的通知/檢查清單/任務會在啟動時自動發送）。**相關性**：「一鍵啟動樣板化流程」的概念，與本專案「進入緊急模式後 `auto_dispatch()` 自動開始運作」的設計哲學一致，屬於已經做到的部分。

### Everbridge 360 + Dataminr First Alert
Everbridge 360 公開文件強調其「風險情報監控中心」（RIMC）用機器學習分析上百種風險類別的大量資料來源，做「可信度威脅」的識別與排序；Dataminr 以「Risk Events」分類方式與 Everbridge 整合，將外部事件（新聞、社群媒體訊號）對應到告警子類別。**相關性**：這類系統做的是「事件偵測與早期預警」，屬於本專案完全沒有涉及的層面（本專案是「災後資源媒合」，不是「災害偵測」），沒有直接技術可借鏡，但概念上「把打卡/警報資料當作弱訊號持續監控」與本專案 vulnerability 分數的資料來源精神一致。

### Palantir Foundry / AIP（本體論架構）
這是本次商用系統調查中**唯一有紮實技術文件（而非純行銷頁）可查證**的案例。Palantir 官方架構文件明確描述：
- **Ontology（本體論）**：把企業資料、邏輯、行動、安全政策整合成一個「人類與 AI 代理都能操作的表示法」。文件原文："The Ontology's language models the 'nouns' and 'verbs' of operational processes"——即用「名詞（物件/實體）」與「動詞（可執行的動作）」作為建模的基本單位。
- **規模化能力**：文件聲稱可「查詢數十億筆物件、協調數萬筆行動、並持續納入回饋學習」（此為官方文件文字，非我獨立驗證的效能數字）。
- **運算引擎**：支援多節點引擎（Spark、Flink）、單節點高效引擎（DuckDB、Polars）、以及任意容器化的「自帶引擎」。
- **自動化模式**：三種——排程型自動化、近即時事件驅動自動化（處理串流資料）、以及與 API 操作交織的自動化。
- **文件未揭露的部分**（誠實列出）：物件屬性繼承模型、連結基數限制、決策/最佳化演算法的具體實作、分散式協調的狀態管理細節——這些都是「能力宣稱」而非「實作規格」。
**相關性**：Palantir 的「本體論 = 名詞（物件+屬性+連結）+ 動詞（行動）」這個建模語言，是本報告第 5 節「實體關係綜合」的方法論靈感來源之一——用同樣的「物件-連結-行動」框架來畫本專案的實體圖，即使本專案不會用到 Palantir 的任何實際軟體。

---

## 5. 實體關係綜合

依 Palantir 本體論式的「物件（名詞）+ 連結 + 行動（動詞）」框架，將任務指定的實體關係整理如下（這是我依據上述文獻中反覆出現的實體與關係整理出的**綜合圖**，非抄自任一單篇論文）：

```
                     ┌─────────────┐
                     │  Disaster   │ (地震/水災等，具規模、位置、時間)
                     │    Area     │
                     └──────┬──────┘
                            │ 影響
              ┌─────────────┼─────────────────┐
              ▼             ▼                 ▼
        ┌──────────┐  ┌──────────┐     ┌─────────────┐
        │  Person  │  │Road/Bridge│     │  Warehouse  │
        │(居民/長者)│  │ (受損機率、│     │ (存貨、位置) │
        └────┬─────┘  │ 通行時間)  │     └──────┬──────┘
             │         └─────┬─────┘            │ 補給
     ┌───────┴────────┐      │ 限制通行時間        ▼
     ▼                ▼      ▼              ┌─────────────┐
┌─────────┐    ┌───────────┐          ┌────►│Food/Water/  │
│ Victim  │    │  Shelter  │          │     │Medicine     │
│(需醫療/ │    │ (收容量、  │◄─────────┘     └──────┬──────┘
│ 需物資) │    │  位置)     │  安置                  │ 配送
└────┬────┘    └───────────┘                         ▼
     │ 送醫（依傷勢分級）                        ┌──────────┐
     ▼                                         │  Truck   │
┌───────────┐        ┌──────────┐              │(容量、路徑)│
│ Ambulance │───────►│ Hospital │              └────┬─────┘
│(位置、容量)│  送達   │(容量、床位)│                   │ 沿路徑
└─────┬─────┘        └──────────┘                    ▼
      │ 派遣                                     Disaster Area
      ▼
┌──────────────┐      ┌─────────┐
│ Doctor / EMT │◄─────┤ Rescue  │
│ (技能、位置)  │ 隸屬  │  Team   │
└──────────────┘      │(技能組合、│
                       │ 位置、任務)│
                       └────┬────┘
                            │ 執行搜救/物資配送任務
                            ▼
                       Disaster Area / Victim
```

**關係說明（文字版，因為表格比圖更精確）**：

| 實體 A | 關係（動詞） | 實體 B | 對應到本專案現況 |
|---|---|---|---|
| Person | 居住於 | Disaster Area | `CommunityNeed.lat/lng`（有座標） |
| Person | 平時累積 | Vulnerability 分數 | `DailyCheckin` + `Alert` + `CareRelation` → `_vulnerability_pts()`（已實作） |
| Person | 災時發出 | Need（需求） | `CommunityNeed`（已實作） |
| Victim | 依傷勢分級送往 | Hospital | **完全未實作**——本專案沒有「傷患分級」與「醫院」實體 |
| Ambulance | 派遣至 | Victim | **完全未實作** |
| Ambulance | 受限於 | Road/Bridge 通行狀態 | **完全未實作**——目前用 Haversine 直線距離,無路網 |
| Rescue Team | 具備 | 技能組合（skill） | **未實作**——本專案是「物資媒合」不是「人力調度」 |
| Truck | 承載 | Food/Water/Medicine | 概念對應 `CommunityResource`（志工個人物資，非車隊） |
| Warehouse | 儲存 | Food/Water/Medicine | 對應 `ResourcePoint`（固定資源點，已實作，但無庫存量欄位——只有「有/無」，見下方弱點） |
| Shelter | 收容 | Victim / Person | `ResourcePoint.point_type == "shelter"`（已有類型,但無容量欄位） |
| Road/Bridge | 限制 | Truck/Ambulance 的可達性 | **完全未實作** |
| Doctor/EMT | 隸屬於 | Rescue Team | **未實作** |
| Rescue Team | 執行任務於 | Disaster Area | 概念對應「志工」執行「media 建議」,但無「隊伍」概念,是個人層級 |

**關鍵觀察**：本專案目前的實體模型（CommunityResource / ResourcePoint / CommunityNeed）本質上只覆蓋了上圖中 **Person → Need → Resource(Warehouse簡化版)** 這一小段，尚未觸及 Victim/Ambulance/Hospital/RescueTeam/Road 這整條「醫療後送鏈」與「道路可達性」。這不是缺陷，而是**範疇選擇**——鄰里守望平台的定位是「日常關懷 + 災時物資媒合」，不是「大量傷患後送調度」，這點在第 7、8 節會進一步說明為何多數學術文獻（尤其是 A4、B5、B6、Hungarian/MDP 救護車派遣類）的技術**不適用**於本專案現階段範疇。

---

## 6. 方法比較表

**方法適用性判斷依據**：以下「適合場景」欄位僅根據本報告實際檢索到的論文中，該方法被拿來解決什麼問題來歸納，而非教科書式的通用敘述。若某方法在檢索範圍內找不到對應的災害應用論文，會誠實註記。

| 方法 | 本報告中找到的實際應用 | 適合災害應變系統的哪個環節 | 對本專案的適用性 |
|---|---|---|---|
| **Dijkstra / A\*** | 在檢索範圍內，沒有找到「以 Dijkstra/A\* 本身為研究貢獻」的災害論文；它以「子程序」形式隱含在幾乎所有 VRP／路網修復論文中（如 B5、B6、道路搶修+配送聯合排程論文），用來計算受損路網上的最短可行路徑 | 路網上兩點間最短/最快路徑查詢，是幾乎所有路徑規劃模型的基礎子程序 | **需要路網圖資才能用**，本專案目前只有 Haversine 直線距離，沒有道路圖，此方法暫時無從導入 |
| **Min-Cost Max-Flow** | Haghani & Oh (1996) 奠基性的多商品網路流模型；「Applying network flow optimisation techniques to minimise cost associated with flood disaster」等論文 | 供應鏈骨幹的物資流量分配（倉庫→配送中心→需求點的流量規劃），假設網路容量與成本已知 | 概念上可用於「多個資源點同時服務多個需求」的批次分配，但本專案目前是逐筆貪婪處理,尚無「同時求解全域最佳流量」的需求規模 |
| **Multi-Commodity Flow** | 同上 Haghani & Oh；多商品存貨配置模型（多種物資同時規劃） | 當有多種異質物資（水、食物、藥品）需要透過共用運輸網路配送時 | 本專案的 `TYPE_AFFINITY` 矩陣已用簡化方式處理「多種物資類型」，尚不需要正式多商品流模型 |
| **Hungarian Algorithm（指派問題）** | 「Modified Hungarian Method for Unbalanced Assignment Doctor Problem」（醫生-傷患指派）；「minimax difference」新論文明確拿來跟匈牙利演算法比較（用於地震救援隊指派） | 一對一或小規模指派問題（例如「N 個醫生對 M 個傷患」），當雙方數量不平衡時需要修改版本 | **這是本報告中最容易低成本借鏡的方法之一**——但本專案目前是「多對一貪婪」（一個需求選最佳候選,資源被標記為不可用),如果要處理「同一時間批次多筆需求 vs 多筆資源」的全域最佳指派，Hungarian algorithm 是自然的下一步（見第 7 節） |
| **MILP** | E12/E13 (WFP Optimus，**已實際部署**)；A1、D11、多篇選址/路徑模型 | 當決策變數少、規則明確、需要證明「全域最優解」時（WFP 案例是月度規劃，不需要秒級反應） | 本專案的貪婪評分函數本質上是 MILP 的「啟發式替代品」——本專案規模小、即時性要求高，不建議換成 MILP 求解器（見第 7 節） |
| **MINLP** | A4（存活機率的非線性目標） | 當目標函數本身是非線性的（如存活機率隨時間非線性衰減）且需要精確建模這種非線性關係時 | 求解複雜度高,需要 SAA、column generation 等配套技術,**明確不建議**本專案採用 |
| **VRP（車輛路徑問題）** | A1（車隊路徑）、多篇「損壞路網下的人道 VRP」論文、ALNS 論文 | 當「一輛車/一位志工要跑多個地點」時（多站配送) | 本專案目前的媒合是「一份物資對一筆需求」的單站模式，尚未進入「一位志工跑多站」的排程問題，VRP 暫不適用，但若未來志工要一次配送多筆需求，這會是自然的升級方向 |
| **Facility Location（設施選址）** | D11、A1（DC 選址）、Balcik & Beamon (2008) | 決定「倉庫/避難所該蓋在哪」的**規劃階段**問題，通常是災前決策 | 本專案的 ResourcePoint 是「匯入既有政府開放資料」,不涉及選址決策,此方法不適用（本專案不決定避難所蓋在哪，只是使用既有清單） |
| **Benders Decomposition** | 「Capacity reservation for humanitarian relief」、「Nested logic-based Benders decomposition for disaster preparedness」 | 用於將大型兩階段隨機規劃問題（如「先決定倉庫容量，再因應各種災害情境決定配送」）拆解成可求解的子問題 | 需要正式的兩階段隨機規劃模型作為前提,本專案完全沒有這類模型,**不適用** |
| **Column Generation** | A4（結合 SAA 求解 MINLP） | 當決策變數數量隨問題規模指數成長（如大量可能路徑）時，用來避免窮舉所有變數 | **不適用**，本專案候選集合通常只有幾十筆,不需要這種技術 |
| **Sample Average Approximation (SAA)** | A4 | 用有限抽樣情境近似求解含連續機率分布的隨機規劃問題 | 需要「不確定性建模」的前提（如旅行時間分布），本專案目前沒有這類機率模型,**不適用** |
| **Monte Carlo Simulation** | A1（模擬最佳化演算法）、One Concern 數位分身（物理模型+資料修正） | 當系統行為難以用封閉形式公式描述、需要透過大量情境抽樣估計結果分布時 | 若本專案未來想做「災前情境演練」（例如模擬不同規模地震下系統的物資缺口）,蒙地卡羅模擬是低成本的可行方向,但這屬於「規劃工具」而非即時派遣邏輯 |
| **Robust Optimization** | 多篇「robust optimization for relief logistics」搜尋結果；GNN+ARO 應急物流網路論文 | 當不確定性的機率分布本身不可靠或未知，只知道不確定性的範圍（uncertainty set）時，尋找「最壞情況下仍可接受」的解 | 需要正式最佳化模型作為前提,**不適用**本專案現況 |
| **Stochastic Programming** | A1（兩階段隨機規劃）、Barbarosoğlu & Arda、Rawls & Turnquist | 災前準備階段（如「應該預先儲備多少物資、放在哪」），因為災害發生與否/規模是機率性的 | 本專案沒有「災前預先儲備決策」這個功能模組，**不適用**現況,但若未來要做「建議社區該囤多少物資」的功能，這是理論基礎來源 |
| **Rolling Horizon / MPC** | A3（Lu, Ying, Chen 2016，**與本專案設計精神最接近**） | 災後即時應變，資訊隨時間逐步揭露、需要「每隔一段時間重新規劃、只執行當下決策」的場景 | **高度適用且已部分實作**——`auto_dispatch()` 每 30 分鐘重跑一次，本質上已經是簡化版 rolling horizon；可以借鏡的是 A3「加入對未來需求的預測」這個尚未做到的部分（見第 7 節） |
| **Ant Colony Optimization** | C7（Yi & Kumar 2007）、MDPI 近期論文 | 大型組合最佳化問題（如大規模 VRP）,當問題規模大到精確解法不可行時 | 本專案候選集合規模小（單一社區,幾十筆候選）,精確貪婪法已足夠,**不需要**元啟發式 |
| **Genetic Algorithm** | "optimal allocation model for emergency rescue teams" (NSGA-II)；modified PSO 論文（相近的演化式方法） | 多目標最佳化（例如同時要「公平」又要「效率」時，找一組 Pareto 最優解） | 本專案目前是單一分數的貪婪排序，若未來要顯式處理「效率 vs 公平」的多目標權衡（而非現在隱含在分數公式裡的加權和），GA/NSGA-II 是理論選項，但**對單一管理員的小系統來說，可解釋性會大幅下降**，不建議 |
| **ALNS** | Scientia Iranica 論文（地震情境下的救災物資配送網路設計） | 大型 VRP/選址-路徑聯合問題的近似求解 | **不適用**，問題規模門檻遠高於本專案 |
| **MDP / RL** | 多篇救護車派遣文獻（Maxwell ADP、Luan et al MDP-RL、Transformer-RL）、STGNN+ARO | 高頻率、重複發生、有大量歷史資料可供訓練的派遣決策（如城市 EMS 系統每天處理數百通電話） | **明確不適用**——本專案是低頻率（緊急模式偶爾啟動）、資料量小（可能整個競賽期間只有測試資料）的場景，RL 需要的訓練資料量與環境穩定性本專案都不具備 |
| **Graph Neural Networks** | STGNN+ARO 應急物流論文、BiGGAT 停電時間預測 | 當系統可自然表示成圖（路網、供應鏈網路）且有足夠歷史資料訓練節點/邊的表示時 | **明確不適用**，本專案沒有路網圖資,也沒有訓練資料量 |

---

## 7. 本專案能實際採用的東西，及其代價

背景限制（誠實列出）：單一管理員、無專職 OR 工程師、距離決賽約 3 週、系統目前是 FastAPI + LINE Bot + PostgreSQL/SQLite、`dispatch.py` 是純 Python 貪婪評分（無求解器依賴）、沒有道路網圖資、沒有醫院/救護車/傷患實體。以下按「現在就能低成本加」vs.「需要本專案目前不具備的基礎設施」分類。

### 現在就能低成本加（幾小時到 1-2 天工作量，不需要新的資料來源）

1. **等待時間懲罰（Deprivation-cost 精神的極簡版）** —— 直接受 D8/D9 啟發。
   目前 `_score()` 完全沒有「這筆需求已經開放多久沒被處理」這個因子（只有排序時用 `created_at` 做 tie-break，不影響分數本身）。可以加一個小項：
   ```python
   wait_hours = (datetime.utcnow() - need.created_at).total_seconds() / 3600
   wait_pts = min(wait_hours * WAIT_PTS_PER_HOUR, WAIT_PTS_CAP)  # 例如每小時 +1，上限 15
   ```
   加進 `_score()` 的加分項。這正是 D8 論文「用剝奪時間的函數當作懲罰/加分」的最簡化版本（非遲滯性、線性版，論文中最簡單的那種），公式量級可以直接仿照現有 `urgency_pts`/`vulnerability_pts` 的設計方式（有上限、可解釋）。**成本**：改一個函數 + 一個設定常數，不需要新資料表（`created_at` 已存在）。

2. **同分 tie-breaking 規則明確化** —— 目前 `needs_with_vuln.sort()` 已經用 `(urgency, vulnerability, -created_at)` 排序，這其實已經是文獻中「懲罰基礎模型」的簡化實踐；可以在此基礎上，若要更貼近 D10（Gutjahr & Fischer）的公平性提醒，加一條規則：**同一批次中，若某資源被判給了「非最高脆弱度」的需求，且存在另一筆脆弱度明顯更高（例如高出一個等級）的候選需求同樣可以使用該資源，則優先讓給脆弱度更高者**——這其實就是現有貪婪演算法「已經在做」的事（因為排序已經把 vulnerability 排在 urgency 之後、created_at 之前），只需要**在管理員 UI 的候選預覽（`preview_candidates`）中明確標示「此建議是否曾經讓給更脆弱的需求」，增加可解釋性**，不需要改演算法本身。

3. **匈牙利演算法（Hungarian Algorithm）處理「批次同時派遣」**——這是本報告認為最值得投入的一項。
   目前 `auto_dispatch()` 是「依序處理每筆需求,每次都選當下最高分」的貪婪法,已知的理論弱點是：貪婪法不保證全域最優（先處理的需求可能搶走了對另一筆需求而言更關鍵的資源）。若同一批次（例如同一次 30 分鐘排程週期）有 N 筆待處理需求與 M 個候選資源,可以用 `scipy.optimize.linear_sum_assignment`（即匈牙利演算法的現成實作）對這一批次求解「總分數最大化」的全域最佳指派，取代目前「先到先贏」的貪婪處理順序。
   - **成本**：`scipy` 通常已是常見依賴（若專案還沒用，需加一個套件），成本函數矩陣就是目前 `_score()` 算出來的分數（負號轉換成最小化問題），核心邏輯改動大約是把 `for need in needs_with_vuln: ... best = max(cands)` 這段迴圈換成「先蒐集所有 (need, candidate) 配對分數 → 建立矩陣 → 呼叫 `linear_sum_assignment` → 依結果指派」。這是**確定可行、有現成函式庫、不需要新資料、一天內可完成**的改動，且有直接對應的文獻依據（Hungarian algorithm 在災害指派問題的應用，見第 6 節）。
   - **限制**：矩陣大小是 N×M，若候選數量成長到數千則會變慢，但本專案規模（單一社區）不會到那個量級。

4. **Rolling horizon 的「輕量預測」元素**（部分借鏡 A3）——目前 `auto_dispatch()` 已經是每 30 分鐘重跑、只處理當下的貪獃排程，這就是 rolling horizon 的核心精神（"only the decisions pertaining to the current period are implemented"）。A3 論文加了一個「狀態估計與預測模組」來預測未來需求，本專案可以做一個**極簡化版本**：在 `preview_candidates` 或後台儀表板加一個「近 N 小時需求量趨勢」的簡單統計（不是機器學習預測，只是移動平均），提醒管理員「目前物資消耗速度 vs 剩餘存量」，成本極低（一個 SQL 聚合查詢），但不宣稱這是「預測模型」。

5. **在管理員 UI 呈現「為什麼是這個分數」的分解**——`preview_candidates()` 已經回傳排序後的候選清單與分數，可以進一步把 `urgency_pts / vulnerability_pts / affinity_pts / dist_penalty / load_penalty` 五個子項分別列出（目前只回傳總分），讓管理員能看懂決策依據。這對應 D9/D10 文獻反覆強調的「決策要可稽核、可解釋」精神，成本是修改回傳的 dict 結構，幾乎零風險。

### 需要本專案目前不具備的基礎設施（明確不建議在 3 週內做）

1. **任何需要道路網圖資的方法**（Dijkstra/A*、VRP、道路搶修排程 B5/B6、路網脆弱性模型 A4）——本專案目前用 Haversine 直線距離，取得真實道路網圖（例如 OSM 路網 + 即時路況）並整合進距離計算，是一個獨立的資料工程專案，不是排程演算法的小改動。**明確不建議**在 3 週內嘗試,即使技術上 OSRM/OpenStreetMap 資料可取得，真正的價值在於「哪些路斷了」的即時災情資料，這在小型社區平台上根本沒有資料來源。

2. **傷患分級 + 救護車 + 醫院產能的整條鏈**（A4、Hungarian 醫生-病患指派、MDP/RL 救護車派遣）——本專案的核心是「物資媒合」，不是「大量傷患醫療後送」。要做這件事需要新增至少 3 個實體（Ambulance、Hospital、Victim/傷勢分級）與對應的資料來源（醫院即時床位資訊——這在台灣目前沒有公開即時 API），**技術上不可行也超出計畫定位**（依 MEMORY 中「鄰里守望平台參賽定位」，主軸是賑災物資,非醫療後送調度）。

3. **MILP/MINLP 求解器整合**（E12 WFP Optimus 等級）——即使技術上可以用開源求解器（如 PuLP + CBC）取代貪婪演算法，但這需要：(a) 把現有隱含的評分邏輯轉換成正式的目標函數與限制式（工程量不小,且貪婪法目前也還算可解釋、可除錯,轉換成 MILP 反而降低透明度）；(b) 求解器的授權/效能/除錯是新的技術風險點,在決賽前 3 週引入新求解器依賴是不必要的風險。**明確不建議**。

4. **任何隨機規劃/穩健最佳化/SAA/Benders/Column Generation**——這些方法的共同前提是「有一個正式的數學規劃模型 + 對不確定性的機率分布或範圍有估計」。本專案連基礎的正式模型都還沒有（目前是手工評分公式，不是從約束優化問題推導出來的），跳過這一步直接上這些進階求解技術是本末倒置。**明確不建議**。

5. **MDP/強化學習、圖神經網路**——這兩類方法的共同前提是「有大量歷史資料可供訓練」。本專案作為競賽原型，甚至可能沒有真實試辦資料（依 MEMORY「計畫書定稿狀態」，硬天花板正是「原型無試辦資料」），訓練資料量遠遠不足，**技術上不可行，不只是不建議**。

### 一句話總結
**現在就做**：等待時間懲罰項（1 行程式碼概念）、匈牙利演算法批次指派（scipy 現成函式、一天內可完成、有文獻依據）、分數分解透明化（UI 改動）。**不要碰**：任何要求道路網圖資、醫院/救護車實體、正式數學規劃模型、或機器學習訓練資料的方法——這些不是「來不及做」，而是本專案目前的資料基礎設施在架構上就不支援，勉強做出來也是空殼。

---

## 8. 統一架構提案

### 8.1 願景版（完整資金到位的正式運營系統）

```
輸入層
 ├─ 道路圖（節點=路口, 邊=路段, 屬性=長度/等級）
 ├─ 道路受損機率（每邊一個機率值，來自結構工程模型或人工回報）
 ├─ 旅行時間（正常 + 受損情境）
 ├─ 人口分布（含脆弱族群密度）
 ├─ 傷患數與嚴重度分布
 ├─ 醫院清單 + 即時容量（床位/科別）
 ├─ 避難所清單 + 容量
 ├─ 救援人力清單 + 技能標籤
 ├─ 車輛清單（救護車/卡車）+ 容量
 ├─ 倉庫清單 + 即時存貨
 └─ 預測需求（依人口/歷史/即時回報估計）

核心決策模組（依本報告文獻對應）
 ├─ 災前：Facility Location（D11/A1）→ 倉庫/避難所選址、預置存貨量
 ├─ 災前：Stochastic Programming（A1 第一階段）→ 車隊規模、預置決策
 ├─ 道路可通行性評估：GNN/STGNN（Nature STGNN+ARO 論文）→ 動態路況預測
 ├─ 路徑計算：Dijkstra/A*（基礎子程序）→ 供任何路徑相關模組呼叫
 ├─ 救護車→傷患：MDP/RL 或 MINLP（A4、Maxwell ADP）→ 即時派遣
 ├─ 傷患→醫院：Hungarian Algorithm / MILP（醫生-病患指派論文）→ 依容量與嚴重度指派
 ├─ 隊伍→任務：Facility Location + Assignment（C7 ACO 或 Hungarian）→ 大規模則用元啟發式
 ├─ 道路修復優先序：B5/B6 聯合排程模型 → 修復隊與配送車輛協同排程
 ├─ 卡車→物資→需求：Multi-Commodity Flow / MILP（E12 WFP Optimus 等級）→ 批次配送規劃
 ├─ 避難所物資量：Inventory-Allocation + Deprivation Cost（D8/D9/D10）→ 兼顧效率與公平
 └─ 全域重新規劃觸發：Rolling Horizon/MPC（A3）→ 每隔固定時間或事件觸發時重新求解

輸出層：誰去哪裡、救護車-病患配對、病患-醫院配對、隊伍-任務配對、
        道路修復優先序、卡車-物資配對、避難所供應量、路徑、重新優化觸發時機
```

這個願景版需要：專職 OR 工程師、即時路網 API、醫院即時容量資料介接、大規模運算基礎設施、以及最重要的——**真實歷史資料**來校正任何機率模型。這是縣市級或國家級（如 NCDR）的規模，不是單一社區平台的規模。

### 8.2 三週內學生競賽專案可實際借用的部分（誠實版）

對照上圖，本專案在 3 週內**唯一**能碰的，是「輸出層」中的**「卡車→物資→需求」這一小格**的簡化版，而且連這一格都只能借用概念、不能照搬方法：

| 願景版模組 | 本專案能做的簡化版 | 對應第 7 節建議 |
|---|---|---|
| 卡車→物資→需求 分配 | 用匈牙利演算法做「批次全域指派」取代逐筆貪婪 | 第 7 節第 3 項 |
| 避難所物資量（Deprivation Cost） | 加「等待時間懲罰」到現有分數公式 | 第 7 節第 1 項 |
| 全域重新規劃觸發（Rolling Horizon） | 已有（30 分鐘排程），可加簡單需求趨勢統計 | 第 7 節第 4 項 |
| 決策可解釋性（呼應 Palantir Ontology「人類與 AI 都能理解的表示法」精神） | 分數分解透明化 | 第 7 節第 5 項 |
| 其餘所有模組（道路圖、GNN、MDP/RL、醫院/救護車指派、選址、Benders、SAA…） | **明確不做**，資料基礎設施不存在，勉強做是空殼展演 | 第 7 節「不建議採用」清單 |

**對決賽展演的建議定位**：與其在海報/展演中聲稱採用了某個聽起來很厲害的演算法名稱（如「強化學習」「圖神經網路」），不如誠實呈現「我們的貪婪評分法在文獻中對應到 XX 類方法（priority-based / penalty-based heuristic），並且我們額外加入了學術文獻建議的『等待時間懲罰』與『批次全域指派』兩項具體改良，這兩項改良都可以指出對應的論文依據（D8/D9 剝奪成本理論；Hungarian algorithm 指派問題文獻）」——這是一個誠實、可驗證、評審若追問細節也站得住腳的說法，而不是「別瞎編」原則下應該避免的、聽起來厲害但經不起追問的宣稱。

---

## 附錄：本次研究涉及的實際檔案

- 本專案現有派遣邏輯：`D:\Smart_Emergency\app\services\dispatch.py`（已讀取全文，第 7、8 節建議均基於此檔案實際程式碼結構）
- D8 論文全文 PDF（已讀取）：本地暫存於 WebFetch 快取（可重新以 DOI `10.1287/trsc.2014.0565` 或 Bilkent 課程網站連結取得）
- E12 論文全文 PDF（已讀取）：本地暫存於 WebFetch 快取（可重新以 optimization-online.org 工作論文編號 7198 或 DOI `10.1287/ijoo.2019.0047` 取得）
