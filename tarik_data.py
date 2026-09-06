import base64
import json
import os
import sqlite3
import requests
import time
import threading
from datetime import datetime
import random
import uuid
import logging
import sys
from pathlib import Path


# payload = {
#     "user_id": user_id,
#     "timestamp": timestamp,
#     "device_sn": device_sn,
#     "template_hash": template_hash,
#     "client_event_id": client_event_id,
# }

DEFAULT_CONFIG = {
    "api_base_url": "http://127.0.0.1/devel/svr_aplikasi_absensi",
    "api_token": "change-this-token",
    "use_emulator": True,
    "device_merk": "U.are.U 4500",
    "device_sn": "U4500-SIMULATOR-001",
    "request_timeout_seconds": 5,
}


def load_config():
    config_path = Path(os.environ.get("HCIS_CONFIG_PATH", Path(__file__).with_name("config_client.json")))
    if not config_path.exists():
        return DEFAULT_CONFIG.copy()
    with config_path.open("r", encoding="utf-8") as config_file:
        configured = json.load(config_file)
    if not isinstance(configured, dict):
        raise RuntimeError("File config HCIS harus berisi object JSON")
    config = DEFAULT_CONFIG.copy()
    config.update(configured)
    if not isinstance(config["api_base_url"], str) or not config["api_base_url"].strip():
        raise RuntimeError("api_base_url pada config HCIS wajib diisi")
    if not isinstance(config["api_token"], str) or not config["api_token"].strip():
        raise RuntimeError("api_token pada config HCIS wajib diisi")
    return config


CONFIG = load_config()
API_BASE_URL = CONFIG["api_base_url"].rstrip("/")
API_URL = API_BASE_URL + "/api/v1/presensi/upload"
EMPLOYEES_URL = API_BASE_URL + "/api/v1/employees"
FINGERPRINT_TEMPLATES_URL = API_BASE_URL + "/api/v1/fingerprint-templates"
API_TOKEN = CONFIG["api_token"]
USE_EMULATOR = bool(CONFIG["use_emulator"])
DEVICE_MERK = str(CONFIG["device_merk"])
DEVICE_SN = str(CONFIG["device_sn"])
REQUEST_TIMEOUT = int(CONFIG["request_timeout_seconds"])


def api_headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def fetch_fingerprint_templates():
    response = requests.get(FINGERPRINT_TEMPLATES_URL, headers=api_headers(), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    body = json.loads(response.content.decode("utf-8-sig"))
    if not body.get("success") or not isinstance(body.get("data"), list):
        raise RuntimeError("Respons template fingerprint dari server tidak valid")
    return body["data"]


def fetch_employees():
    response = requests.get(EMPLOYEES_URL, headers=api_headers(), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    body = json.loads(response.content.decode("utf-8-sig"))
    if not body.get("success") or not isinstance(body.get("data"), list):
        raise RuntimeError("Respons data karyawan dari server tidak valid")
    return body["data"]


def fetch_client_config():
    response = requests.get(API_BASE_URL + "/api/v1/client-config", headers=api_headers(), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    body = json.loads(response.content.decode("utf-8-sig"))
    if not body.get("success") or not isinstance(body.get("data"), dict):
        raise RuntimeError("Respons konfigurasi client dari server tidak valid")
    return body["data"]


def check_web_connection(timeout=None):
    timeout = REQUEST_TIMEOUT if timeout is None else timeout
    try:
        response = requests.get(API_BASE_URL + "/health", timeout=timeout)
        status_code = response.status_code
        details = getattr(response, "text", "HTTP status " + str(status_code))
        return 200 <= status_code < 400, status_code, details
    except requests.exceptions.RequestException as error:
        return False, None, str(error)


def upload_fingerprint_template(employee_code, finger_slot, template_blob):
    response = requests.post(
        FINGERPRINT_TEMPLATES_URL + "/save",
        json={
            "employee_code": employee_code,
            "finger_slot": finger_slot,
            "template_base64": base64.b64encode(template_blob).decode("ascii"),
            "device_sn": DEVICE_SN,
        },
        headers={**api_headers(), "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(body.get("message", "Template fingerprint gagal disimpan"))
    return body["data"]


def delete_fingerprint_templates(employee_code):
    response = requests.post(
        FINGERPRINT_TEMPLATES_URL + "/delete",
        json={"employee_code": employee_code},
        headers={**api_headers(), "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(body.get("message", "Template fingerprint gagal dihapus"))

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
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = str(Path(__file__).with_name("attendance_offline.db"))
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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employee_directory (
                    employee_code TEXT PRIMARY KEY,
                    employee_name TEXT NOT NULL DEFAULT '',
                    position_name TEXT,
                    department_name TEXT,
                    updated_at TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employee_mappings (
                    employee_code TEXT NOT NULL,
                    fingerprint_user_id TEXT NOT NULL,
                    device_sn TEXT,
                    PRIMARY KEY (employee_code, fingerprint_user_id, device_sn)
                )
            """)
            conn.commit()

    def sync_employees(self):
        """Mengunduh master karyawan aktif dan mapping fingerprint dari server."""
        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        response = requests.get(EMPLOYEES_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        body = json.loads(response.content.decode("utf-8-sig"))
        if not body.get("success") or not isinstance(body.get("data"), list):
            raise RuntimeError("Respons master karyawan dari server tidak valid")

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO employee_directory
                    (employee_code, employee_name, position_name, department_name, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(employee_code) DO UPDATE SET
                    employee_name = excluded.employee_name,
                    position_name = excluded.position_name,
                    department_name = excluded.department_name,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        employee["employee_code"],
                        employee.get("name", ""),
                        employee.get("position_name"),
                        employee.get("department_name"),
                        employee.get("updated_at"),
                    )
                    for employee in body["data"]
                    if employee.get("employee_code")
                ],
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO employee_mappings
                    (employee_code, fingerprint_user_id, device_sn)
                VALUES (?, ?, ?)
                """,
                [
                    (
                        employee["employee_code"],
                        mapping["fingerprint_user_id"],
                        mapping.get("device_sn"),
                    )
                    for employee in body["data"]
                    for mapping in employee.get("mappings", [])
                    if mapping.get("fingerprint_user_id")
                ],
            )
            conn.commit()
        return len(body["data"])

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

    def pending_log_count(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM log_presensi WHERE is_uploaded = 0"
            ).fetchone()[0]

    def sync_to_server(self, on_error=None):
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
                    response = requests.post(API_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)

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
                    if on_error:
                        on_error(str(e))
                    break  # Hentikan loop sync jika koneksi terputus

    def sync_all_pending_to_server(self, on_error=None):
        uploaded = 0
        while self.pending_log_count():
            before = self.pending_log_count()
            self.sync_to_server(on_error=on_error)
            after = self.pending_log_count()
            uploaded += max(0, before - after)
            if after >= before:
                break
        return uploaded, self.pending_log_count()

    def start_background_sync(self, interval_seconds=10, on_error=None, on_complete=None):
        """Thread terpisah untuk memeriksa dan mengunggah data secara berkala."""
        def run():
            while True:
                try:
                    self.sync_to_server(on_error=on_error)
                    if on_complete:
                        on_complete(self.pending_log_count())
                except Exception as err:
                    logger.exception("Sync worker error")
                    print(f"[Sync Thread Error] {err}")
                    if on_error:
                        on_error(str(err))
                    if on_complete:
                        on_complete(self.pending_log_count())
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