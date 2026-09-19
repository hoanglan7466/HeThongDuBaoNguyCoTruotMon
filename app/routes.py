import csv, io, json, uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse
from functools import wraps
from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename
from .extensions import db
from .models import Alert, AuditLog, Course, EmailLog, Enrollment, ImportBatch, ModelVersion, Prediction, Recommendation, Semester, Student, User
from .services import REQUIRED_COLUMNS, delete_enrollments, import_rows, model_bundle, predict_enrollment, validate_csv, smtp_configured

bp=Blueprint("main",__name__)

def _preview_path(token):
    folder=Path(current_app.instance_path)/"import_previews"; folder.mkdir(parents=True,exist_ok=True)
    return folder/f"{token}.json"

def _save_preview(rows, filename):
    token=str(uuid.uuid4()); path=_preview_path(token)
    path.write_text(json.dumps({"filename":filename,"rows":rows},ensure_ascii=False),encoding="utf-8")
    session["import_preview_token"]=token

def _pop_preview():
    token=session.pop("import_preview_token",None)
    if not token: return None
    path=_preview_path(token)
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError): return None
    finally: path.unlink(missing_ok=True)

def _import_summary(rows):
    return {
        "records":len(rows),
        "students":len({row["student_code"] for row in rows}),
        "courses":len({row["course_code"] for row in rows}),
        "weeks":sorted({int(row["current_week"]) for row in rows}),
    }
def _safe_next(target):
    if not target: return False
    host=urlparse(request.host_url)
    candidate=urlparse(urljoin(request.host_url,target or ""))
    return candidate.scheme in {"http","https"} and candidate.netloc==host.netloc
def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*a,**kw):
        if current_user.role!="ADMIN": abort(403)
        return fn(*a,**kw)
    return wrapped

@bp.route("/login",methods=["GET","POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("main.dashboard"))
    if request.method=="POST":
        user=User.query.filter_by(username=request.form.get("username","").strip()).first()
        if user and user.check_password(request.form.get("password","")) and user.active:
            target=request.args.get("next")
            login_user(user); return redirect(target if _safe_next(target) else url_for("main.dashboard"))
        flash("Tên đăng nhập hoặc mật khẩu không đúng.","error")
    return render_template("login.html",show_demo_accounts=current_app.config["SHOW_DEMO_ACCOUNTS"])

@bp.post("/logout")
@login_required
def logout(): logout_user(); return redirect(url_for("main.login"))

@bp.get("/")
@login_required
def dashboard():
    course_id=request.args.get("course_id",type=int); semester_id=request.args.get("semester_id",type=int)
    scope=Enrollment.query
    if course_id: scope=scope.filter(Enrollment.course_id==course_id)
    if semester_id: scope=scope.filter(Enrollment.semester_id==semester_id)
    scoped_ids=[row[0] for row in scope.with_entities(Enrollment.id).all()]
    total=scope.with_entities(Enrollment.student_id).distinct().count(); latest={}
    predictions=Prediction.query.filter(Prediction.enrollment_id.in_(scoped_ids)) if scoped_ids else Prediction.query.filter(db.false())
    for p in predictions.options(joinedload(Prediction.enrollment).joinedload(Enrollment.course)).order_by(Prediction.enrollment_id,Prediction.created_at.desc(),Prediction.id.desc()).all(): latest.setdefault(p.enrollment_id,p)
    counts={"CAO":0,"TRUNG_BINH":0,"ON_DINH":0}
    for p in latest.values(): counts[p.risk_level]+=1
    latest_predictions=list(latest.values())
    attention=sorted(latest_predictions,key=lambda p:p.probability,reverse=True)[:5]
    weekly={week:{"CAO":0,"TRUNG_BINH":0,"ON_DINH":0} for week in range(5,13)}
    for prediction in latest_predictions:
        week=prediction.week_number
        if week in weekly: weekly[week][prediction.risk_level]+=1
    attendance={}
    for enrollment in scope.options(joinedload(Enrollment.course)).all():
        bucket=attendance.setdefault(enrollment.course.name,{"total":0,"count":0})
        bucket["total"]+=enrollment.attendance_rate; bucket["count"]+=1
    attendance_by_course=[{"name":name,"rate":round(values["total"]/values["count"],1)} for name,values in attendance.items()]
    latest_prediction=max(latest_predictions,key=lambda p:p.created_at,default=None)
    latest_alert=Alert.query.order_by(Alert.created_at.desc()).first()
    active_model=ModelVersion.query.filter_by(is_active=True).order_by(ModelVersion.trained_at.desc()).first()
    activities=[]
    if active_model: activities.append({"icon":"cpu","title":"Mô hình đã được huấn luyện","detail":active_model.version,"time":active_model.trained_at})
    if latest_prediction: activities.append({"icon":"sparkles","title":"Đã chạy dự báo học tập","detail":f"{len(latest_predictions)} ghi danh có dự báo mới nhất","time":latest_prediction.created_at})
    if latest_alert: activities.append({"icon":"bell-ring","title":"Cảnh báo mới nhất","detail":latest_alert.title,"time":latest_alert.created_at})
    semester=Semester.query.filter_by(is_current=True).order_by(Semester.id.desc()).first()
    model_ready=False
    try: model_bundle(); model_ready=True
    except (FileNotFoundError, ValueError): pass
    is_demo=Student.query.filter_by(is_demo=True).count()>0
    alerts_query=Alert.query.join(Enrollment).filter(Enrollment.id.in_(scoped_ids)) if scoped_ids else Alert.query.filter(db.false())
    return render_template("dashboard.html",total=total,counts=counts,attention=attention,alerts=alerts_query.options(joinedload(Alert.enrollment).joinedload(Enrollment.student),joinedload(Alert.enrollment).joinedload(Enrollment.course)).order_by(Alert.created_at.desc()).limit(5).all(),is_demo=is_demo,semester=semester,weekly=weekly,attendance_by_course=attendance_by_course,activities=activities,active_model=active_model,model_ready=model_ready,email_status="ĐÃ CẤU HÌNH" if smtp_configured() else "DEV MODE",courses=Course.query.order_by(Course.code).all(),semesters=Semester.query.order_by(Semester.code.desc()).all(),course_id=course_id,semester_id=semester_id)

@bp.get("/students")
@login_required
def students():
    q=Student.query; term=request.args.get("q","").strip(); class_name=request.args.get("class_name","").strip(); course_id=request.args.get("course_id",type=int); semester_id=request.args.get("semester_id",type=int); sort=request.args.get("sort","code")
    if term: q=q.filter(db.or_(Student.student_code.contains(term),Student.full_name.contains(term)))
    if class_name: q=q.filter(Student.class_name==class_name)
    if course_id or semester_id:
        q=q.join(Enrollment)
        if course_id: q=q.filter(Enrollment.course_id==course_id)
        if semester_id: q=q.filter(Enrollment.semester_id==semester_id)
    order={"name":Student.full_name,"class":Student.class_name}.get(sort,Student.student_code)
    page=q.distinct().order_by(order).paginate(page=request.args.get("page",1,type=int),per_page=15,error_out=False)
    classes=[r[0] for r in db.session.query(Student.class_name).distinct().order_by(Student.class_name)]
    return render_template("students.html",page=page,total_students=Student.query.count(),term=term,class_name=class_name,course_id=course_id,semester_id=semester_id,sort=sort,classes=classes,courses=Course.query.order_by(Course.code).all(),semesters=Semester.query.order_by(Semester.code.desc()).all())

@bp.get("/academic-data")
@login_required
def academic_data():
    page=Enrollment.query.options(joinedload(Enrollment.student),joinedload(Enrollment.course),joinedload(Enrollment.semester)).order_by(Enrollment.id.desc()).paginate(page=request.args.get("page",1,type=int),per_page=20,error_out=False)
    return render_template("academic_data.html",page=page)

@bp.get("/analysis")
@login_required
def analysis():
    semesters=Semester.query.order_by(Semester.is_current.desc(),Semester.code.desc()).all()
    semester_id=request.args.get("semester_id",type=int) or (semesters[0].id if semesters else None)
    course_query=Course.query.join(Enrollment)
    if semester_id: course_query=course_query.filter(Enrollment.semester_id==semester_id)
    courses=course_query.distinct().order_by(Course.code).all()
    course_id=request.args.get("course_id",type=int) or (courses[0].id if courses else None)
    available_weeks_query=db.session.query(Enrollment.current_week)
    if semester_id: available_weeks_query=available_weeks_query.filter(Enrollment.semester_id==semester_id)
    if course_id: available_weeks_query=available_weeks_query.filter(Enrollment.course_id==course_id)
    available_weeks=sorted({row[0] for row in available_weeks_query.all()})
    week=request.args.get("week",type=int)
    if week is None: week=max(available_weeks,default=None)
    term=request.args.get("q","").strip()
    query=Enrollment.query.options(joinedload(Enrollment.student),joinedload(Enrollment.course),joinedload(Enrollment.semester),selectinload(Enrollment.predictions))
    if semester_id: query=query.filter(Enrollment.semester_id==semester_id)
    if course_id: query=query.filter(Enrollment.course_id==course_id)
    if week is not None: query=query.filter(Enrollment.current_week==week)
    if term: query=query.join(Student).filter(db.or_(Student.student_code.contains(term),Student.full_name.contains(term)))
    enrollments=query.all(); rows=[]; counts={"CAO":0,"TRUNG_BINH":0,"ON_DINH":0}
    for enrollment in enrollments:
        latest=max(enrollment.predictions,key=lambda p:(p.created_at,p.id),default=None)
        if latest: counts[latest.risk_level]+=1
        rows.append({"enrollment":enrollment,"prediction":latest})
    rank={"CAO":0,"TRUNG_BINH":1,"ON_DINH":2}
    rows.sort(key=lambda row:(rank.get(row["prediction"].risk_level,3) if row["prediction"] else 3,-(row["prediction"].probability if row["prediction"] else -1),row["enrollment"].student.student_code))
    return render_template("analysis.html",rows=rows,counts=counts,total=len({e.student_id for e in enrollments}),has_data=Enrollment.query.count()>0,semesters=semesters,courses=courses,available_weeks=available_weeks,semester_id=semester_id,course_id=course_id,week=week,term=term)

@bp.get("/students/<int:student_id>")
@login_required
def student_detail(student_id):
    s=db.get_or_404(Student,student_id)
    recs=Recommendation.query.join(Enrollment).filter(Enrollment.student_id==student_id).order_by(Recommendation.created_at.desc()).all()
    student_alerts=Alert.query.join(Enrollment).filter(Enrollment.student_id==student_id).order_by(Alert.created_at.desc()).all()
    return render_template("student_detail.html",student=s,recommendations=recs,alerts=student_alerts)

@bp.post("/predict/<int:enrollment_id>")
@login_required
def predict(enrollment_id):
    e=db.get_or_404(Enrollment,enrollment_id)
    if e.current_week<5: flash("Chưa đủ dữ liệu để thực hiện dự báo từ tuần 5.","error")
    else:
        try:
            p=predict_enrollment(e); label={"CAO":"Nguy cơ cao","TRUNG_BINH":"Cần theo dõi","ON_DINH":"Ổn định"}[p.risk_level]; flash(f"Đã lưu dự báo: {label} ({p.probability:.1%}).","success")
        except (FileNotFoundError, ValueError) as ex: flash(str(ex),"error")
    return redirect(url_for("main.student_detail",student_id=e.student_id))

@bp.post("/predict/batch")
@login_required
def predict_batch():
    semester_id=request.form.get("semester_id",type=int); course_id=request.form.get("course_id",type=int); week=request.form.get("week",type=int)
    redirect_args={"semester_id":semester_id,"course_id":course_id,"week":week}
    if not semester_id or not course_id or week is None:
        flash("Vui lòng chọn học kỳ, môn học và tuần.","error"); return redirect(url_for("main.analysis",**redirect_args))
    if week<5:
        flash("Chưa đủ dữ liệu để dự báo. Hệ thống bắt đầu dự báo nguy cơ từ tuần 5.","error"); return redirect(url_for("main.analysis",**redirect_args))
    enrollments=Enrollment.query.filter_by(semester_id=semester_id,course_id=course_id,current_week=week).all()
    if not enrollments:
        flash("Chưa có dữ liệu để dự báo cho bộ lọc này.","error"); return redirect(url_for("main.analysis",**redirect_args))
    done=skipped=0
    for e in enrollments:
        try: predict_enrollment(e); done+=1
        except (FileNotFoundError,ValueError):
            db.session.rollback(); current_app.logger.exception("Không thể tải hoặc chạy mô hình dự báo"); flash("Không thể tải mô hình dự báo.","error"); return redirect(url_for("main.analysis",**redirect_args))
        except Exception:
            db.session.rollback(); current_app.logger.exception("Prediction failed for enrollment %s",e.id); skipped+=1
    flash(f"Đã hoàn thành dự báo cho {done} sinh viên" + (f"; bỏ qua {skipped}." if skipped else "."),"success")
    return redirect(url_for("main.analysis",**redirect_args))

@bp.route("/data/import",methods=["GET","POST"])
@admin_required
def import_data():
    preview=[]; errors=[]; filename=None
    if request.method=="POST":
        f=request.files.get("file")
        safe_name=secure_filename(f.filename) if f and f.filename else ""
        if not f or not safe_name.lower().endswith(".csv"): flash("Chỉ chấp nhận tệp CSV.","error")
        else:
            rows,errors=validate_csv(f.stream)
            filename=safe_name
            if not errors:
                _save_preview(rows,safe_name); preview=rows; flash(f"Đã kiểm tra {len(rows)} dòng hợp lệ. Hãy xác nhận nhập.","success")
    summary={"students":Student.query.count(),"courses":Course.query.count(),"records":Enrollment.query.count(),"latest":ImportBatch.query.order_by(ImportBatch.imported_at.desc()).first()}
    demo_file=Path(current_app.root_path).parent/"demo_data"/"du_lieu_sinh_vien_demo.csv"
    demo_rows,demo_errors=validate_csv(demo_file.open("rb")) if demo_file.exists() else ([],[{"message":"Không tìm thấy dữ liệu demo."}])
    return render_template("import.html",preview=preview[:15],preview_summary=_import_summary(preview) if preview else None,preview_filename=filename,errors=errors[:20],error_count=len(errors),demo_count=Student.query.filter_by(is_demo=True).count(),demo_summary=_import_summary(demo_rows) if not demo_errors else None,batches=ImportBatch.query.order_by(ImportBatch.imported_at.desc()).all(),summary=summary)

@bp.post("/data/import/confirm")
@admin_required
def import_confirm():
    payload=_pop_preview()
    if not payload: flash("Phiên xem trước đã hết hạn.","error")
    else:
        try:
            count,batch=import_rows(payload["rows"],filename=payload["filename"],imported_by=current_user.id)
            db.session.add(AuditLog(user_id=current_user.id,action="IMPORT_DATA",target=batch.batch_id)); db.session.commit()
            flash(f"Đã nhập {count} bản ghi (batch {batch.batch_id[:8]}).","success")
        except ValueError as e: flash(str(e),"error")
    return redirect(url_for("main.import_data"))

@bp.get("/data/template.csv")
@login_required
def csv_template():
    out=io.StringIO(); w=csv.writer(out); w.writerow(REQUIRED_COLUMNS); w.writerow(["SV001","Nguyễn Văn A","CNTT01","sv001@example.test","ML101","Học máy","2026A","Học kỳ 1 2026",5,7.5,90,0])
    return Response(out.getvalue().encode("utf-8-sig"),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=mau_import.csv"})

@bp.post("/data/seed-demo")
@admin_required
def seed_demo():
    if Student.query.filter_by(is_demo=True).first(): flash("Dữ liệu demo đã tồn tại.","error")
    else:
        demo_file=Path(current_app.root_path).parent/"demo_data"/"du_lieu_sinh_vien_demo.csv"
        rows,errors=validate_csv(demo_file.open("rb"))
        if errors: flash("Tệp dữ liệu DEMO không hợp lệ.","error"); return redirect(url_for("main.import_data"))
        _,batch=import_rows(rows,is_demo=True,filename=demo_file.name,imported_by=current_user.id)
        db.session.add(AuditLog(user_id=current_user.id,action="LOAD_DEMO",target=batch.batch_id)); db.session.commit()
        predicted=0
        try:
            for enrollment in Enrollment.query.filter(Enrollment.import_batch_id==batch.id, Enrollment.current_week >= 5).all():
                predict_enrollment(enrollment); predicted+=1
            flash(f"Đã tải dữ liệu demo và tạo {predicted} lượt dự báo.","success")
        except FileNotFoundError:
            flash("Đã tải dữ liệu demo. Cần huấn luyện mô hình trước khi chạy dự báo.","success")
    return redirect(url_for("main.dashboard"))

@bp.post("/data/reset-demo")
@admin_required
def reset_demo():
    batches=ImportBatch.query.filter_by(data_type="DEMO").all()
    legacy_students=Student.query.filter_by(is_demo=True).filter(~Student.enrollments.any(Enrollment.import_batch_id.isnot(None))).all()
    if not batches and not legacy_students:
        flash("Không có dữ liệu DEMO để xóa.","error")
        return redirect(url_for("main.import_data"))
    enrollments=Enrollment.query.filter(Enrollment.import_batch_id.in_([b.id for b in batches])).all() if batches else []
    enrollments += [e for student in legacy_students for e in student.enrollments]
    removed=len({e.student_id for e in enrollments}) + len([s for s in legacy_students if not s.enrollments])
    delete_enrollments(enrollments,current_user,"RESET_DEMO","demo batches",commit=False)
    for student in legacy_students:
        if db.session.get(Student,student.id): db.session.delete(student)
    ImportBatch.query.filter_by(data_type="DEMO").delete(synchronize_session=False); db.session.commit()
    flash(f"Đã xóa an toàn {removed} sinh viên DỮ LIỆU DEMO.","success")
    return redirect(url_for("main.import_data"))

@bp.post("/data/batches/<int:batch_id>/delete")
@admin_required
def delete_batch(batch_id):
    batch=db.get_or_404(ImportBatch,batch_id); enrollments=list(batch.enrollments)
    delete_enrollments(enrollments,current_user,"DELETE_IMPORT_BATCH",batch.filename,commit=False)
    db.session.delete(batch); db.session.commit(); flash("Đã xóa dữ liệu thuộc batch đã chọn.","success")
    return redirect(url_for("main.import_data"))

@bp.route("/students/add",methods=["GET","POST"])
@admin_required
def add_student():
    if request.method=="POST":
        rows=[{key:request.form.get(key,"").strip() for key in REQUIRED_COLUMNS}]
        rows[0]["score"]=request.form.get("score",""); rows[0]["attendance_rate"]=request.form.get("attendance_rate",""); rows[0]["late_submissions"]=request.form.get("late_submissions",""); rows[0]["current_week"]=request.form.get("current_week","")
        payload=io.BytesIO((",".join(REQUIRED_COLUMNS)+"\n"+",".join(str(rows[0][k]) for k in REQUIRED_COLUMNS)+"\n").encode())
        clean,errors=validate_csv(payload)
        if errors: flash(errors[0]["message"],"error")
        else:
            try: import_rows(clean,filename="manual-entry",imported_by=current_user.id); flash("Đã thêm sinh viên.","success"); return redirect(url_for("main.students"))
            except ValueError as e: flash(str(e),"error")
    return render_template("student_form.html",student=None,enrollment=None,courses=Course.query.all(),semesters=Semester.query.all())

@bp.route("/students/<int:student_id>/edit",methods=["GET","POST"])
@admin_required
def edit_student(student_id):
    student=db.get_or_404(Student,student_id)
    if request.method=="POST":
        full_name=request.form.get("full_name","").strip(); class_name=request.form.get("class_name","").strip(); email=request.form.get("email","").strip() or None
        if not full_name or not class_name:
            flash("Họ tên và lớp là bắt buộc.","error")
        else:
            student.full_name=full_name; student.class_name=class_name; student.email=email; db.session.commit()
            flash("Đã cập nhật sinh viên.","success"); return redirect(url_for("main.student_detail",student_id=student.id))
    return render_template("student_edit.html",student=student)

@bp.post("/students/<int:student_id>/delete")
@admin_required
def delete_student(student_id):
    student=db.get_or_404(Student,student_id); delete_enrollments(list(student.enrollments),current_user,"DELETE_STUDENT",student.student_code)
    return redirect(url_for("main.students"))

@bp.post("/students/delete-selected")
@admin_required
def delete_selected_students():
    ids=[int(value) for value in request.form.getlist("student_ids") if value.isdigit()]
    selected=Student.query.filter(Student.id.in_(ids)).all()
    delete_enrollments([e for s in selected for e in s.enrollments],current_user,"DELETE_STUDENTS",str(len(selected)))
    flash(f"Đã xóa {len(selected)} sinh viên.","success"); return redirect(url_for("main.students"))

@bp.get("/alerts")
@login_required
def alerts():
    status=request.args.get("status","")
    query=Alert.query.options(joinedload(Alert.enrollment).joinedload(Enrollment.student),joinedload(Alert.enrollment).joinedload(Enrollment.course))
    if status in {"MOI","DA_XEM","DA_XU_LY"}: query=query.filter(Alert.status==status)
    return render_template("alerts.html",alerts=query.order_by(Alert.created_at.desc()).all(),status=status)

@bp.post("/alerts/<int:alert_id>/status")
@login_required
def alert_status(alert_id):
    a=db.get_or_404(Alert,alert_id); status=request.form.get("status")
    if status not in {"MOI","DA_XEM","DA_XU_LY"}: abort(400)
    a.status=status; db.session.commit(); return redirect(url_for("main.alerts"))

@bp.post("/api/alerts/bulk-status")
@login_required
def bulk_alert_status():
    payload=request.get_json(silent=True) or {}; updates=payload.get("updates")
    if not isinstance(updates,list) or not updates: return jsonify(ok=False,message="Dữ liệu thay đổi không hợp lệ."),400
    valid={"MOI","DA_XEM","DA_XU_LY"}; ids=[]
    try:
        for item in updates:
            if not isinstance(item,dict) or not isinstance(item.get("id"),int) or item.get("status") not in valid: raise ValueError
            ids.append(item["id"])
    except ValueError: return jsonify(ok=False,message="Trạng thái không hợp lệ."),400
    alerts=Alert.query.filter(Alert.id.in_(ids)).all()
    if len(alerts)!=len(set(ids)): return jsonify(ok=False,message="Không tìm thấy cảnh báo."),404
    try:
        wanted={item["id"]:item["status"] for item in updates}
        for alert in alerts: alert.status=wanted[alert.id]
        db.session.commit()
    except Exception:
        db.session.rollback(); return jsonify(ok=False,message="Không thể lưu thay đổi."),500
    return jsonify(ok=True,updated=len(alerts),new_count=Alert.query.filter_by(status="MOI").count())

@bp.route("/account/password",methods=["GET","POST"])
@login_required
def change_password():
    if request.method=="POST":
        current=request.form.get("current_password",""); new=request.form.get("new_password",""); confirm=request.form.get("confirm_password","")
        if not current_user.check_password(current): flash("Mật khẩu hiện tại không đúng.","error")
        elif len(new)<8 or new!=confirm: flash("Mật khẩu mới tối thiểu 8 ký tự và phải khớp.","error")
        else: current_user.set_password(new); db.session.commit(); flash("Đã đổi mật khẩu.","success"); return redirect(url_for("main.dashboard"))
    return render_template("change_password.html")

def _report_query():
    query=Prediction.query.join(Enrollment).join(Student).join(Course).join(Semester)
    level=request.args.get("risk","").strip()
    term=request.args.get("q","").strip()
    course_id=request.args.get("course_id",type=int)
    semester_id=request.args.get("semester_id",type=int)
    if level in {"CAO","TRUNG_BINH","ON_DINH"}: query=query.filter(Prediction.risk_level==level)
    if term: query=query.filter(db.or_(Student.student_code.contains(term),Student.full_name.contains(term)))
    if course_id: query=query.filter(Enrollment.course_id==course_id)
    if semester_id: query=query.filter(Enrollment.semester_id==semester_id)
    return query.order_by(Prediction.created_at.desc()),level,term,course_id,semester_id

@bp.get("/reports")
@login_required
def reports():
    query,level,term,course_id,semester_id=_report_query()
    return render_template("reports.html",predictions=query.all(),risk=level,term=term,course_id=course_id,semester_id=semester_id,courses=Course.query.order_by(Course.code).all(),semesters=Semester.query.order_by(Semester.code.desc()).all())

@bp.get("/reports/export.csv")
@login_required
def export_report():
    out=io.StringIO(); w=csv.writer(out); w.writerow(["MSSV","Họ tên","Môn","Học kỳ","Tuần dự báo","Xác suất","Mức nguy cơ","Phiên bản","Thời gian"])
    query,*_=_report_query()
    for p in query.all(): w.writerow([p.enrollment.student.student_code,p.enrollment.student.full_name,p.enrollment.course.name,p.enrollment.semester.name,p.week_number,f"{p.probability:.4f}",p.risk_level,p.model_version,p.created_at.isoformat()])
    return Response(out.getvalue().encode("utf-8-sig"),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=bao_cao_nguy_co.csv"})

@bp.get("/model")
@login_required
def model_info():
    record=ModelVersion.query.filter_by(is_active=True).order_by(ModelVersion.trained_at.desc()).first()
    metadata=algorithm=None
    try:
        bundle=model_bundle(); metadata=bundle["metadata"]; algorithm=bundle["model"].__class__.__name__
    except (FileNotFoundError,ValueError): pass
    return render_template("model.html",model=record,metadata=metadata,algorithm=algorithm)

@bp.route("/admin/users",methods=["GET","POST"])
@admin_required
def users():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        full_name=request.form.get("full_name","").strip()
        email=request.form.get("email","").strip() or None
        role=request.form.get("role","")
        password=request.form.get("password","")
        if not (3<=len(username)<=80) or not full_name or role not in {"ADMIN","COVAN"} or len(password)<8:
            flash("Kiểm tra tên đăng nhập, họ tên, vai trò và mật khẩu tối thiểu 8 ký tự.","error")
        elif User.query.filter(db.or_(User.username==username, User.email==email if email else db.false())).first():
            flash("Tên đăng nhập hoặc email đã tồn tại.","error")
        else:
            user=User(username=username,full_name=full_name,email=email,role=role); user.set_password(password); db.session.add(user); db.session.commit(); flash("Đã tạo tài khoản.","success")
        return redirect(url_for("main.users"))
    return render_template("users.html",users=User.query.order_by(User.username).all())

@bp.post("/admin/users/<int:user_id>/update")
@admin_required
def update_user(user_id):
    user=db.get_or_404(User,user_id)
    full_name=request.form.get("full_name","").strip(); email=request.form.get("email","").strip() or None; role=request.form.get("role",""); password=request.form.get("password","")
    duplicate=User.query.filter(User.id!=user.id, User.email==email).first() if email else None
    active_admins=User.query.filter_by(role="ADMIN",active=True).count()
    if not full_name or role not in {"ADMIN","COVAN"} or duplicate or (password and len(password)<8): flash("Không thể cập nhật: kiểm tra họ tên, email hoặc mật khẩu.","error")
    elif user.role=="ADMIN" and role!="ADMIN" and user.active and active_admins<=1: flash("Phải còn ít nhất một ADMIN đang hoạt động.","error")
    else:
        user.full_name,user.email,user.role=full_name,email,role
        if password: user.set_password(password)
        db.session.commit(); flash("Đã cập nhật tài khoản.","success")
    return redirect(url_for("main.users"))

@bp.post("/admin/users/<int:user_id>/toggle")
@admin_required
def toggle_user(user_id):
    user=db.get_or_404(User,user_id)
    if user.id==current_user.id: flash("Không thể khóa tài khoản đang đăng nhập.","error")
    elif user.active and user.role=="ADMIN" and User.query.filter_by(role="ADMIN",active=True).count()<=1: flash("Phải còn ít nhất một ADMIN đang hoạt động.","error")
    else:
        user.active=not user.active; db.session.commit(); flash("Đã cập nhật trạng thái tài khoản.","success")
    return redirect(url_for("main.users"))

@bp.get("/settings")
@admin_required
def settings():
    bundle=None
    try: bundle=model_bundle()
    except (FileNotFoundError,ValueError): pass
    return render_template("settings.html",database=current_app.config["SQLALCHEMY_DATABASE_URI"].split(":",1)[0],smtp="Đã cấu hình" if smtp_configured() else "DEV preview",model_ready=bundle is not None)

@bp.get("/api/dashboard")
@login_required
def dashboard_api():
    course_id=request.args.get("course_id",type=int); semester_id=request.args.get("semester_id",type=int)
    scope=Enrollment.query
    if course_id: scope=scope.filter(Enrollment.course_id==course_id)
    if semester_id: scope=scope.filter(Enrollment.semester_id==semester_id)
    ids=[row[0] for row in scope.with_entities(Enrollment.id).all()]
    latest={}
    predictions=Prediction.query.filter(Prediction.enrollment_id.in_(ids) if ids else db.false()).options(joinedload(Prediction.enrollment).joinedload(Enrollment.student),joinedload(Prediction.enrollment).joinedload(Enrollment.course)).order_by(Prediction.enrollment_id,Prediction.created_at.desc(),Prediction.id.desc()).all()
    for prediction in predictions: latest.setdefault(prediction.enrollment_id,prediction)
    values=list(latest.values())
    weekly={str(week):{"high":0,"medium":0,"stable":0} for week in range(5,13)}
    levels={"CAO":"high","TRUNG_BINH":"medium","ON_DINH":"stable"}
    for prediction in values:
        if prediction.week_number in range(5,13): weekly[str(prediction.week_number)][levels[prediction.risk_level]]+=1
    attention=[]
    for prediction in sorted(values,key=lambda item:item.probability,reverse=True)[:5]:
        enrollment=prediction.enrollment
        attention.append({"code":enrollment.student.student_code,"name":enrollment.student.full_name,"course":enrollment.course.name,"score":enrollment.score,"attendance":enrollment.attendance_rate,"late":enrollment.late_submissions,"level":prediction.risk_level,"probability":prediction.probability,"url":url_for("main.student_detail",student_id=enrollment.student.id)})
    query=Alert.query.join(Enrollment).filter(Enrollment.id.in_(ids)) if ids else Alert.query.filter(db.false())
    alerts=[]
    for alert in query.options(joinedload(Alert.enrollment).joinedload(Enrollment.student),joinedload(Alert.enrollment).joinedload(Enrollment.course)).order_by(Alert.created_at.desc()).limit(5).all():
        alerts.append({"code":alert.enrollment.student.student_code,"course":alert.enrollment.course.name,"title":alert.title,"created_at":alert.created_at.strftime("%d/%m %H:%M"),"url":url_for("main.student_detail",student_id=alert.enrollment.student.id)})
    return jsonify(ok=True,data={"students":scope.with_entities(Enrollment.student_id).distinct().count(),"risk":{"high":sum(p.risk_level=="CAO" for p in values),"medium":sum(p.risk_level=="TRUNG_BINH" for p in values),"stable":sum(p.risk_level=="ON_DINH" for p in values)},"weekly":weekly,"attention":attention,"alerts":alerts})
