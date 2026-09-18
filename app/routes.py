import csv, io, json
from urllib.parse import urljoin, urlparse
from functools import wraps
from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.orm import joinedload, selectinload
from .extensions import db
from .models import Alert, Course, EmailLog, Enrollment, ModelVersion, Prediction, Recommendation, Semester, Student, User
from .services import REQUIRED_COLUMNS, import_rows, model_bundle, predict_enrollment, validate_csv, smtp_configured

bp=Blueprint("main",__name__)
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
    total=Student.query.count(); latest={}
    for p in Prediction.query.options(joinedload(Prediction.enrollment).joinedload(Enrollment.course)).order_by(Prediction.enrollment_id,Prediction.created_at.desc(),Prediction.id.desc()).all(): latest.setdefault(p.enrollment_id,p)
    counts={"CAO":0,"TRUNG_BINH":0,"ON_DINH":0}
    for p in latest.values(): counts[p.risk_level]+=1
    latest_predictions=list(latest.values())
    attention=sorted(latest_predictions,key=lambda p:p.probability,reverse=True)[:5]
    weekly={week:{"CAO":0,"TRUNG_BINH":0,"ON_DINH":0} for week in range(5,13)}
    for prediction in latest_predictions:
        week=prediction.week_number
        if week in weekly: weekly[week][prediction.risk_level]+=1
    attendance={}
    for enrollment in Enrollment.query.options(joinedload(Enrollment.course)).all():
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
    return render_template("dashboard.html",total=total,counts=counts,attention=attention,alerts=Alert.query.options(joinedload(Alert.enrollment).joinedload(Enrollment.student),joinedload(Alert.enrollment).joinedload(Enrollment.course)).order_by(Alert.created_at.desc()).limit(5).all(),is_demo=is_demo,semester=semester,weekly=weekly,attendance_by_course=attendance_by_course,activities=activities,active_model=active_model,model_ready=model_ready,email_status="ĐÃ CẤU HÌNH" if smtp_configured() else "DEV MODE")

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
    return render_template("students.html",page=page,term=term,class_name=class_name,course_id=course_id,semester_id=semester_id,sort=sort,classes=classes,courses=Course.query.order_by(Course.code).all(),semesters=Semester.query.order_by(Semester.code.desc()).all())

@bp.get("/academic-data")
@login_required
def academic_data():
    page=Enrollment.query.options(joinedload(Enrollment.student),joinedload(Enrollment.course),joinedload(Enrollment.semester)).order_by(Enrollment.id.desc()).paginate(page=request.args.get("page",1,type=int),per_page=20,error_out=False)
    return render_template("academic_data.html",page=page)

@bp.get("/analysis")
@login_required
def analysis():
    enrollments=Enrollment.query.options(joinedload(Enrollment.student),joinedload(Enrollment.course),selectinload(Enrollment.predictions)).order_by(Enrollment.id.desc()).limit(100).all()
    selected=request.args.get("enrollment_id",type=int)
    return render_template("analysis.html",enrollments=enrollments,selected=selected)

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
    done=skipped=0
    for e in Enrollment.query.filter(Enrollment.current_week>=5).all():
        try: predict_enrollment(e); done+=1
        except FileNotFoundError: flash("Chưa có model. Hãy chạy lệnh train-model.","error"); return redirect(url_for("main.dashboard"))
        except Exception: db.session.rollback(); skipped+=1
    flash(f"Đã dự báo hàng loạt {done} ghi danh; bỏ qua {skipped}.","success")
    return redirect(url_for("main.dashboard"))

@bp.route("/data/import",methods=["GET","POST"])
@admin_required
def import_data():
    preview=session.get("import_preview")
    if request.method=="POST":
        f=request.files.get("file")
        if not f or not f.filename.lower().endswith(".csv"): flash("Chỉ chấp nhận tệp CSV.","error")
        else:
            rows,errors=validate_csv(f.stream)
            if errors: return render_template("import.html",errors=errors,preview=[],demo_count=Student.query.filter_by(is_demo=True).count())
            session["import_preview"]=rows; preview=rows; flash(f"Đã kiểm tra {len(rows)} dòng hợp lệ. Hãy xác nhận nhập.","success")
    return render_template("import.html",preview=preview or [],errors=[],demo_count=Student.query.filter_by(is_demo=True).count())

@bp.post("/data/import/confirm")
@admin_required
def import_confirm():
    rows=session.pop("import_preview",None)
    if not rows: flash("Phiên xem trước đã hết hạn.","error")
    else:
        try: flash(f"Đã nhập {import_rows(rows)} bản ghi.","success")
        except ValueError as e: flash(str(e),"error")
    return redirect(url_for("main.import_data"))

@bp.get("/data/template.csv")
@login_required
def csv_template():
    out=io.StringIO(); w=csv.writer(out); w.writerow(REQUIRED_COLUMNS); w.writerow(["SV001","Nguyễn Văn A","CNTT01","sv@example.edu.vn","ML101","Học máy","2026A","Học kỳ 1 2026",5,7.5,90,0])
    return Response(out.getvalue().encode("utf-8-sig"),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=mau_import.csv"})

@bp.post("/data/seed-demo")
@admin_required
def seed_demo():
    if Student.query.filter_by(is_demo=True).first(): flash("Dữ liệu demo đã tồn tại.","error")
    else:
        rows=[]
        for i in range(1,31):
            rows.append(dict(student_code=f"DEMO{i:03d}",full_name=f"Sinh viên Demo {i:02d}",class_name="DEMO-CNTT",email=f"demo{i:03d}@example.test",course_code="ML101",course_name="Học máy ứng dụng",semester_code="DEMO-2026A",semester_name="Học kỳ demo",current_week=5+(i%5),score=round(2.5+(i*1.7)%7.3,1),attendance_rate=float(55+(i*7)%46),late_submissions=i%5))
        import_rows(rows,is_demo=True)
        predicted=0
        try:
            for enrollment in Enrollment.query.join(Student).filter(Student.is_demo.is_(True)).all():
                predict_enrollment(enrollment); predicted+=1
            flash(f"Đã tải dữ liệu demo và tạo {predicted} lượt dự báo.","success")
        except FileNotFoundError:
            flash("Đã tải dữ liệu demo. Cần huấn luyện mô hình trước khi chạy dự báo.","success")
    return redirect(url_for("main.dashboard"))

@bp.post("/data/reset-demo")
@admin_required
def reset_demo():
    demo_students=Student.query.filter_by(is_demo=True).all()
    if not demo_students:
        flash("Không có dữ liệu DEMO để xóa.","error")
        return redirect(url_for("main.import_data"))
    enrollment_ids=[enrollment.id for student in demo_students for enrollment in student.enrollments]
    if enrollment_ids:
        Alert.query.filter(Alert.enrollment_id.in_(enrollment_ids)).delete(synchronize_session=False)
        Recommendation.query.filter(Recommendation.enrollment_id.in_(enrollment_ids)).delete(synchronize_session=False)
    for student in demo_students: db.session.delete(student)
    db.session.commit()
    flash(f"Đã xóa an toàn {len(demo_students)} sinh viên DỮ LIỆU DEMO.","success")
    return redirect(url_for("main.import_data"))

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
    metadata=None
    try: metadata=model_bundle()["metadata"]
    except (FileNotFoundError,ValueError): pass
    return render_template("model.html",model=record,metadata=metadata)

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
    latest={}
    for p in Prediction.query.order_by(Prediction.enrollment_id,Prediction.created_at.desc(),Prediction.id.desc()).all(): latest.setdefault(p.enrollment_id,p)
    return jsonify(ok=True,data={"students":Student.query.count(),"risk":{"high":sum(p.risk_level=="CAO" for p in latest.values()),"medium":sum(p.risk_level=="TRUNG_BINH" for p in latest.values()),"stable":sum(p.risk_level=="ON_DINH" for p in latest.values())}})
