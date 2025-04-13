# 透過藍牙與 WebSocket 以陀螺儀控制的小精靈 (Pac-Man)

本專案展示如何使用智慧型手機的陀螺儀感測器來控制經典的 Pac-Man 網頁遊戲。感測器數據透過藍牙無線傳輸至 Python 後端伺服器，伺服器再利用 WebSocket 技術即時將控制指令轉發給網頁前端遊戲。同時，陀螺儀數據也會被記錄到 MySQL 資料庫中。

此專案以前端的 [Pacman Canvas HTML5](https://github.com/platzhersh/pacman-canvas) 遊戲為基礎進行修改。
![image](https://github.com/user-attachments/assets/214cdc62-77a5-49be-821c-4a5aa0889cd5)

## 主要功能
* **即時遊戲控制：** 使用手機的物理傾斜來導航 Pac-Man。
* **無線感測器串流：** 使用藍牙 RFCOMM 從行動裝置傳輸陀螺儀數據。
* **低延遲更新：** 採用 WebSocket 技術，實現後端到前端遊戲的即時指令中繼。
* **異步後端：** 使用 Python 的 `asyncio` 處理 WebSocket，並透過 `threading` 處理藍牙和資料庫 I/O。
* **數據持久化：** 將帶有時間戳的陀螺儀數據 (X, Y, Z) 記錄到 MySQL 資料庫。
* **安全資料庫連線：** 利用 SSH Tunnel (`sshtunnel`) 安全地連接到遠端資料庫。
* **動態建立資料表：** 每次執行時，自動為該次的工作階段(Session)數據建立一個帶有時間戳的新資料表。
* **優化資料庫寫入：** 實作批次插入 (Batch Insertion) 以提升資料庫效能。


## 系統架構

系統的數據流如下：

1.  **手機陀螺儀** -> **手機配套App**
2.  **手機配套App** -> (藍牙 RFCOMM) -> **Python 後端**
3.  **Python 後端** 處理數據並分流至：
    * -> (WebSocket) -> **前端遊戲 (index.htm)** -> (模擬鍵盤事件) -> **Pac-Man 遊戲邏輯**
    * -> (SSH 通道 & 批次插入) -> **MySQL 資料庫**


##  安裝環境與設定環境變數

安裝 requirement.txt
設定.env

##  執行伺服器:
Bash
python server.py
伺服器啟動後會嘗試建立資料庫表格，並開始監聽藍牙 (Channel 4) 和 WebSocket (Port 8765) 連接。
確保防火牆允許相關的連接埠和藍牙通訊。
##  前端設定 (index.htm):
託管檔案: 使用任何網頁伺服器提供專案中的 HTML/CSS/JS 檔案，或直接在瀏覽器中開啟 index.htm。
設定 WebSocket 位址:
編輯 index.htm。
找到檔案末尾 <script> 標籤內的 WS_SERVER 常數。
將其值修改為後端 server.py 正在運行的 實際 IP 位址或主機名 以及 Port (預設是 ws://<你的後端IP>:8765)。

##  關於原始 Pac-Man Canvas
本專案的遊戲前端基於 Pacman Canvas 進行修改。感謝原作者的貢獻。


授權條款 (License)
本專案可能繼承自原始 Pacman Canvas 的授權條款。原始作品使用 Creative Commons Attribution-ShareAlike 4.0 International License (姓名標示-相同方式分享 4.0 國際)。請遵守此授權條款。

<a rel="license" href="http://creativecommons.org/licenses/by-sa/4.0/"><img alt="Creative Commons License" style="border-width:0" src="https://i.creativecommons.org/l/by-sa/4.0/88x31.png" /></a>   

致謝
感謝 Platzh1rsch 開發了原始的 Pacman Canvas 遊戲。
