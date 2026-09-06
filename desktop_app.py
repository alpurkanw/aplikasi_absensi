import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QHeaderView,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from capture_agent import FingerprintCaptureAgent
from enrollment_service import EnrollmentService
from tarik_data import AgentEngine, DEVICE_MERK, fetch_client_config, fetch_fingerprint_templates


class CaptureWorker(QThread):
    event_received = Signal(str)
    failed = Signal(str)

    def __init__(self, agent_engine, templates=None):
        super().__init__()
        self.agent_engine = agent_engine
        self.agent = None
        self.verification = EnrollmentService()
        self.templates = templates or {}

    def on_sample(self, sample):
        for employee_id, employee_templates in self.templates.items():
            for employee_name, template_blob in employee_templates:
                if self.verification.verify_sample(template_blob, sample):
                    self.agent_engine.save_log(
                        employee_id,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        DEVICE_MERK,
                        employee_name or employee_id,
                    )
                    self.event_received.emit(f"[OK] Fingerprint matched: {employee_id}")
                    return
        self.event_received.emit("[WARN] Fingerprint tidak cocok dengan template terdaftar")

    def run(self):
        try:
            self.agent = FingerprintCaptureAgent(
                on_event=self.event_received.emit,
                on_sample=self.on_sample,
            )
            self.agent.run()
        except Exception as error:
            self.failed.emit(str(error))

    def stop_capture(self):
        if self.agent:
            self.agent.stop()


class EnrollmentWorker(QThread):
    event_received = Signal(str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, employee_id, employee_name="", finger_slot=1):
        super().__init__()
        self.employee_id = employee_id
        self.employee_name = employee_name
        self.finger_slot = finger_slot
        self.agent = None
        self.samples = []
        self.required = 0

    def on_sample(self, sample):
        self.samples.append(sample)
        self.event_received.emit(f"[INFO] Enrollment capture {len(self.samples)}/{self.required}")
        if len(self.samples) >= self.required and self.agent:
            self.event_received.emit("[INFO] Semua capture selesai, membuat template...")
            self.agent.stop()

    def run(self):
        try:
            service = EnrollmentService()
            self.required = service.required_samples()
            self.event_received.emit(f"[INFO] Tempelkan jari yang sama {self.required} kali")
            self.agent = FingerprintCaptureAgent(
                on_event=self.event_received.emit,
                on_sample=self.on_sample,
            )
            self.agent.run()
            if len(self.samples) != self.required:
                raise RuntimeError("Enrollment dihentikan sebelum semua capture selesai")
            template = service.create_template(self.samples)
            target = service.save_template_remote(template, self.employee_id, self.finger_slot)
            self.completed.emit(str(target))
        except Exception as error:
            self.failed.emit(str(error))

    def stop_enrollment(self):
        if self.agent:
            self.agent.stop()


class EmployeeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Register Karyawan")
        self.employee_id = QLineEdit()
        self.employee_id.setPlaceholderText("Contoh: EMP-1001")
        self.employee_name = QLineEdit()
        self.employee_name.setPlaceholderText("Contoh: Budi Santoso")
        self.finger_slot = QComboBox()
        self.finger_slot.addItems(["1", "2", "3"])
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Masukkan NIP / nomor induk pegawai:"))
        layout.addWidget(self.employee_id)
        layout.addWidget(QLabel("Masukkan nama karyawan:"))
        layout.addWidget(self.employee_name)
        layout.addWidget(QLabel("Pilih slot sidik jari (maksimal 3):"))
        layout.addWidget(self.finger_slot)
        layout.addWidget(buttons)

    def accept(self):
        if not self.employee_id.text().strip():
            QMessageBox.warning(self, "NIP wajib diisi", "Masukkan NIP karyawan terlebih dahulu.")
            return
        if not self.employee_name.text().strip():
            QMessageBox.warning(self, "Nama wajib diisi", "Masukkan nama karyawan terlebih dahulu.")
            return
        super().accept()


class MainWindow(QMainWindow):
    connection_failed = Signal(str)
    pending_count_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.worker = None
        self.network_alert_shown = False
        self.agent_engine = AgentEngine()
        self.templates = {}
        self.connection_failed.connect(self.show_connection_alert)
        self.pending_count_changed.connect(self.update_pending_label)
        self._build_ui()

        try:
            self.templates = EnrollmentService.load_all_templates()
        except Exception as error:
            self.show_connection_alert(str(error))
        try:
            synced = self.agent_engine.sync_employees()
        except Exception as error:
            synced = 0
            self.sync_error = str(error)
            self.show_connection_alert(str(error))
        self.agent_engine.start_background_sync(
            interval_seconds=5,
            on_error=self.connection_failed.emit,
            on_complete=self.pending_count_changed.emit,
        )
        self.setWindowTitle("Fingerprint Attendance Agent")
        self.resize(760, 520)
        self.refresh_summary()
        if getattr(self, "sync_error", None):
            self.append_log(f"[WARN] Sinkronisasi karyawan gagal: {self.sync_error}")
        else:
            self.append_log(f"[OK] {synced} karyawan aktif tersinkronisasi")

        QShortcut(QKeySequence("Ctrl+C"), self, self.close)

    def _build_ui(self):
        self.status_dot = QLabel("●")
        self.status_text = QLabel("DEVICE NOT CONNECTED")
        self.status_dot.setObjectName("statusDot")
        self.status_text.setObjectName("statusText")

        self.start_button = QPushButton("Start Scan")
        self.stop_button = QPushButton("Stop Scan")
        self.list_button = QPushButton("Daftar Karyawan")
        self.reload_button = QPushButton("Reload Fingerprint")
        self.start_button.setObjectName("startButton")
        self.stop_button.setObjectName("stopButton")
        self.list_button.setObjectName("listButton")
        self.reload_button.setObjectName("reloadButton")
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        self.list_button.clicked.connect(self.show_registered_employees)
        self.reload_button.clicked.connect(self.reload_fingerprint_data)
        self.stop_button.setEnabled(False)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Log transaksi fingerprint akan tampil di sini...")

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setAlignment(Qt.AlignCenter)
        self.warning_label.setObjectName("warningLabel")
        self.warning_label.hide()

        header = QHBoxLayout()
        header.addWidget(QLabel("FINGERPRINT ATTENDANCE AGENT"))
        header.addStretch()
        header.addWidget(self.status_dot)
        header.addWidget(self.status_text)

        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.list_button)
        controls.addWidget(self.reload_button)
        controls.addStretch()

        self.summary_label = QLabel("0 terdaftar")
        self.summary_label.setObjectName("summaryLabel")
        self.pending_label = QPushButton("0 log belum terkirim")
        self.pending_label.setObjectName("pendingLabel")
        self.pending_label.setCursor(Qt.PointingHandCursor)
        self.pending_label.setToolTip("Klik untuk mengirim ulang log absensi yang tertunda")
        self.pending_label.clicked.connect(self.reload_pending_logs)

        summary_row = QHBoxLayout()
        summary_row.addWidget(self.summary_label)
        summary_row.addWidget(self.pending_label)
        summary_row.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(header)
        layout.addLayout(summary_row)
        layout.addWidget(self.warning_label)
        layout.addWidget(QLabel("Live Fingerprint Transaction Log"))
        layout.addLayout(controls)
        layout.addWidget(self.log_view)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.setStyleSheet("""
            QMainWindow { background: #f8f9fa; }
            QLabel { color: #212529; font-size: 14px; }
            QLabel#statusText { font-weight: 700; }
            QLabel#statusDot { color: #dc3545; font-size: 24px; }
            QLabel#summaryLabel { color: #0d6efd; font-size: 13px; font-weight: 700; }
            QPushButton#pendingLabel { background: transparent; color: #b45309; padding: 0; font-size: 13px; font-weight: 700; border: 0; }
            QPushButton#pendingLabel:hover { color: #92400e; text-decoration: underline; }
            QLabel#warningLabel { color: #842029; background: #f8d7da; border: 1px solid #f1aeb5; padding: 12px; font-size: 18px; font-weight: 700; }
            QPushButton { padding: 10px 20px; font-weight: 600; border: 0; border-radius: 6px; color: white; }
            QPushButton#startButton { background: #0d6efd; }
            QPushButton#startButton:hover { background: #0b5ed7; }
            QPushButton#startButton:disabled { background: #9ec5fe; color: #eef6ff; }
            QPushButton#stopButton { background: #dc3545; }
            QPushButton#stopButton:hover { background: #bb2d3b; }
            QPushButton#stopButton:disabled { background: #f1aeb5; color: #fff5f5; }
            QPushButton#listButton { background: #6f42c1; }
            QPushButton#listButton:hover { background: #59359a; }
            QPushButton#reloadButton { background: #0f766e; }
            QPushButton#reloadButton:hover { background: #115e59; }
            QPushButton#reloadButton:disabled { background: #99d5cf; color: #effffc; }
            QDialog { background: #ffffff; }
            QDialog QLabel { color: #212529; }
            QDialog QPushButton { background: #0d6efd; }
            QDialog QPushButton:hover { background: #0b5ed7; }
            QLineEdit { background: #ffffff; border: 1px solid #ced4da; border-radius: 6px; padding: 8px 10px; color: #212529; }
            QLineEdit:focus { border: 1px solid #86b7fe; }
            QTextEdit { background: #111827; color: #e5e7eb; font-family: Consolas; font-size: 13px; border: 1px solid #374151; border-radius: 6px; }
            QTableWidget { background: #ffffff; gridline-color: #e9ecef; color: #212529; border: 1px solid #dee2e6; border-radius: 6px; }
            QHeaderView::section { background: #e9ecef; color: #212529; padding: 8px; border: 0; }
            QMessageBox { background: #ffffff; }
            QMessageBox QLabel { color: #212529; }
            QMessageBox QPushButton { background: #0d6efd; color: white; border: 0; border-radius: 6px; }
            QMessageBox QPushButton:hover { background: #0b5ed7; }
        """)

    def refresh_summary(self):
        self.summary_label.setText(f"{len(self.templates)} terdaftar")
        self.refresh_pending_count()

    def refresh_pending_count(self):
        self.update_pending_label(self.agent_engine.pending_log_count())

    def update_pending_label(self, pending_count):
        self.pending_label.setText(f"{pending_count} log belum terkirim")

    def append_log(self, message):
        self.log_view.append(message)
        if message.startswith("[WARN] Fingerprint tidak cocok"):
            self.warning_label.setText(
                "SIDIK JARI TIDAK DIKENAL\n"
                "PERIKSA SIDIK JARI ANDA, ATAU DAFTARKAN SIDIK JARI ANDA TERLEBIH DAHULU"
            )
            self.warning_label.show()
        elif message.startswith("[OK] Fingerprint matched"):
            self.warning_label.clear()
            self.warning_label.hide()
            self.refresh_pending_count()

        if "Fingerprint reader connected" in message:
            self.status_dot.setStyleSheet("color: #2e9d62; font-size: 24px;")
            self.status_text.setText("DEVICE CONNECTED")
        elif "disconnected" in message.lower() or message.startswith("[ERROR]"):
            self.status_dot.setStyleSheet("color: #d64545; font-size: 24px;")
            self.status_text.setText("DEVICE NOT CONNECTED")

    def show_connection_alert(self, details=""):
        self.append_log(f"[ERROR] Koneksi server gagal: {details}")
        if self.network_alert_shown:
            return
        self.network_alert_shown = True
        QMessageBox.critical(
            self,
            "Koneksi Internet Tidak Tersedia",
            "Koneksi Inertnet Anda tidak ada, SIlahkan Cek ulang Jalur Jaringan Anda",
        )

    def start_scan(self):
        self.log_view.append("[INFO] Starting fingerprint scan...")
        self.warning_label.clear()
        self.warning_label.hide()
        self.status_text.setText("CONNECTING...")
        self.status_dot.setStyleSheet("color: #d49a27; font-size: 24px;")
        self.worker = CaptureWorker(self.agent_engine, self.templates)
        self.worker.event_received.connect(self.append_log)
        self.worker.failed.connect(self.handle_error)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def reload_pending_logs(self):
        self.pending_label.setEnabled(False)
        try:
            uploaded, remaining = self.agent_engine.sync_all_pending_to_server(
                on_error=self.connection_failed.emit
            )
            self.refresh_pending_count()
            if remaining:
                self.show_connection_alert("Masih ada log yang belum terkirim")
            else:
                QMessageBox.information(
                    self,
                    "Reload Log Berhasil",
                    f"{uploaded} log absensi berhasil dikirim ke server.",
                )
        except Exception as error:
            self.show_connection_alert(str(error))
        finally:
            self.pending_label.setEnabled(True)

    def reload_fingerprint_data(self):
        self.reload_button.setEnabled(False)
        self.append_log("[INFO] Memuat ulang template fingerprint dan master karyawan...")
        try:
            templates = EnrollmentService.load_all_templates()
            synced = self.agent_engine.sync_employees()
            self.templates = templates
            self.refresh_summary()
            self.network_alert_shown = False
            self.append_log(f"[OK] Reload selesai: {len(templates)} karyawan fingerprint, {synced} karyawan tersinkronisasi")
            QMessageBox.information(
                self,
                "Reload Berhasil",
                f"Data fingerprint berhasil dimuat ulang.\n\n"
                f"Karyawan dengan fingerprint: {len(templates)}\n"
                f"Master karyawan tersinkronisasi: {synced}",
            )
        except Exception as error:
            self.show_connection_alert(str(error))
        finally:
            self.reload_button.setEnabled(True)

    def stop_scan(self):
        if self.worker and hasattr(self.worker, "stop_enrollment"):
            self.worker.stop_enrollment()
        elif self.worker and hasattr(self.worker, "stop_capture"):
            self.worker.stop_capture()
        self.log_view.append("[INFO] Stopping fingerprint scan...")

    def register_employee(self):
        dialog = EmployeeDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        employee_id = dialog.employee_id.text().strip()
        employee_name = dialog.employee_name.text().strip()
        self.start_enrollment(employee_id, employee_name, int(dialog.finger_slot.currentText()))

    def start_enrollment(self, employee_id, employee_name, finger_slot):
        self.list_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Batalkan Registrasi")
        self.status_text.setText("REGISTERING...")
        self.status_dot.setStyleSheet("color: #d49a27; font-size: 24px;")
        self.log_view.append(f"[INFO] Registration started for {employee_id} - {employee_name}")
        self.worker = EnrollmentWorker(employee_id, employee_name, finger_slot)
        self.worker.event_received.connect(self.append_log)
        self.worker.completed.connect(self.registration_completed)
        self.worker.failed.connect(self.handle_error)
        self.worker.finished.connect(self.registration_finished)
        self.worker.start()

    def registration_completed(self, target):
        self.templates = EnrollmentService.load_all_templates()
        self.refresh_summary()
        self.log_view.append(f"[OK] Employee mapped to template: {target}")
        QMessageBox.information(self, "Registrasi berhasil", "Template fingerprint berhasil dibuat dan disimpan.")

    def show_registered_employees(self):
        try:
            client_config = fetch_client_config()
        except Exception as error:
            self.show_connection_alert(str(error))
            return

        pin, accepted = QInputDialog.getText(
            self,
            "PIN Daftar Karyawan",
            "Masukkan PIN untuk membuka Daftar Karyawan:",
            QLineEdit.Password,
        )
        if not accepted:
            return
        if pin != str(client_config.get("pin_open_daftar_karyawan", "")):
            QMessageBox.warning(self, "PIN Salah", "PIN yang Anda masukkan salah.")
            return

        try:
            records = EnrollmentService.get_employee_records()
        except Exception as error:
            self.show_connection_alert(str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Daftar Karyawan")
        dialog.resize(760, 440)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["NIP Karyawan", "Nama Karyawan", "Status Finger", "Aksi"])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        table.setRowCount(len(records))
        for row_index, record in enumerate(records):
            employee_id = record["employee_id"]
            finger_count = record["finger_count"]
            table.setItem(row_index, 0, QTableWidgetItem(employee_id))
            table.setItem(row_index, 1, QTableWidgetItem(record["employee_name"]))
            status = f"{finger_count}/3 terdaftar" if finger_count else "Belum ada fingerprint"
            table.setItem(row_index, 2, QTableWidgetItem(status))

            action = QComboBox()
            if finger_count < 3:
                action.addItem("Tambah Finger", "add")
            if finger_count:
                action.addItem("Hapus Semua Finger", "delete")
            action.addItem("Pilih aksi...", "none")
            action.setCurrentIndex(action.count() - 1)
            action.activated.connect(
                lambda _, combo=action, employee=record: self.handle_fingerprint_action(
                    dialog, combo, employee
                )
            )
            table.setCellWidget(row_index, 3, action)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Daftar seluruh karyawan dari server:"))
        layout.addWidget(table)
        dialog.exec()

    def handle_fingerprint_action(self, dialog, combo, record):
        action = combo.currentData()
        combo.setCurrentIndex(combo.count() - 1)
        if action == "add":
            slot = next(slot for slot in range(1, 4) if slot not in record["finger_slots"])
            slot_dialog = EmployeeDialog(self)
            slot_dialog.employee_id.setText(record["employee_id"])
            slot_dialog.employee_id.setReadOnly(True)
            slot_dialog.employee_name.setText(record["employee_name"])
            slot_dialog.employee_name.setReadOnly(True)
            slot_dialog.finger_slot.setCurrentText(str(slot))
            if slot_dialog.exec() == QDialog.Accepted:
                dialog.close()
                self.start_enrollment(record["employee_id"], record["employee_name"], slot)
        elif action == "delete":
            result = QMessageBox.warning(
                self,
                "Hapus semua fingerprint",
                "SIDIK JARI AKAN DIHAPUS SEMUA\n\n"
                "KAMU BISA DAFTARKAN ULANG SIDIK JARI",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if result != QMessageBox.Yes:
                return
            EnrollmentService.delete_employee(record["employee_id"])
            self.templates = EnrollmentService.load_all_templates()
            self.refresh_summary()
            dialog.close()
            QMessageBox.information(self, "Berhasil", "Semua fingerprint karyawan sudah dihapus.")

    def registration_finished(self):
        self.list_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stop Scan")
        self.status_dot.setStyleSheet("color: #2e9d62; font-size: 24px;")
        self.status_text.setText("DEVICE CONNECTED")
        self.worker = None

    def handle_error(self, message):
        self.append_log(f"[ERROR] {message}")
        if "connection" in message.lower() or "timed out" in message.lower() or "max retries" in message.lower():
            self.show_connection_alert(message)
            return
        QMessageBox.critical(
            self,
            "Fingerprint Device Error",
            "Fingerprint device tidak dapat digunakan.\n\n" + message,
        )

    def scan_finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_dot.setStyleSheet("color: #d64545; font-size: 24px;")
        self.status_text.setText("SCAN STOPPED")
        self.worker = None

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.stop_scan()
            self.worker.wait(3000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
