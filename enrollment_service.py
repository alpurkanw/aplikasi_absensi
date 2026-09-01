import ctypes
import sqlite3
from pathlib import Path

from capture_agent import dpfp


FEATURE_DLL = "dpHFtrEx.dll"
MATCH_DLL = "dpHMatch.dll"
FT_PRE_REG_FTR = 0
FT_REG_FTR = 1
FT_VER_FTR = 2
FT_OK = 0
FT_TRUE = 1

fx = ctypes.WinDLL(FEATURE_DLL)
mc = ctypes.WinDLL(MATCH_DLL)

fx.FX_init.restype = ctypes.c_int
fx.FX_terminate.restype = ctypes.c_int
fx.FX_createContext.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
fx.FX_createContext.restype = ctypes.c_int
fx.FX_closeContext.argtypes = [ctypes.c_void_p]
fx.FX_closeContext.restype = ctypes.c_int
fx.FX_getFeaturesLen.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
fx.FX_getFeaturesLen.restype = ctypes.c_int
fx.FX_extractFeatures.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
]
fx.FX_extractFeatures.restype = ctypes.c_int

mc.MC_init.restype = ctypes.c_int
mc.MC_terminate.restype = ctypes.c_int
mc.MC_createContext.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
mc.MC_createContext.restype = ctypes.c_int
mc.MC_closeContext.argtypes = [ctypes.c_void_p]
mc.MC_closeContext.restype = ctypes.c_int
mc.MC_getSettings.argtypes = [ctypes.POINTER(ctypes.c_int)]
mc.MC_getSettings.restype = ctypes.c_int
mc.MC_getFeaturesLen.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
mc.MC_getFeaturesLen.restype = ctypes.c_int
mc.MC_generateRegFeatures.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
]
mc.MC_generateRegFeatures.restype = ctypes.c_int
mc.MC_verifyFeaturesEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_int),
]
mc.MC_verifyFeaturesEx.restype = ctypes.c_int


class EnrollmentService:
    DEFAULT_DB_PATH = "attendance_offline.db"

    @staticmethod
    def _resolve_db_path(db_path=None):
        if db_path is None:
            return EnrollmentService.DEFAULT_DB_PATH
        return db_path

    @staticmethod
    def _migrate_legacy_templates(target_db_path=None):
        target_db_path = EnrollmentService._resolve_db_path(target_db_path)
        legacy_db = "employee_templates.db"
        if not Path(legacy_db).exists() or Path(target_db_path).exists() and Path(target_db_path).stat().st_size > 0:
            return

        try:
            with sqlite3.connect(legacy_db) as src, sqlite3.connect(target_db_path) as dst:
                rows = src.execute(
                    "SELECT employee_id, employee_name, template_blob, created_at FROM employee_templates"
                ).fetchall()
                if rows:
                    dst.execute(
                        """
                        CREATE TABLE IF NOT EXISTS employee_templates (
                            employee_id TEXT PRIMARY KEY,
                            employee_name TEXT NOT NULL DEFAULT '',
                            template_blob BLOB NOT NULL,
                            created_at TEXT DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                    dst.executemany(
                        "INSERT OR REPLACE INTO employee_templates (employee_id, employee_name, template_blob, created_at) VALUES (?, ?, ?, ?)",
                        rows,
                    )
                    dst.commit()
        except sqlite3.Error:
            pass

    @staticmethod
    def init_template_db(db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        EnrollmentService._migrate_legacy_templates(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS employee_templates (
                    employee_id TEXT PRIMARY KEY,
                    employee_name TEXT NOT NULL DEFAULT '',
                    template_blob BLOB NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            columns = [row[1] for row in conn.execute("PRAGMA table_info(employee_templates)").fetchall()]
            if "employee_name" not in columns:
                conn.execute("ALTER TABLE employee_templates ADD COLUMN employee_name TEXT NOT NULL DEFAULT ''")
            conn.commit()

    @staticmethod
    def employee_exists(employee_id, db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        employee_id = (employee_id or "").strip()
        if not employee_id:
            return False
        EnrollmentService.init_template_db(db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM employee_templates WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def save_template(template, employee_id, employee_name="", directory="templates", db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        if not template:
            raise ValueError("Template kosong")

        employee_id = (employee_id or "").strip()
        if not employee_id:
            raise ValueError("NIP karyawan wajib diisi")
        if EnrollmentService.employee_exists(employee_id, db_path):
            raise ValueError(f"NIP karyawan {employee_id} sudah terdaftar")

        EnrollmentService.init_template_db(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO employee_templates (employee_id, employee_name, template_blob)
                VALUES (?, ?, ?)
                """,
                (employee_id, employee_name, template),
            )
            conn.commit()
        return employee_id

    @staticmethod
    def list_registered_employees(db_path=None, directory="templates"):
        db_path = EnrollmentService._resolve_db_path(db_path)
        EnrollmentService.init_template_db(db_path)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT employee_id, created_at FROM employee_templates ORDER BY employee_id"
            ).fetchall()

        employees = [row[0] for row in rows]
        if not employees:
            for template_path in Path(directory).glob("*.fpt"):
                employees.append(template_path.stem)
        return employees

    @staticmethod
    def get_employee_name(employee_id, db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT employee_name FROM employee_templates WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
        return row[0] if row and row[0] else employee_id

    @staticmethod
    def get_employee_records(db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        EnrollmentService.init_template_db(db_path)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT employee_id, employee_name, created_at FROM employee_templates ORDER BY employee_id"
            ).fetchall()
        return [
            {"employee_id": employee_id, "employee_name": employee_name or employee_id, "created_at": created_at}
            for employee_id, employee_name, created_at in rows
        ]

    @staticmethod
    def delete_employee(employee_id, directory="templates", db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        EnrollmentService.init_template_db(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM employee_templates WHERE employee_id = ?", (employee_id,))
            conn.commit()

        template_file = Path(directory) / f"{employee_id}.fpt"
        if template_file.exists():
            template_file.unlink()
        return True

    @staticmethod
    def load_all_templates(db_path=None, directory="templates"):
        db_path = EnrollmentService._resolve_db_path(db_path)
        EnrollmentService.init_template_db(db_path)
        templates = {}
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT employee_id, employee_name, template_blob FROM employee_templates ORDER BY employee_id"
            ).fetchall()

        for employee_id, employee_name, template_blob in rows:
            templates[employee_id] = (employee_name or employee_id, template_blob)

        if not templates and Path(directory).exists():
            for template_path in Path(directory).glob("*.fpt"):
                employee_id = template_path.stem
                template_blob = template_path.read_bytes()
                templates[employee_id] = (employee_id, template_blob)
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO employee_templates (employee_id, employee_name, template_blob) VALUES (?, ?, ?)",
                        (employee_id, employee_id, template_blob),
                    )
                    conn.commit()
        return templates

    def required_samples(self):
        if fx.FX_init() != FT_OK or mc.MC_init() != FT_OK:
            raise RuntimeError("SDK feature extraction tidak dapat diinisialisasi")
        try:
            settings = (ctypes.c_int * 1)()
            if mc.MC_getSettings(settings) != FT_OK:
                raise RuntimeError("Konfigurasi enrollment SDK tidak dapat dibaca")
            return settings[0]
        finally:
            mc.MC_terminate()
            fx.FX_terminate()

    def create_template(self, samples):
        if not samples:
            raise ValueError("Tidak ada sample fingerprint")
        if fx.FX_init() != FT_OK or mc.MC_init() != FT_OK:
            raise RuntimeError("SDK enrollment tidak dapat diinisialisasi")
        fx_context = ctypes.c_void_p()
        mc_context = ctypes.c_void_p()
        try:
            if fx.FX_createContext(ctypes.byref(fx_context)) != FT_OK:
                raise RuntimeError("Feature extraction context gagal dibuat")
            if mc.MC_createContext(ctypes.byref(mc_context)) != FT_OK:
                raise RuntimeError("Matching context gagal dibuat")

            feature_len = ctypes.c_int()
            if fx.FX_getFeaturesLen(FT_PRE_REG_FTR, ctypes.byref(feature_len), None) != FT_OK:
                raise RuntimeError("Ukuran feature fingerprint tidak tersedia")

            features = []
            for sample in samples:
                image = ctypes.create_string_buffer(sample)
                feature = ctypes.create_string_buffer(feature_len.value)
                image_quality = ctypes.c_int()
                feature_quality = ctypes.c_int()
                created = ctypes.c_int()
                result = fx.FX_extractFeatures(
                    fx_context, len(sample), image, FT_PRE_REG_FTR,
                    feature_len.value, feature, ctypes.byref(image_quality),
                    ctypes.byref(feature_quality), ctypes.byref(created),
                )
                if result != FT_OK or not created.value:
                    raise RuntimeError("Kualitas fingerprint tidak cukup untuk membuat template")
                features.append(feature)

            template_len = ctypes.c_int()
            if mc.MC_getFeaturesLen(FT_REG_FTR, 0, ctypes.byref(template_len), None) != FT_OK:
                raise RuntimeError("Ukuran template fingerprint tidak tersedia")
            feature_ptrs = (ctypes.c_void_p * len(features))(
                *(ctypes.cast(feature, ctypes.c_void_p) for feature in features)
            )
            template = ctypes.create_string_buffer(template_len.value)
            created = ctypes.c_int()
            result = mc.MC_generateRegFeatures(
                mc_context, 0, len(features), feature_len.value,
                feature_ptrs, template_len.value, template, None,
                ctypes.byref(created),
            )
            if result != FT_OK or not created.value:
                raise RuntimeError("Template fingerprint gagal dibuat")
            return template.raw[:template_len.value]
        finally:
            if mc_context:
                mc.MC_closeContext(mc_context)
            if fx_context:
                fx.FX_closeContext(fx_context)
            mc.MC_terminate()
            fx.FX_terminate()

    def verify_sample(self, template, sample):
        if not template or not sample:
            return False
        if fx.FX_init() != FT_OK or mc.MC_init() != FT_OK:
            raise RuntimeError("SDK verification tidak dapat diinisialisasi")
        fx_context = ctypes.c_void_p()
        mc_context = ctypes.c_void_p()
        try:
            if fx.FX_createContext(ctypes.byref(fx_context)) != FT_OK:
                raise RuntimeError("Feature extraction context gagal dibuat")
            if mc.MC_createContext(ctypes.byref(mc_context)) != FT_OK:
                raise RuntimeError("Matching context gagal dibuat")
            feature_len = ctypes.c_int()
            if fx.FX_getFeaturesLen(FT_VER_FTR, ctypes.byref(feature_len), None) != FT_OK:
                raise RuntimeError("Ukuran feature verifikasi tidak tersedia")
            image = ctypes.create_string_buffer(sample)
            feature = ctypes.create_string_buffer(feature_len.value)
            image_quality = ctypes.c_int()
            feature_quality = ctypes.c_int()
            created = ctypes.c_int()
            result = fx.FX_extractFeatures(
                fx_context, len(sample), image, FT_VER_FTR,
                feature_len.value, feature, ctypes.byref(image_quality),
                ctypes.byref(feature_quality), ctypes.byref(created),
            )
            if result != FT_OK or not created.value:
                return False
            template_buffer = ctypes.create_string_buffer(template)
            achieved_far = ctypes.c_double()
            decision = ctypes.c_int()
            result = mc.MC_verifyFeaturesEx(
                mc_context, len(template), template_buffer,
                feature_len.value, feature, 0, None, None, None,
                ctypes.byref(achieved_far), ctypes.byref(decision),
            )
            return result == FT_OK and bool(decision.value)
        finally:
            if mc_context:
                mc.MC_closeContext(mc_context)
            if fx_context:
                fx.FX_closeContext(fx_context)
            mc.MC_terminate()
            fx.FX_terminate()
