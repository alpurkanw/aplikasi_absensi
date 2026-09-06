import ctypes
import base64
import sqlite3
from pathlib import Path

from capture_agent import dpfp
from tarik_data import delete_fingerprint_templates, fetch_employees, fetch_fingerprint_templates, upload_fingerprint_template


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
    DEFAULT_DB_PATH = Path(__file__).with_name("attendance_offline.db")

    @staticmethod
    def _resolve_db_path(db_path=None):
        if db_path is None:
            return EnrollmentService.DEFAULT_DB_PATH
        return db_path

    @staticmethod
    def employee_exists(employee_id, db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        employee_id = (employee_id or "").strip()
        if not employee_id:
            return False
        return any(item["employee_code"] == employee_id for item in fetch_fingerprint_templates())

    @staticmethod
    def save_template_remote(template, employee_id, finger_slot):
        if not template:
            raise ValueError("Template kosong")
        if not 1 <= int(finger_slot) <= 3:
            raise ValueError("Nomor sidik jari harus antara 1 dan 3")
        upload_fingerprint_template(employee_id, int(finger_slot), template)
        return employee_id

    @staticmethod
    def migrate_local_templates_to_server(db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        sources = []
        for source_path in (Path(db_path), Path(db_path).with_name("employee_templates.db")):
            if not source_path.exists():
                continue
            with sqlite3.connect(source_path) as conn:
                table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'employee_templates'"
                ).fetchone()
                if table:
                    sources.extend((source_path, employee_id, template_blob) for employee_id, template_blob in conn.execute(
                        "SELECT employee_id, template_blob FROM employee_templates ORDER BY employee_id"
                    ).fetchall())

        migrated = []
        for source_path, employee_id, template_blob in sources:
            try:
                EnrollmentService.save_template_remote(template_blob, employee_id, 1)
                migrated.append((source_path, employee_id))
            except Exception:
                continue

        for source_path, employee_id in migrated:
            if source_path == Path(db_path):
                with sqlite3.connect(source_path) as conn:
                    conn.execute("DELETE FROM employee_templates WHERE employee_id = ?", (employee_id,))
                    conn.commit()

        legacy_db = Path(db_path).with_name("employee_templates.db")
        if legacy_db.exists():
            with sqlite3.connect(legacy_db) as conn:
                remaining = conn.execute("SELECT COUNT(*) FROM employee_templates").fetchone()[0]
            if remaining == 0:
                legacy_db.unlink()
        return len(migrated)

    @staticmethod
    def list_registered_employees(db_path=None, directory="templates"):
        db_path = EnrollmentService._resolve_db_path(db_path)
        return sorted({item["employee_code"] for item in fetch_fingerprint_templates()})

    @staticmethod
    def get_employee_name(employee_id, db_path=None):
        db_path = EnrollmentService._resolve_db_path(db_path)
        for item in fetch_fingerprint_templates():
            if item["employee_code"] == employee_id:
                return item.get("employee_name") or employee_id
        return employee_id

    @staticmethod
    def get_employee_records(db_path=None):
        templates_by_employee = {}
        for item in fetch_fingerprint_templates():
            templates_by_employee.setdefault(item["employee_code"], []).append(item)

        records = []
        for employee in fetch_employees():
            employee_id = employee["employee_code"]
            employee_templates = templates_by_employee.get(employee_id, [])
            records.append({
                "employee_id": employee_id,
                "employee_name": employee.get("name") or employee_id,
                "created_at": employee.get("updated_at") or "SERVER",
                "finger_count": len(employee_templates),
                "finger_slots": [item["finger_slot"] for item in employee_templates],
            })
        return records

    @staticmethod
    def delete_employee(employee_id, directory="templates", db_path=None):
        delete_fingerprint_templates(employee_id)
        return True

    @staticmethod
    def load_all_templates(db_path=None, directory="templates"):
        EnrollmentService.migrate_local_templates_to_server(db_path)
        templates = {}
        for item in fetch_fingerprint_templates():
            employee_id = item["employee_code"]
            templates.setdefault(employee_id, []).append(
                (item.get("employee_name") or employee_id, base64.b64decode(item["template_base64"]))
            )
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
