import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from capture_agent import FingerprintCaptureAgent
from enrollment_service import EnrollmentService
from tarik_data import AgentEngine


class CaptureWorker(QThread):
    event_received = Signal(str)
    failed = Signal(str)

    def __init__(self, agent_engine):
        super().__init__()
        self.agent_engine = agent_engine
        self.agent = None
        self.verification = EnrollmentService()

    def on_sample(self, sample):
        for template_path in Path("templates").glob("*.fpt"):
            if self.verification.verify_sample(template_path.read_bytes(), sample):
                employee_id = template_path.stem
                self.agent_engine.save_log(
                    employee_id,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "U.are.U 4500",
                    employee_id,
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

    def __init__(self, employee_id):
        super().__init__()
        self.employee_id = employee_id
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
            target = service.save_template(template, self.employee_id)
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
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Masukkan NIP / nomor induk pegawai:"))
        layout.addWidget(self.employee_id)
        layout.addWidget(buttons)

    def accept(self):
        if not self.employee_id.text().strip():
            QMessageBox.warning(self, "NIP wajib diisi", "Masukkan NIP karyawan terlebih dahulu.")
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.agent_engine = AgentEngine()
        self.agent_engine.start_background_sync(interval_seconds=5)
        self.setWindowTitle("Fingerprint Attendance Agent")
        self.resize(760, 520)
        self._build_ui()

        QShortcut(QKeySequence("Ctrl+C"), self, self.close)

    def _build_ui(self):
        self.status_dot = QLabel("●")
        self.status_text = QLabel("DEVICE NOT CONNECTED")
        self.status_dot.setObjectName("statusDot")
        self.status_text.setObjectName("statusText")

        self.start_button = QPushButton("Start Scan")
        self.stop_button = QPushButton("Stop Scan")
        self.register_button = QPushButton("Register Karyawan")
        self.start_button.setObjectName("startButton")
        self.stop_button.setObjectName("stopButton")
        self.register_button.setObjectName("registerButton")
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        self.register_button.clicked.connect(self.register_employee)
        self.stop_button.setEnabled(False)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Log transaksi fingerprint akan tampil di sini...")

        header = QHBoxLayout()
        header.addWidget(QLabel("FINGERPRINT ATTENDANCE AGENT"))
        header.addStretch()
        header.addWidget(self.status_dot)
        header.addWidget(self.status_text)

        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.register_button)
        controls.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(header)
        layout.addWidget(QLabel("Live Fingerprint Transaction Log"))
        layout.addLayout(controls)
        layout.addWidget(self.log_view)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.setStyleSheet("""
            QMainWindow { background: #f4f6f8; }
            QLabel { color: #24313d; font-size: 14px; }
            QLabel#statusText { font-weight: 700; }
            QLabel#statusDot { color: #d64545; font-size: 24px; }
            QPushButton { padding: 10px 20px; font-weight: 600; }
            QPushButton#startButton { background: #1677c8; color: white; border: 0; border-radius: 4px; }
            QPushButton#startButton:hover { background: #0f5f9f; }
            QPushButton#startButton:disabled { background: #9fb4c5; color: #edf3f7; }
            QPushButton#stopButton { background: #d64545; color: white; border: 0; border-radius: 4px; }
            QPushButton#stopButton:hover { background: #b93434; }
            QPushButton#stopButton:disabled { background: #c9ced3; color: #70777d; }
            QPushButton#registerButton { background: #16705a; color: white; border: 0; border-radius: 4px; }
            QPushButton#registerButton:hover { background: #0f5947; }
            QPushButton#registerButton:disabled { background: #b7c8c3; color: #edf3f7; }
            QTextEdit { background: #17232d; color: #d8f3dc; font-family: Consolas; font-size: 13px; }
        """)

    def append_log(self, message):
        self.log_view.append(message)
        if "Fingerprint reader connected" in message:
            self.status_dot.setStyleSheet("color: #2e9d62; font-size: 24px;")
            self.status_text.setText("DEVICE CONNECTED")
        elif "disconnected" in message.lower() or message.startswith("[ERROR]"):
            self.status_dot.setStyleSheet("color: #d64545; font-size: 24px;")
            self.status_text.setText("DEVICE NOT CONNECTED")

    def start_scan(self):
        self.log_view.append("[INFO] Starting fingerprint scan...")
        self.status_text.setText("CONNECTING...")
        self.status_dot.setStyleSheet("color: #d49a27; font-size: 24px;")
        self.worker = CaptureWorker(self.agent_engine)
        self.worker.event_received.connect(self.append_log)
        self.worker.failed.connect(self.handle_error)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

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
        self.register_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Batalkan Registrasi")
        self.status_text.setText("REGISTERING...")
        self.status_dot.setStyleSheet("color: #d49a27; font-size: 24px;")
        self.log_view.append(f"[INFO] Registration started for {employee_id}")
        self.worker = EnrollmentWorker(employee_id)
        self.worker.event_received.connect(self.append_log)
        self.worker.completed.connect(self.registration_completed)
        self.worker.failed.connect(self.handle_error)
        self.worker.finished.connect(self.registration_finished)
        self.worker.start()

    def registration_completed(self, target):
        self.log_view.append(f"[OK] Employee mapped to template: {target}")
        QMessageBox.information(self, "Registrasi berhasil", "Template fingerprint berhasil dibuat dan disimpan.")

    def registration_finished(self):
        self.register_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stop Scan")
        self.status_dot.setStyleSheet("color: #2e9d62; font-size: 24px;")
        self.status_text.setText("DEVICE CONNECTED")
        self.worker = None

    def handle_error(self, message):
        self.append_log(f"[ERROR] {message}")
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
