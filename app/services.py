import csv, io, json, os, re, smtplib, threading, time, uuid
from datetime import datetime, timezone
from functools import lru_cache
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
import joblib
import pandas as pd
from flask import current_app, render_template
from .extensions import db
from .models import Alert, AuditLog, Course, EmailLog, Enrollment, ImportBatch, ModelVersion, Prediction, Recommendation, Semester, Student, SystemSetting

FEATURES = ["score", "attendance_rate", "late_submissions"]
REQUIRED_COLUMNS = ["student_code", "full_name", "class_name", "email", "course_code", "course_name", "semester_code", "semester_name", "current_week", *FEATURES]
DEMO_EMAIL_SUBJECT = "[Cảnh báo học tập] Thông báo nguy cơ học tập"
DEMO_EMAIL_MESSAGE = """Xin chào sinh viên,

Hệ thống ghi nhận kết quả học tập hiện tại của bạn có một số chỉ số cần được lưu ý.

Môn học: Cơ sở dữ liệu
Tuần phân tích: Tuần 5
Mức nguy cơ: Cần theo dõi
Xác suất dự báo: 68%

Một số chỉ số học tập:
- Điểm hiện tại: 5.8
- Tỷ lệ chuyên cần: 72%
- Số lần nộp bài trễ: 2

Gợi ý:
- Ôn tập lại các nội dung chưa đạt yêu cầu.
- Cải thiện tỷ lệ tham gia lớp học.
- Hoàn thành bài tập đúng hạn.
- Trao đổi với cố vấn học tập nếu cần hỗ trợ.

Đây là cảnh báo hỗ trợ học tập được tạo từ hệ thống phân tích dữ liệu.
Kết quả dự báo mang tính hỗ trợ và không thay thế đánh giá của giảng viên hoặc cố vấn học tập.

Trân trọng,
Hệ thống phân tích kết quả học tập và dự báo nguy cơ trượt môn
Đại học Đại Nam"""

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
        try:
            raw["score"] = float(raw["score"]); raw["attendance_rate"] = float(raw["attendance_rate"])
            raw["late_submissions"] = int(raw["late_submissions"]); raw["current_week"] = int(raw["current_week"])
            if not 0 <= raw["score"] <= 10: err.append("Điểm phải từ 0 đến 10")
            if not 0 <= raw["attendance_rate"] <= 100: err.append("Chuyên cần phải từ 0 đến 100")
            if raw["late_submissions"] < 0: err.append("Số lần nộp trễ không âm")
            if not 1 <= raw["current_week"] <= 10: err.append("Tuần học phải nằm trong khoảng 1–10")
            key = (code, raw["course_code"].strip(), raw["semester_code"].strip(), raw["current_week"])
            if key in seen: err.append("Bản ghi sinh viên, môn học, học kỳ và tuần bị trùng trong tệp")
            seen.add(key)
        except (ValueError, TypeError): err.append("Dữ liệu số không hợp lệ")
        if err: errors.append({"row": n, "message": "; ".join(err)})
        else: rows.append(raw)
    return rows, errors

def import_rows(rows, is_demo=False, filename="uploaded.csv", imported_by=None):
    count = 0
    try:
        batch=ImportBatch(batch_id=str(uuid.uuid4()),filename=filename,data_type="DEMO" if is_demo else "USER",imported_by=imported_by,record_count=len(rows),success_count=len(rows),failed_count=0,status="COMPLETED") if imported_by else None
        if batch: db.session.add(batch)
        for r in rows:
            student = Student.query.filter_by(student_code=r["student_code"]).first()
            if not student:
                student = Student(student_code=r["student_code"], full_name=r["full_name"], class_name=r["class_name"], email=r["email"] or None, is_demo=is_demo); db.session.add(student)
            course = Course.query.filter_by(code=r["course_code"]).first()
            if not course: course = Course(code=r["course_code"], name=r["course_name"]); db.session.add(course)
            semester = Semester.query.filter_by(code=r["semester_code"]).first()
            if not semester: semester = Semester(code=r["semester_code"], name=r["semester_name"], is_current=True); db.session.add(semester)
            db.session.flush()
            if Enrollment.query.filter_by(student_id=student.id, course_id=course.id, semester_id=semester.id, current_week=r["current_week"]).first(): raise ValueError(f"Bản ghi đã tồn tại: {r['student_code']} / {r['course_code']} / tuần {r['current_week']}")
            db.session.add(Enrollment(student=student, course=course, semester=semester, current_week=r["current_week"], score=r["score"], attendance_rate=r["attendance_rate"], late_submissions=r["late_submissions"], import_batch=batch)); count += 1
        db.session.commit(); return (count, batch) if batch else count
    except Exception: db.session.rollback(); raise

def delete_enrollments(enrollments, actor, action, target, commit=True):
    ids=[e.id for e in enrollments]
    if ids:
        Alert.query.filter(Alert.enrollment_id.in_(ids)).delete(synchronize_session=False)
        Recommendation.query.filter(Recommendation.enrollment_id.in_(ids)).delete(synchronize_session=False)
    students={e.student for e in enrollments}; courses={e.course for e in enrollments}; semesters={e.semester for e in enrollments}
    for enrollment in enrollments: db.session.delete(enrollment)
    db.session.flush()
    for student in students:
        if not student.enrollments: db.session.delete(student)
    for course in courses:
        if not Enrollment.query.filter_by(course_id=course.id).first(): db.session.delete(course)
    for semester in semesters:
        if not Enrollment.query.filter_by(semester_id=semester.id).first(): db.session.delete(semester)
    db.session.add(AuditLog(user_id=actor.id,action=action,target=target))
    if commit: db.session.commit()

def delete_students(students, actor, action="DELETE_STUDENT"):
    """Atomically remove students and all dependent academic data."""
    students=list(dict.fromkeys(students)); enrollments=[e for student in students for e in list(student.enrollments)]
    try:
        delete_enrollments(enrollments,actor,action,",".join(s.student_code for s in students),commit=False)
        for student in students:
            if db.session.get(Student,student.id): db.session.delete(student)
        db.session.commit()
    except Exception:
        db.session.rollback(); raise

def setting_value(key, default=None):
    row=SystemSetting.query.filter_by(key=key).first()
    return row.value if row else default

def setting_bool(key, default=False):
    return str(setting_value(key,"1" if default else "0")).lower() in {"1","true","on","yes"}

def set_setting(key, value):
    row=SystemSetting.query.filter_by(key=key).first()
    if not row: row=SystemSetting(key=key,value=str(value)); db.session.add(row)
    else: row.value=str(value)
    db.session.commit(); return row.value

def automation_status():
    return {"prediction":setting_bool("auto_prediction_enabled",False),"email":setting_bool("auto_email_enabled",False),"interval_minutes":int(setting_value("auto_prediction_interval_minutes",5)),"last_run":setting_value("automation_last_run"),"scheduler":"running"}

def run_auto_pipeline(enrollment_ids):
    if not setting_bool("auto_prediction_enabled",False): return {"predictions":0,"alerts":0,"emails":0,"skipped":0,"failed":0}
    result={"predictions":0,"alerts":0,"emails":0,"skipped":0,"failed":0}; auto_email=setting_bool("auto_email_enabled",False)
    for enrollment in Enrollment.query.filter(Enrollment.id.in_(list(enrollment_ids)),Enrollment.current_week>=5).all():
        try:
            before=Alert.query.filter_by(enrollment_id=enrollment.id).count(); predict_enrollment(enrollment,send_auto_email=auto_email); result["predictions"]+=1; after=Alert.query.filter_by(enrollment_id=enrollment.id).count(); result["alerts"]+=max(0,after-before)
            if not auto_email: result["skipped"]+=1
        except Exception:
            db.session.rollback(); result["failed"]+=1
    set_setting("automation_last_run",datetime.now(timezone.utc).isoformat())
    return result

def start_scheduler(app):
    if app.testing or getattr(app,"_automation_scheduler_started",False): return
    app._automation_scheduler_started=True
    def loop():
        while True:
            with app.app_context():
                interval=max(1,int(setting_value("auto_prediction_interval_minutes",5))) * 60
                if setting_bool("auto_prediction_enabled",False):
                    pending=Enrollment.query.filter(Enrollment.current_week>=5,~Enrollment.predictions.any()).with_entities(Enrollment.id).all()
                    if pending: run_auto_pipeline([row[0] for row in pending])
                else: interval=max(60,interval)
            time.sleep(interval)
    threading.Thread(target=loop,name="automation-scheduler",daemon=True).start()

def model_bundle():
    path = Path(current_app.config["MODEL_PATH"])
    if not path.exists(): raise FileNotFoundError("Chưa có model. Hãy chạy lệnh train-model.")
    bundle=_load_bundle(str(path),path.stat().st_mtime_ns)
    if not isinstance(bundle,dict) or "model" not in bundle or "metadata" not in bundle:
        raise ValueError("Tệp mô hình không đúng định dạng.")
    if bundle["metadata"].get("features") not in (None,FEATURES):
        raise ValueError("Bộ đặc trưng của mô hình không tương thích.")
    # Keep inference inside the Flask worker.  Some Windows deployments cannot
    # create Joblib worker handles from a web request.
    if hasattr(bundle["model"], "n_jobs"):
        bundle["model"].n_jobs = 1
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

def predict_enrollment(e, send_auto_email=True):
    if e.current_week < 5:
        raise ValueError("Chưa đủ dữ liệu để thực hiện dự báo từ tuần 5.")
    bundle=model_bundle(); probability=float(bundle["model"].predict_proba(pd.DataFrame([[e.score,e.attendance_rate,e.late_submissions]],columns=FEATURES))[0][1])
    level=risk_level(probability); factors=[]
    if e.score<5: factors.append("điểm hiện tại thấp")
    if e.attendance_rate<80: factors.append("tỷ lệ chuyên cần thấp")
    if e.late_submissions>=2: factors.append("nhiều lần nộp bài trễ")
    version=bundle["metadata"]["version"]
    p=Prediction.query.filter_by(enrollment_id=e.id,model_version=version).order_by(Prediction.id.desc()).first()
    if p:
        p.probability=probability; p.risk_level=level; p.factors_json=json.dumps(factors,ensure_ascii=False); p.week_number=e.current_week
    else:
        p=Prediction(enrollment=e, probability=probability, risk_level=level, model_version=version, factors_json=json.dumps(factors,ensure_ascii=False), week_number=e.current_week); db.session.add(p)
    db.session.flush()
    Recommendation.query.filter_by(enrollment_id=e.id).delete()
    for cat,content in recommendations_for(e): db.session.add(Recommendation(enrollment_id=e.id,category=cat,content=content))
    created_alert=False
    if level=="CAO" and not Alert.query.filter(Alert.enrollment_id==e.id,Alert.status!="DA_XU_LY").first() and not Alert.query.filter_by(enrollment_id=e.id,prediction_id=p.id).first():
        title=f"{e.student.full_name} có nguy cơ trượt môn {e.course.name} ở tuần {e.current_week}."
        db.session.add(Alert(enrollment=e,prediction_id=p.id,title=title)); created_alert=True
    db.session.commit()
    if created_alert and e.student.email and not send_auto_email:
        db.session.add(EmailLog(recipient=e.student.email,subject="AUTO_EMAIL_DISABLED",status="SKIPPED",preview="AUTO_EMAIL_DISABLED")); db.session.commit()
    if created_alert and e.student.email and send_auto_email:
        subject=f"[Cảnh báo học tập] Nguy cơ học tập - {e.course.name}"
        context={"student":e.student,"enrollment":e,"prediction":p,"recommendations":Recommendation.query.filter_by(enrollment_id=e.id).all()}
        plain=render_template("email/risk_alert.txt",**context)
        html=render_template("email/risk_alert.html",**context)
        send_email(e.student.email,subject,plain,html_body=html)
    return p

def smtp_configured():
    return current_app.config.get("MAIL_MODE") == "smtp" and all(current_app.config.get(key) for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"))

def valid_email(recipient):
    value=(recipient or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",value): return False
    domain=value.rsplit("@",1)[1].lower()
    return not (domain.endswith(".test") or domain in {"example.invalid","localhost"})

def send_email(recipient, subject, body, html_body=None):
    cfg=current_app.config
    recipient=(recipient or "").strip()
    if not valid_email(recipient):
        db.session.add(EmailLog(recipient=recipient or "unknown",subject=subject,status="SKIPPED",preview="Recipient không hợp lệ hoặc thuộc domain demo.")); db.session.commit(); return "SKIPPED"
    if not smtp_configured():
        db.session.add(EmailLog(recipient=recipient,subject=subject,status="DEV_PREVIEW",preview=body)); db.session.commit(); current_app.logger.info("EMAIL DEV_PREVIEW recipient=%s subject=%s",recipient,subject); return "DEV_PREVIEW"
    msg=EmailMessage(); msg["From"]=formataddr((cfg.get("MAIL_FROM_NAME"),cfg["SMTP_FROM"])); msg["To"]=recipient; msg["Subject"]=subject; msg.set_content(body)
    if html_body: msg.add_alternative(html_body,subtype="html")
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"],cfg["SMTP_PORT"],timeout=15) as server:
            if cfg["SMTP_USE_TLS"]: server.starttls()
            server.login(cfg["SMTP_USERNAME"],cfg["SMTP_PASSWORD"]); server.send_message(msg)
    except (OSError, smtplib.SMTPException):
        db.session.add(EmailLog(recipient=recipient,subject=subject,status="FAILED",preview=body)); db.session.commit()
        current_app.logger.warning("Email delivery failed; prediction remains saved.")
        return "FAILED"
    db.session.add(EmailLog(recipient=recipient,subject=subject,status="SENT")); db.session.commit(); return "SENT"

def send_demo_email(recipient, subject, message):
    """Send an admin-authored demo message without touching prediction data."""
    html=render_template("email/demo_notification.html", message=message)
    return send_email(recipient, subject, message, html_body=html)
