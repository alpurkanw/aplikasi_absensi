import sqlite3
import requests
import time
import threading
from datetime import datetime
import random
import uuid
import logging
import sys


# API_URL = "http://127.0.0.1/devel/payroll_app/api/v1/presensi/upload"

# API_URL = "http://10.0.80.173/devel/payroll_app/api/v1/presensi/upload"

# payload = {
#     "user_id": user_id,
#     "timestamp": timestamp,
#     "device_sn": device_sn,
#     "template_hash": template_hash,
#     "client_event_id": client_event_id,
# }

# Configuration
API_URL = "http://127.0.0.1/devel/payroll_app/api/v1/presensi/upload"
API_TOKEN = "change-this-token"
USE_EMULATOR = True  # Ubah ke False setelah driver dan SDK asli terintegrasi

logging.basicConfig(
    filename="agent_error.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    encoding="utf-8"
)
logger = logging.getLogger("hcis_agent")


def log_uncaught_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = log_uncaught_exception

# -------------------------------------------------------------------
# 1. AGENT CORE: DATABASE & SYNC ENGINE
# -------------------------------------------------------------------
class AgentEngine:
    def __init__(self, db_path="attendance_offline.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Membuat tabel lokal untuk menampung presensi jika belum ada."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS log_presensi (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    device_sn TEXT NOT NULL,
                    template_hash TEXT,
                    client_event_id TEXT UNIQUE,
                    is_uploaded INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            columns = {row[1] for row in cursor.execute("PRAGMA table_info(log_presensi)")}
            if "client_event_id" not in columns:
                cursor.execute("ALTER TABLE log_presensi ADD COLUMN client_event_id TEXT")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_log_presensi_event_id ON log_presensi(client_event_id)")
            cursor.execute("SELECT id FROM log_presensi WHERE client_event_id IS NULL")
            for (log_id,) in cursor.fetchall():
                cursor.execute(
                    "UPDATE log_presensi SET client_event_id = ? WHERE id = ?",
                    (str(uuid.uuid4()), log_id)
                )
            conn.commit()

    def save_log(self, user_id, timestamp, device_sn, template_hash=""):
        """Menyimpan hasil scan sidik jari ke SQLite lokal."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO log_presensi
                    (user_id, timestamp, device_sn, template_hash, client_event_id, is_uploaded)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (user_id, timestamp, device_sn, template_hash, str(uuid.uuid4())))
            conn.commit()
            print(f"  [SQLite] Saved: {user_id} @ {timestamp} (Status: Unuploaded)")

    def sync_to_server(self):
        """Mengecek data unuploaded di SQLite lalu hit ke REST API."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Ambil maksimal 10 data teratas yang belum ter-upload
            cursor.execute("""
                SELECT id, user_id, timestamp, device_sn, template_hash, client_event_id
                FROM log_presensi
                WHERE is_uploaded = 0
                ORDER BY id
                LIMIT 10
            """)
            rows = cursor.fetchall()

            if not rows:
                return

            print(f"\n[Sync Worker] Menemukan {len(rows)} data pending. Mencoba upload...")

            for row in rows:
                log_id, user_id, timestamp, device_sn, template_hash, client_event_id = row
                payload = {
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "device_sn": device_sn,
                    "template_hash": template_hash,
                    "client_event_id": client_event_id
                }
                headers = {
                    "Authorization": f"Bearer {API_TOKEN}",
                    "Content-Type": "application/json"
                }

                try:
                    # Hit REST API Server
                    response = requests.post(API_URL, json=payload, headers=headers, timeout=5)

                    if response.status_code in [200, 201]:
                        # Update status jika berhasil
                        cursor.execute("UPDATE log_presensi SET is_uploaded = 1 WHERE id = ?", (log_id,))
                        conn.commit()
                        print(f"  [HTTP 200] SUCCESS Upload ID: {log_id} ({user_id})")
                    else:
                        response_body = response.text[:2000]
                        logger.error(
                            "Upload failed: local_id=%s user_id=%s status=%s url=%s response=%s",
                            log_id,
                            user_id,
                            response.status_code,
                            API_URL,
                            response_body
                        )
                        print(f"  [HTTP {response.status_code}] Failed Upload ID: {log_id}. Server Error.")
                
                except requests.exceptions.RequestException as e:
                    logger.exception(
                        "Request error while uploading local_id=%s user_id=%s url=%s",
                        log_id,
                        user_id,
                        API_URL
                    )
                    print(f"  [Offline/Error] Tidak dapat terhubung ke server: {e}")
                    break  # Hentikan loop sync jika koneksi terputus

    def start_background_sync(self, interval_seconds=10):
        """Thread terpisah untuk memeriksa dan mengunggah data secara berkala."""
        def run():
            while True:
                try:
                    self.sync_to_server()
                except Exception as err:
                    logger.exception("Sync worker error")
                    print(f"[Sync Thread Error] {err}")
                time.sleep(interval_seconds)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()


# -------------------------------------------------------------------
# 2. EMULATOR DRIVER (Simulasi U.are.U 4500)
# -------------------------------------------------------------------
class FingerprintEmulator:
    def __init__(self, agent_engine):
        self.agent = agent_engine
        self.dummy_users = ["EMP-1001", "EMP-1002", "EMP-1003", "EMP-1004"]

    def start_cli_listener(self):
        print("\n==================================================")
        print("     EMULATOR FINGERPRINT U.are.U 4500 READY      ")
        print(" Tekan [ENTER] untuk simulasi scan sidik jari.")
        print(" Ketik 'exit' lalu Enter untuk menghentikan.")
        print("==================================================\n")

        while True:
            cmd = input(">>> Press Enter to Scan Finger... ")
            if cmd.strip().lower() == "exit":
                print("Exiting emulator...")
                break

            # Simulasi penangkapan data dari alat
            scanned_user = random.choice(self.dummy_users)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mock_sn = "U4500-SIMULATOR-001"
            mock_hash = str(uuid.uuid4())

            print(f"\n[Event Fingerprint] Jari terdeteksi!")
            # Teruskan ke Agent Engine untuk disimpan di SQLite
            self.agent.save_log(scanned_user, now_str, mock_sn, mock_hash)


# -------------------------------------------------------------------
# 3. ENTRY POINT PROGRAM
# -------------------------------------------------------------------
if __name__ == "__main__":
    # Inisialisasi Agent
    agent = AgentEngine()

    # Jalankan sync worker di latar belakang (cek SQLite tiap 5 detik)
    agent.start_background_sync(interval_seconds=5)

    if USE_EMULATOR:
        # Jalankan Emulator interaktif
        emulator = FingerprintEmulator(agent)
        emulator.start_cli_listener()
    else:
        # Tempat pemanggilan SDK C/C++ DigitalPersona U.are.U asli nantinya
        pass