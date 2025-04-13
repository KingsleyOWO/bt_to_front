import asyncio
import json
import threading
from queue import Queue
from bluetooth import BluetoothSocket, RFCOMM
from websockets import serve
from sshtunnel import SSHTunnelForwarder
import pymysql
from datetime import datetime
# 全域隊列與 WebSocket 連線集合
broadcast_queue = Queue()
db_queue = Queue()
clients = set()
# 全域變數，用來保存動態建立的資料表名稱
table_name = None
# 自動建立動態命名的資料表
def create_dynamic_table():
    """
    每次啟動程式時，自動建立一個以當前時間命名的資料表，
    並返回該資料表的名稱。
    """
    dynamic_table = "gyro_data_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 建立 SSH 隧道：連線到外網的爬蟲機 dv107，
    # 並在 dv107 上轉發連線到內網資料庫 labdb (埠3306)
    with SSHTunnelForwarder(
        ('dv107.coded2.fun', 8022),
        ssh_username='kingsley',
        ssh_password='ji394djp4',
        remote_bind_address=('labdb.coded2.fun', 3306)
    ) as tunnel:
        # 隧道啟動後，連線到本機的 tunnel.local_bind_port
        conn = pymysql.connect(
            host='127.0.0.1',
            port=tunnel.local_bind_port,
            user='kingsley',         # 請替換成資料庫帳號（若不同）
            password='ji394djp4',     # 請替換成資料庫密碼（若不同）
            db='KINGSLEY'             # 資料庫名稱，請依實際情況修改
        )
        cursor = conn.cursor()
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS `{dynamic_table}` (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp BIGINT NOT NULL,
            x FLOAT NOT NULL,
            y FLOAT NOT NULL,
            z FLOAT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        try:
            cursor.execute(create_table_sql)
            conn.commit()
            print(f"資料表 {dynamic_table} 建立成功！")
        except Exception as e:
            print("建立資料表失敗:", e)
        finally:
            cursor.close()
            conn.close()
    
    return dynamic_table

# 藍牙接收線程
def bluetooth_thread():
    """
    建立藍牙 RFCOMM 伺服器，等待藍牙連線，
    接收資料後解析為 JSON，並同時放入廣播與資料庫隊列中
    """
    server_sock = BluetoothSocket(RFCOMM)
    try:
        # 直接指定通道 4
        server_sock.bind(("", 4))
        port = 4
        print(f"綁定成功，使用通道 {port}")
    except OSError as e:
        print(f"綁定通道 4 失敗：{e}")
        exit()
    server_sock.listen(1)
    
    print("等待藍牙連接...")
    client_sock, client_info = server_sock.accept()
    print("已連接:", client_info)
    
    try:
        buffer = ""
        while True:
            data = client_sock.recv(1024)
            if not data:
                break
            # 將收到的資料累積到 buffer 中（因可能分段傳送）
            buffer += data.decode('utf-8', errors='ignore')
            # 利用換行符號拆解多筆 JSON 資料
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        json_data = json.loads(line)
                        print("接收到 JSON:", json_data)
                        # 分流至廣播與資料庫隊列
                        broadcast_queue.put(json_data)
                        db_queue.put(json_data)
                    except json.JSONDecodeError:
                        print(f"無效的 JSON 數據: {line}")
    finally:
        client_sock.close()
        server_sock.close()


# WebSocket 連線處理

async def websocket_handler(websocket):
    """
    當 WebSocket 客戶端連線時，
    將其加入全域 clients 集合，並監聽訊息（可依需求進行控制邏輯）
    """
    clients.add(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if data.get('type') == 'gyro':
                    print(f"接收到陀螺儀數據: {data}")
                    # 此處可根據需求加入額外控制邏輯
            except json.JSONDecodeError as e:
                print(f"Error decoding message: {e}")
    finally:
        clients.remove(websocket)


# WebSocket 廣播任務

async def broadcast_data():
    """
    從 broadcast_queue 讀取資料，並將 JSON 資料包裝後廣播給所有連線的 WebSocket 客戶端
    """
    while True:
        if not broadcast_queue.empty():
            data = broadcast_queue.get()
            if clients:
                message = json.dumps({
                    'type': 'gyro',
                    'x': data['x'],
                    'y': data['y'],
                    'z': data['z']
                })
                await asyncio.gather(*[client.send(message) for client in clients])
        await asyncio.sleep(0.1)


# 資料庫插入線程 (批次插入)

def db_insertion_thread():
    """
    透過 SSH 隧道連上 labdb，
    將 db_queue 中的資料累積成批，達到一定數量後進行批次插入
    """
    BATCH_SIZE = 90  # 設定每次批次插入 90 筆資料
    buffer = []      # 用來暫存累積的資料
    
    # 建立 SSH 隧道（從 dv107 連線至 labdb）
    with SSHTunnelForwarder(
        ('dv107.coded2.fun', 8022),
        ssh_username='kingsley',
        ssh_password='ji394djp4',
        remote_bind_address=('labdb.coded2.fun', 3306)
    ) as tunnel:
        conn = pymysql.connect(
            host='127.0.0.1',
            port=tunnel.local_bind_port,
            user='kingsley',       # 請替換成資料庫使用者名稱
            password='ji394djp4',   # 請替換成資料庫密碼
            db='KINGSLEY'
        )
        cursor = conn.cursor()
        while True:
            data = db_queue.get()  # 阻塞式取得下一筆資料
            buffer.append((data['timestamp'], data['x'], data['y'], data['z']))
            if len(buffer) >= BATCH_SIZE:
                sql = f"INSERT INTO `{table_name}` (timestamp, x, y, z) VALUES (%s, %s, %s, %s)"
                try:
                    cursor.executemany(sql, buffer)
                    conn.commit()
                    print(f"批次插入 {len(buffer)} 筆資料")
                except Exception as e:
                    print("批次 DB 插入失敗:", e)
                buffer = []
        # 注意：此 while 迴圈永不退出，程式結束時隧道會自動關閉
        # cursor.close()
        # conn.close()


# 主程式（啟動 WebSocket 服務）

async def main():
    """
    使用 asyncio 啟動 WebSocket 服務，並同時啟動 broadcast_data 任務，
    使程式持續運行
    """
    async with serve(websocket_handler, "0.0.0.0", 8765):
        asyncio.create_task(broadcast_data())
        await asyncio.Future()  # 永遠等待
# 程式進入點
if __name__ == "__main__":
    # 每次啟動程式時，自動建立一個動態命名的資料表
    table_name = create_dynamic_table()

    # 啟動藍牙接收線程
    bt_thread = threading.Thread(target=bluetooth_thread)
    bt_thread.daemon = True
    bt_thread.start()

    # 啟動資料庫插入線程 (批次插入)
    db_thread = threading.Thread(target=db_insertion_thread)
    db_thread.daemon = True
    db_thread.start()

    # 啟動 WebSocket 服務
    asyncio.run(main())
