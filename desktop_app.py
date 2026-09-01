import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
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

    def __init__(self, agent_engine, templates=None):
        super().__init__()
        self.agent_engine = agent_engine
        self.agent = None
        self.verification = EnrollmentService()
        self.templates = templates or {}

    def on_sample(self, sample):
        for employee_id, (employee_name, template_blob) in self.templates.items():
            if self.verification.verify_sample(template_blob, sample):
                self.agent_engine.save_log(
                    employee_id,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "U.are.U 4500",
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

    def __init__(self, employee_id, employee_name=""):
        super().__init__()
        self.employee_id = employee_id
        self.employee_name = employee_name
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
            target = service.save_template(template, self.employee_id, self.employee_name)
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
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Masukkan NIP / nomor induk pegawai:"))
        layout.addWidget(self.employee_id)
        layout.addWidget(QLabel("Masukkan nama karyawan:"))
        layout.addWidget(self.employee_name)
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
    def __init__(self):
        super().__init__()
        self.worker = None
        self.agent_engine = AgentEngine()
        self.templates = EnrollmentService.load_all_templates()
        self.agent_engine.start_background_sync(interval_seconds=5)
        self.setWindowTitle("Fingerprint Attendance Agent")
        self.resize(760, 520)
        self._build_ui()
        self.refresh_summary()

        QShortcut(QKeySequence("Ctrl+C"), self, self.close)

    def _build_ui(self):
        self.status_dot = QLabel("●")
        self.status_text = QLabel("DEVICE NOT CONNECTED")
        self.status_dot.setObjectName("statusDot")
        self.status_text.setObjectName("statusText")

        self.start_button = QPushButton("Start Scan")
        self.stop_button = QPushButton("Stop Scan")
        self.register_button = QPushButton("Register Karyawan")
        self.list_button = QPushButton("Daftar Karyawan")
        self.start_button.setObjectName("startButton")
        self.stop_button.setObjectName("stopButton")
        self.register_button.setObjectName("registerButton")
        self.list_button.setObjectName("listButton")
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        self.register_button.clicked.connect(self.register_employee)
        self.list_button.clicked.connect(self.show_registered_employees)
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
        controls.addWidget(self.list_button)
        controls.addStretch()

        self.summary_label = QLabel("0 terdaftar")
        self.summary_label.setObjectName("summaryLabel")

        layout = QVBoxLayout()
        layout.addLayout(header)
        layout.addWidget(self.summary_label)
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
            QPushButton { padding: 10px 20px; font-weight: 600; border: 0; border-radius: 6px; color: white; }
            QPushButton#startButton { background: #0d6efd; }
            QPushButton#startButton:hover { background: #0b5ed7; }
            QPushButton#startButton:disabled { background: #9ec5fe; color: #eef6ff; }
            QPushButton#stopButton { background: #dc3545; }
            QPushButton#stopButton:hover { background: #bb2d3b; }
            QPushButton#stopButton:disabled { background: #f1aeb5; color: #fff5f5; }
            QPushButton#registerButton { background: #198754; }
            QPushButton#registerButton:hover { background: #157347; }
            QPushButton#registerButton:disabled { background: #a3cfbb; color: #f0fdf4; }
            QPushButton#listButton { background: #6f42c1; }
            QPushButton#listButton:hover { background: #59359a; }
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
        self.worker = CaptureWorker(self.agent_engine, self.templates)
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
        employee_name = dialog.employee_name.text().strip()
        if EnrollmentService.employee_exists(employee_id):
            QMessageBox.warning(self, "NIP sudah terdaftar", f"NIP {employee_id} sudah ada di database. Registrasi ditolak.")
            return

        self.register_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Batalkan Registrasi")
        self.status_text.setText("REGISTERING...")
        self.status_dot.setStyleSheet("color: #d49a27; font-size: 24px;")
        self.log_view.append(f"[INFO] Registration started for {employee_id} - {employee_name}")
        self.worker = EnrollmentWorker(employee_id, employee_name)
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
        records = EnrollmentService.get_employee_records()
        dialog = QDialog(self)
        dialog.setWindowTitle("Daftar Karyawan Terdaftar")
        dialog.resize(560, 360)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["NIP Karyawan", "Nama Karyawan", "Tanggal Daftar"])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        if not records:
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem("-"))
            table.setItem(0, 1, QTableWidgetItem("Belum ada karyawan"))
            table.setItem(0, 2, QTableWidgetItem("-"))
        else:
            table.setRowCount(len(records))
            for row_index, record in enumerate(records):
                table.setItem(row_index, 0, QTableWidgetItem(record["employee_id"]))
                table.setItem(row_index, 1, QTableWidgetItem(record["employee_name"]))
                table.setItem(row_index, 2, QTableWidgetItem(record["created_at"]))

        delete_button = QPushButton("Hapus Karyawan Terpilih")
        delete_button.clicked.connect(lambda: self.delete_selected_employee(dialog, table))

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Daftar fingerprint terdaftar:"))
        layout.addWidget(table)
        layout.addWidget(delete_button)
        dialog.exec()

    def delete_selected_employee(self, dialog, table):
        selected_row = table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Tidak ada yang dipilih", "Pilih karyawan yang akan dihapus terlebih dahulu.")
            return
        employee_id = table.item(selected_row, 0).text().strip()
        if not employee_id or employee_id == "-":
            return
        result = QMessageBox.question(
            self,
            "Hapus karyawan",
            f"Yakin ingin menghapus {employee_id} dan template fingerprintnya?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        EnrollmentService.delete_employee(employee_id)
        self.templates = EnrollmentService.load_all_templates()
        self.refresh_summary()
        dialog.close()
        QMessageBox.information(self, "Berhasil", f"Karyawan {employee_id} dan template fingerprintnya sudah dihapus.")

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
