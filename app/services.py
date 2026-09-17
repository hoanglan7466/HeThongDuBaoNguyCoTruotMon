import csv, io, json, os, smtplib
from functools import lru_cache
from email.message import EmailMessage
from pathlib import Path
import joblib
import pandas as pd
from flask import current_app
from .extensions import db
from .models import Alert, Course, EmailLog, Enrollment, ModelVersion, Prediction, Recommendation, Semester, Student

FEATURES = ["score", "attendance_rate", "late_submissions"]
REQUIRED_COLUMNS = ["student_code", "full_name", "class_name", "email", "course_code", "course_name", "semester_code", "semester_name", "current_week", *FEATURES]

def risk_level(probability):
    if probability >= current_app.config["RISK_HIGH_THRESHOLD"]: return "CAO"
    if probability >= current_app.config["RISK_MEDIUM_THRESHOLD"]: return "TRUNG_BINH"
    return "ON_DINH"

def validate_csv(stream):
    try: text = stream.read().decode("utf-8-sig")
    except Exception: return [], [{"row": 0, "message": "Tệp phải dùng mã hóa UTF-8."}]
    reader = csv.DictReader(io.StringIO(text)); rows, errors = [], []
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing: return [], [{"row": 1, "message": "Thiếu cột: " + ", ".join(missing)}]
    seen = set()
    for n, raw in enumerate(reader, 2):
        err = []
        for key in REQUIRED_COLUMNS:
            raw[key] = (raw.get(key) or "").strip()
        code = raw["student_code"].strip()
        if not code: err.append("MSSV trống")
        for key,label in (("full_name","Họ tên"),("class_name","Lớp"),("course_code","Mã môn"),("course_name","Tên môn"),("semester_code","Mã học kỳ"),("semester_name","Tên học kỳ")):
            if not raw[key]: err.append(f"{label} trống")
        key = (code, raw["course_code"].strip(), raw["semester_code"].strip())
        if key in seen: err.append("Dòng ghi danh bị trùng trong tệp")
        seen.add(key)
        try:
            raw["score"] = float(raw["score"]); raw["attendance_rate"] = float(raw["attendance_rate"])
            raw["late_submissions"] = int(raw["late_submissions"]); raw["current_week"] = int(raw["current_week"])
            if not 0 <= raw["score"] <= 10: err.append("Điểm phải từ 0 đến 10")
            if not 0 <= raw["attendance_rate"] <= 100: err.append("Chuyên cần phải từ 0 đến 100")
            if raw["late_submissions"] < 0: err.append("Số lần nộp trễ không âm")
            if raw["current_week"] < 1: err.append("Tuần học không hợp lệ")
        except (ValueError, TypeError): err.append("Dữ liệu số không hợp lệ")
        if err: errors.append({"row": n, "message": "; ".join(err)})
        else: rows.append(raw)
    return rows, errors

def import_rows(rows, is_demo=False):
    count = 0
    try:
        for r in rows:
            student = Student.query.filter_by(student_code=r["student_code"]).first()
            if not student:
                student = Student(student_code=r["student_code"], full_name=r["full_name"], class_name=r["class_name"], email=r["email"] or None, is_demo=is_demo); db.session.add(student)
            course = Course.query.filter_by(code=r["course_code"]).first()
            if not course: course = Course(code=r["course_code"], name=r["course_name"]); db.session.add(course)
            semester = Semester.query.filter_by(code=r["semester_code"]).first()
            if not semester: semester = Semester(code=r["semester_code"], name=r["semester_name"], is_current=True); db.session.add(semester)
            db.session.flush()
            if Enrollment.query.filter_by(student_id=student.id, course_id=course.id, semester_id=semester.id).first(): raise ValueError(f"Ghi danh đã tồn tại: {r['student_code']}")
            db.session.add(Enrollment(student=student, course=course, semester=semester, current_week=r["current_week"], score=r["score"], attendance_rate=r["attendance_rate"], late_submissions=r["late_submissions"])); count += 1
        db.session.commit(); return count
    except Exception: db.session.rollback(); raise

def model_bundle():
    path = Path(current_app.config["MODEL_PATH"])
    if not path.exists(): raise FileNotFoundError("Chưa có model. Hãy chạy lệnh train-model.")
    bundle=_load_bundle(str(path),path.stat().st_mtime_ns)
    if not isinstance(bundle,dict) or "model" not in bundle or "metadata" not in bundle:
        raise ValueError("Tệp mô hình không đúng định dạng.")
    if bundle["metadata"].get("features") not in (None,FEATURES):
        raise ValueError("Bộ đặc trưng của mô hình không tương thích.")
    return bundle

@lru_cache(maxsize=4)
def _load_bundle(path, modified_ns):
    return joblib.load(path)

def recommendations_for(e):
    result=[]
    if e.score < 5: result.append(("DIEM", f"Điểm hiện tại {e.score:.1f}/10: ưu tiên ôn các nội dung còn yếu và hẹn cố vấn trong tuần này."))
    if e.attendance_rate < 80: result.append(("CHUYEN_CAN", f"Chuyên cần hiện tại {e.attendance_rate:.0f}%: lập kế hoạch tham dự để đạt tối thiểu 80%."))
    if e.late_submissions >= 2: result.append(("NOP_BAI", f"Đã nộp trễ {e.late_submissions} lần: chia nhỏ bài tập và đặt hạn nhắc trước 48 giờ."))
    if not result: result.append(("DUY_TRI", "Các chỉ số hiện ổn định; tiếp tục duy trì nhịp học và theo dõi hàng tuần."))
    return result

def predict_enrollment(e):
    bundle=model_bundle(); probability=float(bundle["model"].predict_proba(pd.DataFrame([[e.score,e.attendance_rate,e.late_submissions]],columns=FEATURES))[0][1])
    level=risk_level(probability); factors=[]
    if e.score<5: factors.append("điểm hiện tại thấp")
    if e.attendance_rate<80: factors.append("tỷ lệ chuyên cần thấp")
    if e.late_submissions>=2: factors.append("nhiều lần nộp bài trễ")
    p=Prediction(enrollment=e, probability=probability, risk_level=level, model_version=bundle["metadata"]["version"], factors_json=json.dumps(factors,ensure_ascii=False)); db.session.add(p); db.session.flush()
    Recommendation.query.filter_by(enrollment_id=e.id).delete()
    for cat,content in recommendations_for(e): db.session.add(Recommendation(enrollment_id=e.id,category=cat,content=content))
    created_alert=False
    if level=="CAO" and not Alert.query.filter(Alert.enrollment_id==e.id,Alert.status!="DA_XU_LY").first():
        db.session.add(Alert(enrollment=e,prediction_id=p.id,title=f"Nguy cơ dự báo cao: {e.student.student_code}")); created_alert=True
    db.session.commit()
    if created_alert and e.student.email:
        send_email(e.student.email,"Cảnh báo nguy cơ học tập",f"Hệ thống ghi nhận nguy cơ dự báo cao cho môn {e.course.name}. Xác suất mô hình: {probability:.1%}. Vui lòng liên hệ cố vấn để được hỗ trợ.")
    return p

def send_email(recipient, subject, body):
    cfg=current_app.config
    if not cfg.get("SMTP_HOST") or not cfg.get("SMTP_USERNAME"):
        db.session.add(EmailLog(recipient=recipient,subject=subject,status="DEV_PREVIEW",preview=body)); db.session.commit(); current_app.logger.info("EMAIL DEV %s | %s",recipient,body); return "DEV_PREVIEW"
    msg=EmailMessage(); msg["From"]=cfg["SMTP_FROM"]; msg["To"]=recipient; msg["Subject"]=subject; msg.set_content(body)
    with smtplib.SMTP(cfg["SMTP_HOST"],cfg["SMTP_PORT"],timeout=15) as server:
        if cfg["SMTP_USE_TLS"]: server.starttls()
        server.login(cfg["SMTP_USERNAME"],cfg["SMTP_PASSWORD"]); server.send_message(msg)
    db.session.add(EmailLog(recipient=recipient,subject=subject,status="SENT")); db.session.commit(); return "SENT"
