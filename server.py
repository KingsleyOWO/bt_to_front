import asyncio
import json
import threading
from queue import Queue
from bluetooth import BluetoothSocket, RFCOMM
from websockets.server import serve
from sshtunnel import SSHTunnelForwarder
import pymysql
from datetime import datetime
import os

BLUETOOTH_CHANNEL = 4
WEBSOCKET_HOST = "0.0.0.0"
WEBSOCKET_PORT = 8765
DB_BATCH_SIZE = 90

broadcast_queue = Queue()
db_queue = Queue()
clients = set()
table_name = None

SSH_HOST = os.environ.get('SSH_HOST', 'XXXXXX')
SSH_PORT = int(os.environ.get('SSH_PORT', '22'))
SSH_USERNAME = os.environ.get('SSH_USERNAME', 'your_ssh_username')
SSH_PASSWORD = os.environ.get('SSH_PASSWORD', 'your_ssh_password')
DB_REMOTE_HOST = os.environ.get('DB_REMOTE_HOST', 'your_db_ip')
DB_REMOTE_PORT = 3306
DB_USER = os.environ.get('DB_USER', 'your_db_user')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'your_db_password')
DB_NAME = os.environ.get('DB_NAME', 'your_db_name')

def create_dynamic_table():
    global table_name
    table_name = "gyro_data_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    print("嘗試建立 SSH 隧道...")
    try:
        with SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USERNAME,
            ssh_password=SSH_PASSWORD,
            remote_bind_address=(DB_REMOTE_HOST, DB_REMOTE_PORT)
        ) as tunnel:
            print(f"SSH 隧道建立成功，本地綁定 Port: {tunnel.local_bind_port}")
            conn = None
            cursor = None
            try:
                conn = pymysql.connect(
                    host='127.0.0.1',
                    port=tunnel.local_bind_port,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    db=DB_NAME,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
                cursor = conn.cursor()
                create_table_sql = f"""
                CREATE TABLE IF NOT EXISTS `{table_name}` (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp BIGINT NOT NULL COMMENT '時間戳 (毫秒)',
                    x FLOAT NOT NULL COMMENT 'X 軸角速度',
                    y FLOAT NOT NULL COMMENT 'Y 軸角速度',
                    z FLOAT NOT NULL COMMENT 'Z 軸角速度',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '紀錄創建時間'
                );
                """
                print(f"執行 SQL: CREATE TABLE IF NOT EXISTS `{table_name}` ...")
                cursor.execute(create_table_sql)
                conn.commit()
                print(f"資料表 `{table_name}` 建立或確認存在成功！")
            except pymysql.MySQLError as db_err:
                print(f"資料庫操作失敗: {db_err}")
            except Exception as e:
                 print(f"建立資料表時發生未預期錯誤: {e}")
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
                print("資料庫連線已關閉")
    except Exception as tunnel_err:
        print(f"建立 SSH 隧道失敗: {tunnel_err}")
        exit(1)
    return table_name

def bluetooth_thread():
    server_sock = None
    client_sock = None
    try:
        server_sock = BluetoothSocket(RFCOMM)
        server_sock.bind(("", BLUETOOTH_CHANNEL))
        server_sock.listen(1)
        print(f"藍牙 RFCOMM 伺服器啟動，監聽通道 {BLUETOOTH_CHANNEL}...")
        print("等待藍牙客戶端連接...")
        client_sock, client_info = server_sock.accept()
        print(f"藍牙已連接: {client_info}")
        buffer = ""
        while True:
            try:
                data = client_sock.recv(1024)
                if not data:
                    print("藍牙連接已由客戶端關閉。")
                    break
                buffer += data.decode('utf-8', errors='ignore')
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            json_data = json.loads(line)
                            if all(k in json_data for k in ('timestamp', 'x', 'y', 'z')):
                                print(f"接收到有效 JSON: {json_data}")
                                broadcast_queue.put(json_data)
                                db_queue.put(json_data)
                            else:
                                print(f"JSON 數據缺少必要欄位: {line}")
                        except json.JSONDecodeError:
                            print(f"接收到無效的 JSON 格式數據: {line}")
            except IOError as e:
                print(f"藍牙接收 IO 錯誤: {e}")
                break
    except OSError as e:
        print(f"藍牙伺服器綁定通道 {BLUETOOTH_CHANNEL} 失敗: {e}")
        exit(1)
    except Exception as e:
        print(f"藍牙線程發生未預期錯誤: {e}")
    finally:
        if client_sock:
            print("關閉藍牙客戶端 socket...")
            client_sock.close()
        if server_sock:
            print("關閉藍牙伺服器 socket...")
            server_sock.close()
        print("藍牙線程結束。")

async def websocket_handler(websocket):
    print(f"WebSocket 客戶端連接: {websocket.remote_address}")
    clients.add(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                print(f"收到來自 {websocket.remote_address} 的訊息: {data}")
            except json.JSONDecodeError:
                print(f"收到來自 {websocket.remote_address} 的無效 JSON 訊息: {message}")
            except Exception as e:
                 print(f"處理來自 {websocket.remote_address} 的訊息時出錯: {e}")
    except Exception as e:
         print(f"WebSocket 連接錯誤 ({websocket.remote_address}): {e}")
    finally:
        print(f"WebSocket 客戶端斷開連接: {websocket.remote_address}")
        clients.remove(websocket)

async def broadcast_data():
    print("啟動 WebSocket 廣播任務...")
    while True:
        try:
            if not broadcast_queue.empty():
                data = broadcast_queue.get_nowait()
                if clients:
                    message = json.dumps({
                        'type': 'gyro',
                        'x': data.get('x'),
                        'y': data.get('y'),
                        'z': data.get('z')
                    })
                    disconnected_clients = set()
                    tasks = []
                    for client in clients:
                       try:
                           tasks.append(client.send(message))
                       except Exception:
                           disconnected_clients.add(client)
                    if tasks:
                         await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0.05)
        except asyncio.QueueEmpty:
             await asyncio.sleep(0.05)
        except Exception as e:
            print(f"廣播任務發生錯誤: {e}")
            await asyncio.sleep(1)

def db_insertion_thread():
    print("啟動資料庫插入線程...")
    buffer = []
    try:
        with SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USERNAME,
            ssh_password=SSH_PASSWORD,
            remote_bind_address=(DB_REMOTE_HOST, DB_REMOTE_PORT)
        ) as tunnel:
            print(f"資料庫線程：SSH 隧道建立成功，本地 Port: {tunnel.local_bind_port}")
            conn = None
            cursor = None
            try:
                conn = pymysql.connect(
                    host='127.0.0.1',
                    port=tunnel.local_bind_port,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    db=DB_NAME,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
                cursor = conn.cursor()
                print("資料庫線程：資料庫連接成功。")
                while True:
                    try:
                        data = db_queue.get()
                        if table_name is None:
                            print("錯誤：table_name 尚未設定，無法插入數據。")
                            db_queue.task_done()
                            continue
                        timestamp = data.get('timestamp')
                        x = data.get('x')
                        y = data.get('y')
                        z = data.get('z')
                        if None in [timestamp, x, y, z]:
                             print(f"警告：收到的數據缺少欄位，跳過插入: {data}")
                             db_queue.task_done()
                             continue
                        buffer.append((timestamp, x, y, z))
                        if len(buffer) >= DB_BATCH_SIZE:
                            if table_name:
                                sql = f"INSERT INTO `{table_name}` (timestamp, x, y, z) VALUES (%s, %s, %s, %s)"
                                try:
                                    affected_rows = cursor.executemany(sql, buffer)
                                    conn.commit()
                                    print(f"成功批次插入 {affected_rows} / {len(buffer)} 筆資料到 `{table_name}`")
                                    buffer = []
                                except pymysql.MySQLError as db_err:
                                    print(f"批次資料庫插入失敗: {db_err}")
                                    conn.rollback()
                                except Exception as e:
                                    print(f"批次插入時發生未預期錯誤: {e}")
                                    conn.rollback()
                            else:
                                 print("錯誤：table_name 為空，無法執行插入。")
                                 buffer = []
                        db_queue.task_done()
                    except Exception as loop_err:
                         print(f"資料庫線程內部循環錯誤: {loop_err}")
                         db_queue.task_done()
                         break
            except pymysql.MySQLError as db_conn_err:
                print(f"資料庫線程：資料庫連接失敗: {db_conn_err}")
            except Exception as e:
                print(f"資料庫線程：發生未預期錯誤: {e}")
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
                print("資料庫線程：資料庫連接已關閉。")
    except Exception as tunnel_err:
        print(f"資料庫線程：建立 SSH 隧道失敗: {tunnel_err}")
    print("資料庫插入線程結束。")

async def main():
    if table_name is None:
        print("錯誤：無法獲取資料表名稱，程式即將退出。")
        return
    server = await serve(websocket_handler, WEBSOCKET_HOST, WEBSOCKET_PORT)
    print(f"WebSocket 伺服器啟動，監聽於 {WEBSOCKET_HOST}:{WEBSOCKET_PORT}...")
    broadcast_task = asyncio.create_task(broadcast_data())
    try:
        await server.wait_closed()
    finally:
        print("WebSocket 伺服器正在關閉...")
        broadcast_task.cancel()
        try:
            await broadcast_task
        except asyncio.CancelledError:
            print("廣播任務已取消。")

if __name__ == "__main__":
    print("程式啟動...")
    create_dynamic_table()
    if table_name is None:
        print("錯誤：未能成功建立或確認資料表，無法啟動服務。")
        exit(1)
    print("啟動藍牙接收線程...")
    bt_thread = threading.Thread(target=bluetooth_thread, daemon=True)
    bt_thread.start()
    print("啟動資料庫插入線程...")
    db_thread = threading.Thread(target=db_insertion_thread, daemon=True)
    db_thread.start()
    try:
        print("啟動 asyncio 事件循環和 WebSocket 服務...")
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，程式準備結束...")
    finally:
        print("程式執行完畢。")
