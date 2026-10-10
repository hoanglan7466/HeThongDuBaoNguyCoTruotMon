import csv, io, joblib, pytest
from sklearn.ensemble import RandomForestClassifier
from app.extensions import db
from app.cli import _upgrade_schema
from app.models import Alert, AuditLog, Course, EmailLog, Enrollment, ImportBatch, Major, Prediction, Recommendation, Semester, Student, User
from app.services import FEATURES, import_rows, predict_enrollment, render_risk_alert_email, risk_level, run_auto_pipeline, set_setting, validate_csv

def test_health(client):
    r=client.get("/health"); assert r.status_code==200 and r.json["database"]=="ok"

def test_csv_legacy_major_backfill_and_relation_filter(app, client, auth):
    rows,errors=validate_csv(_csv("MAJOR1,Nguyen An,CNTT01,,M1,Mon,HK1,Hoc ky,5,7,8,0"))
    assert not errors
    with app.app_context():
        import_rows(rows)
        known=Student.query.filter_by(student_code="MAJOR1").first()
        unknown_major=Major(code="DATA",name="Du lieu"); db.session.add(unknown_major)
        unknown=Student(student_code="MAJOR2",full_name="Tran Binh",class_name="Lop tu do",major=unknown_major)
        db.session.add(unknown); db.session.commit()
        assert known.major.code=="CNTT"
        assert Major.query.filter_by(code="CNTT").one().name
    page=auth.get("/students?major=DATA")
    assert page.status_code==200 and "MAJOR2" in page.text and "MAJOR1" not in page.text
    with app.app_context():
        assert Student.query.filter_by(student_code="MAJOR2").one().major.code=="DATA"

def test_major_backfill_migration_is_idempotent(app):
    with app.app_context():
        db.session.add_all([
            Student(student_code="BACKFILL1",full_name="Known",class_name="QTKD01"),
            Student(student_code="BACKFILL2",full_name="Unknown",class_name="Lop tu do"),
        ])
        db.session.commit()
        _upgrade_schema(); _upgrade_schema()
        assert Student.query.filter_by(student_code="BACKFILL1").one().major.code=="QTKD"
        assert Student.query.filter_by(student_code="BACKFILL2").one().major is None
        assert Major.query.filter(Major.code.in_(["CNTT","QTKD","NNH"])).count()==3

def test_attendance_uses_ten_point_scale():
    valid="SCALE,An,C1,,M1,Mon,HK1,Hoc ky,5,5,0,0"
    rows,errors=validate_csv(_csv(valid))
    assert len(rows)==1 and not errors and rows[0]["attendance_rate"]==0.0
    valid="SCALE2,An,C1,,M1,Mon,HK1,Hoc ky,5,5,10,0"
    rows,errors=validate_csv(_csv(valid))
    assert len(rows)==1 and not errors and rows[0]["attendance_rate"]==10.0
    invalid="SCALE3,An,C1,,M1,Mon,HK1,Hoc ky,5,5,10.1,0"
    rows,errors=validate_csv(_csv(invalid))
    assert not rows and errors

def test_auth_and_protection(client):
    assert client.get("/").status_code==302
    assert "Tổng quan" in client.post("/login",data={"username":"admin","password":"StrongPass123!"},follow_redirects=True).text
    assert client.post("/login",data={"username":"admin","password":"wrong"},follow_redirects=True).status_code==200

def test_permission(app,client):
    with app.app_context():
        u=User(username="advisor",full_name="Advisor",role="COVAN"); u.set_password("pass"); db.session.add(u); db.session.commit()
    client.post("/login",data={"username":"advisor","password":"pass"})
    assert client.get("/data/import").status_code==403

def test_csv_validation():
    header="student_code,full_name,class_name,email,course_code,course_name,semester_code,semester_name,current_week,score,attendance_rate,late_submissions\n"
    rows,errors=validate_csv(io.BytesIO((header+"S01,An,C1,a@b.vn,M1,Mon,HK1,Hoc ky,5,12,9,0\n").encode()))
    assert not rows and "Điểm" in errors[0]["message"]

def test_risk_thresholds(app):
    with app.app_context(): assert risk_level(.8)=="CAO" and risk_level(.5)=="TRUNG_BINH" and risk_level(.1)=="ON_DINH"

def test_prediction_and_alert(app,tmp_path):
    with app.app_context():
        import pandas as pd
        x=pd.DataFrame([[2,5,5],[3,6,4],[8,9,0],[9,10,0],[4,6,3],[7,9,1]],columns=FEATURES); y=[1,1,0,0,1,0]
        m=RandomForestClassifier(n_estimators=20,random_state=42).fit(x,y); joblib.dump({"model":m,"metadata":{"version":"test-v1"}},app.config["MODEL_PATH"])
        s=Student(student_code="S1",full_name="SV",class_name="C1"); c=Course(code="C",name="Môn"); sem=Semester(code="HK",name="Học kỳ"); db.session.add_all([s,c,sem]); db.session.flush(); e=Enrollment(student=s,course=c,semester=sem,current_week=5,score=2,attendance_rate=5,late_submissions=5); db.session.add(e); db.session.commit()
        p=predict_enrollment(e); assert p.probability>=.65 and p.week_number==5 and Alert.query.count()==1
        assert Prediction.query.count()==1 and db.session.execute(db.text("SELECT 1")).scalar()==1
        assert Recommendation.query.count()>=1 and EmailLog.query.filter_by(status="DEV_PREVIEW").count()==0
        first_recommendations=Recommendation.query.count(); predict_enrollment(e); predict_enrollment(e)
        assert Prediction.query.count()==1 and Alert.query.count()==1 and Recommendation.query.count()==first_recommendations

def test_prediction_requires_week_five(app, tmp_path):
    with app.app_context():
        s=Student(student_code="EARLY",full_name="Early",class_name="C1"); c=Course(code="EARLY",name="Môn"); sem=Semester(code="EARLY",name="Học kỳ")
        db.session.add_all([s,c,sem]); db.session.flush(); enrollment=Enrollment(student=s,course=c,semester=sem,current_week=4,score=5,attendance_rate=8,late_submissions=0); db.session.add(enrollment); db.session.commit()
        with pytest.raises(ValueError, match="Chưa đủ dữ liệu"): predict_enrollment(enrollment)
        assert Prediction.query.count()==0

def test_prediction_allows_week_six(app, tmp_path):
    with app.app_context():
        import pandas as pd
        model=RandomForestClassifier(n_estimators=10,random_state=42).fit(pd.DataFrame([[2,5,5],[9,9,0]],columns=FEATURES),[1,0])
        joblib.dump({"model":model,"metadata":{"version":"week-six","features":FEATURES}},app.config["MODEL_PATH"])
        s=Student(student_code="WEEK6",full_name="Week Six",class_name="C1"); c=Course(code="W6",name="Môn tuần 6"); sem=Semester(code="W6",name="Học kỳ")
        db.session.add_all([s,c,sem]); db.session.flush(); enrollment=Enrollment(student=s,course=c,semester=sem,current_week=6,score=2,attendance_rate=5,late_submissions=5); db.session.add(enrollment); db.session.commit()
        assert predict_enrollment(enrollment).week_number==6

def _configure_high_risk_model(app):
    import pandas as pd
    model=RandomForestClassifier(n_estimators=10,random_state=42).fit(pd.DataFrame([[2,5,5],[9,10,0]],columns=FEATURES),[1,0])
    joblib.dump({"model":model,"metadata":{"version":"auto-email-v1","features":FEATURES}},app.config["MODEL_PATH"])

def _add_high_risk_enrollment(email):
    suffix=Student.query.count()+1
    student=Student(student_code=f"AUTO{suffix}",full_name="Auto Test",class_name="C1",email=email)
    course=Course(code=f"AUTO{suffix}",name="Cơ sở dữ liệu")
    semester=Semester(code=f"AUTO{suffix}",name="Học kỳ")
    db.session.add_all([student,course,semester]); db.session.flush()
    enrollment=Enrollment(student=student,course=course,semester=semester,current_week=5,score=2,attendance_rate=5,late_submissions=5)
    db.session.add(enrollment); db.session.commit()
    return enrollment

def test_auto_pipeline_sends_once_uses_new_template_and_skips_rerun(app, monkeypatch):
    import smtplib
    from email import policy
    from email.parser import BytesParser
    captured=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def starttls(self): pass
        def login(self,*args): pass
        def send_message(self,message): captured.append(message.as_bytes())
    with app.app_context():
        _configure_high_risk_model(app); app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
        set_setting('auto_prediction_enabled','1'); set_setting('auto_email_enabled','1')
        enrollment=_add_high_risk_enrollment('auto@example.com')
        monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
        assert run_auto_pipeline([enrollment.id])['predictions']==1
        assert len(captured)==1 and EmailLog.query.filter_by(status='SENT').count()==1
        html=BytesParser(policy=policy.default).parsebytes(captured[0]).get_body(preferencelist=('html',)).get_content()
        for marker in ('Mức nguy cơ tổng quan','Thông tin môn học','Một số chỉ số học tập','Gợi ý cải thiện','Cơ sở dữ liệu'):
            assert marker in html
        assert run_auto_pipeline([enrollment.id])['predictions']==1 and len(captured)==1
        assert Enrollment.query.filter(~Enrollment.predictions.any()).count()==0

def test_manual_batch_prediction_does_not_send_email(app, auth, monkeypatch):
    import smtplib
    captured=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def starttls(self): pass
        def login(self,*args): pass
        def send_message(self,message): captured.append(message)
    with app.app_context():
        _configure_high_risk_model(app); app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
        set_setting('auto_email_enabled','1'); enrollment=_add_high_risk_enrollment('manual@example.com')
        semester_id,course_id=enrollment.semester_id,enrollment.course_id
    monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
    response=auth.post('/predict/batch',data={'semester_id':semester_id,'course_id':course_id,'week':5},follow_redirects=True)
    assert response.status_code==200 and not captured
    with app.app_context(): assert Prediction.query.count()==1 and Alert.query.count()==1

def test_auto_pipeline_respects_email_disabled_and_missing_recipient(app, monkeypatch):
    import smtplib
    captured=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def starttls(self): pass
        def login(self,*args): pass
        def send_message(self,message): captured.append(message)
    with app.app_context():
        _configure_high_risk_model(app); app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
        monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
        set_setting('auto_prediction_enabled','1'); set_setting('auto_email_enabled','0')
        disabled=_add_high_risk_enrollment('disabled@example.com'); run_auto_pipeline([disabled.id])
        assert not captured and EmailLog.query.filter_by(subject='AUTO_EMAIL_DISABLED',status='SKIPPED').count()==1
        set_setting('auto_email_enabled','1')
        missing=_add_high_risk_enrollment(None); run_auto_pipeline([missing.id])
        assert not captured and Alert.query.count()==2
        assert not captured and Prediction.query.count()==2 and Alert.query.count()==2

def test_auto_pipeline_smtp_failure_keeps_prediction_and_alert(app, monkeypatch):
    import smtplib
    with app.app_context():
        _configure_high_risk_model(app); app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
        set_setting('auto_prediction_enabled','1'); set_setting('auto_email_enabled','1')
        enrollment=_add_high_risk_enrollment('failed@example.com')
        monkeypatch.setattr(smtplib,'SMTP',lambda *args,**kwargs: (_ for _ in ()).throw(smtplib.SMTPException('failed')))
        run_auto_pipeline([enrollment.id])
        assert Prediction.query.count()==1 and Alert.query.count()==1 and EmailLog.query.filter_by(status='FAILED').count()==1

def _csv(body):
    header=",".join(["student_code","full_name","class_name","email","course_code","course_name","semester_code","semester_name","current_week","score","attendance_rate","late_submissions"])
    return io.BytesIO((header+"\n"+body+"\n").encode("utf-8"))

def test_logout_and_safe_next(client):
    response=client.post("/login?next=https://evil.example",data={"username":"admin","password":"StrongPass123!"})
    assert response.headers["Location"].endswith("/")
    assert client.post("/logout").status_code==302
    assert client.get("/").status_code==302
    response=client.post("/login",data={"username":"admin","password":"StrongPass123!"})
    assert response.headers["Location"].endswith("/") and not response.headers["Location"].endswith("/None")

def test_error_pages_do_not_leak(client,auth):
    response=client.get("/missing")
    assert response.status_code==404 and b"Traceback" not in response.data
    response=client.post("/alerts/999/status",data={"status":"INVALID"})
    assert response.status_code==404 and b"Traceback" not in response.data

def test_csv_missing_columns_and_bad_types():
    rows,errors=validate_csv(io.BytesIO(b"student_code,score\nS1,5\n"))
    assert not rows and "Thiếu cột" in errors[0]["message"]
    rows,errors=validate_csv(_csv("S1,An,C1,,M1,Mon,HK1,Hoc ky,x,abc,101,-1"))
    assert not rows and "Dữ liệu số" in errors[0]["message"]

def test_csv_attendance_and_duplicate_validation():
    row="S1,An,C1,,M1,Mon,HK1,Hoc ky,5,5,101,0"
    rows,errors=validate_csv(_csv(row))
    assert not rows and "Chuyên cần" in errors[0]["message"]
    valid="S1,An,C1,,M1,Mon,HK1,Hoc ky,5,5,8,0"
    rows,errors=validate_csv(io.BytesIO((",".join(["student_code","full_name","class_name","email","course_code","course_name","semester_code","semester_name","current_week","score","attendance_rate","late_submissions"])+"\n"+valid+"\n"+valid+"\n").encode()))
    assert len(rows)==1 and any("trùng" in error["message"] for error in errors)

def test_import_is_atomic_on_duplicate(app):
    rows,errors=validate_csv(_csv("S1,An,C1,,M1,Mon,HK1,Hoc ky,5,5,8,0")); assert not errors
    with app.app_context():
        assert import_rows(rows)==1
        with pytest.raises(ValueError): import_rows(rows)
        assert Student.query.count()==1 and Enrollment.query.count()==1

def test_template_download_utf8(auth):
    response=auth.get("/data/template.csv")
    assert response.status_code==200 and response.data.startswith(b"\xef\xbb\xbf")
    parsed=list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
    assert parsed[0][0]=="student_code" and len(parsed)==2

def test_pages_and_dashboard_api(auth):
    for path in ["/","/students","/academic-data","/analysis","/alerts","/reports","/model","/data/import","/admin/users","/settings"]:
        assert auth.get(path).status_code==200
    payload=auth.get("/api/dashboard").get_json()
    assert payload["ok"] and payload["data"]["students"]==0

def test_page_size_validation_and_filter_preservation(app,auth):
    with app.app_context():
        db.session.add_all([Student(student_code=f"PAGE{i:03}",full_name=f"Trang {i}",class_name="C1") for i in range(25)])
        db.session.commit()
    valid=auth.get("/students?q=PAGE&per_page=10")
    assert valid.status_code==200 and 'value="10" selected' in valid.text and 'q=PAGE' in valid.text and 'per_page=10' in valid.text
    second=auth.get("/students?q=PAGE&per_page=10&page=2")
    assert second.status_code==200 and "PAGE010" in second.text and "PAGE000" not in second.text
    for value in ("999999","-1","abc"):
        response=auth.get(f"/students?per_page={value}")
        assert response.status_code==200 and 'value="20" selected' in response.text
    assert auth.get("/reports?per_page=200").status_code==200

def test_pagination_page_size_sequence_across_all_list_pages(auth):
    paths=("/students","/academic-data","/analysis","/alerts","/reports","/data/import")
    for path in paths:
        for size in (20,50,100,200,10,20):
            response=auth.get(f"{path}?per_page={size}&page=1")
            assert response.status_code==200
            if 'class="page-size-control"' in response.text:
                assert f'value="{size}" selected' in response.text
            assert "Đã xóa 0 sinh viên" not in response.text

def test_filter_forms_preserve_page_size_and_reset_page(app,auth):
    with app.app_context():
        db.session.add(Student(student_code="PAGEFILTER",full_name="Filter Page",class_name="C1"))
        db.session.commit()
    cases=(
        ("/students?q=PAGE&major=CNTT&per_page=100&page=1", "student-filters"),
        ("/reports?q=PAGE&risk=CAO&per_page=100&page=1", "report-filters"),
    )
    for url, form_class in cases:
        response=auth.get(url)
        assert response.status_code==200
        assert form_class in response.text, url
        form_start=response.text.index(form_class)
        form_end=response.text.index("</form>", form_start)
        form=response.text[form_start:form_end]
        assert 'name="per_page" value="100"' in form
        assert 'name="page" value="1"' in form
        assert 'value="100" selected' in response.text

def test_student_page_size_never_submits_bulk_delete(app,auth):
    with app.app_context():
        db.session.add_all([Student(student_code=f"SAFE{i:03}",full_name=f"Safe {i}",class_name="C1") for i in range(25)])
        db.session.commit(); before=Student.query.count()
    for size in (10,20,50,100,200):
        response=auth.get(f"/students?q=SAFE&per_page={size}")
        assert response.status_code==200 and "Đã xóa 0 sinh viên" not in response.text
        page_form=response.text.index('class="page-size-control"')
        bulk_form=response.text.index('id="bulk-students"')
        assert page_form < bulk_form
        with app.app_context(): assert Student.query.count()==before

def test_report_filter_and_export(app,auth,tmp_path):
    with app.app_context():
        import pandas as pd
        model=RandomForestClassifier(n_estimators=20,random_state=42).fit(pd.DataFrame([[1,5,5],[9,10,0]],columns=FEATURES),[1,0])
        joblib.dump({"model":model,"metadata":{"version":"filter-v1","features":FEATURES}},app.config["MODEL_PATH"])
        s=Student(student_code="FILTER1",full_name="Nguyễn An",class_name="C1"); c=Course(code="M1",name="Môn 1"); sem=Semester(code="HK1",name="Học kỳ 1")
        db.session.add_all([s,c,sem]); db.session.flush(); course_id, semester_id=c.id, sem.id; e=Enrollment(student=s,course=c,semester=sem,current_week=5,score=1,attendance_rate=5,late_submissions=5); db.session.add(e); db.session.commit(); predict_enrollment(e)
    response=auth.get("/reports?risk=CAO&q=FILTER")
    assert response.status_code==200 and "FILTER1" in response.text
    exported=auth.get("/reports/export.csv?risk=CAO&q=FILTER")
    decoded=exported.data.decode("utf-8-sig")
    assert exported.status_code==200 and exported.data.startswith(b"\xef\xbb\xbf") and "FILTER1" in decoded and "Tuần dự báo" in decoded
    assert auth.get(f"/reports?course_id={course_id}&semester_id={semester_id}").status_code==200

def test_database_constraints(app):
    with app.app_context():
        s=Student(student_code="BAD",full_name="Bad",class_name="C"); c=Course(code="BAD",name="Bad"); sem=Semester(code="BAD",name="Bad"); db.session.add_all([s,c,sem]); db.session.flush()
        db.session.add(Enrollment(student=s,course=c,semester=sem,current_week=5,score=11,attendance_rate=8,late_submissions=0))
        with pytest.raises(Exception): db.session.commit()
        db.session.rollback()

def test_advisor_can_use_workflow_but_not_admin(app,client):
    with app.app_context():
        advisor=User(username="covan",full_name="Cố vấn",role="COVAN"); advisor.set_password("pass123"); db.session.add(advisor); db.session.commit()
    client.post("/login",data={"username":"covan","password":"pass123"})
    for path in ["/","/students","/academic-data","/analysis","/alerts","/reports","/model"]:
        assert client.get(path).status_code==200
    for path in ["/data/import","/admin/users","/settings"]:
        assert client.get(path).status_code==403

def test_demo_seed_creates_complete_demo_flow(app,auth):
    with app.app_context():
        import pandas as pd
        x=pd.DataFrame([[1,5,6],[2,6,5],[5,7,2],[6,8,1],[9,10,0],[8,9,0]],columns=FEATURES); y=[1,1,1,0,0,0]
        model=RandomForestClassifier(n_estimators=30,random_state=42).fit(x,y)
        joblib.dump({"model":model,"metadata":{"version":"demo-flow-v1","features":FEATURES}},app.config["MODEL_PATH"])
    response=auth.post("/data/seed-demo",follow_redirects=True)
    assert response.status_code==200 and "DỮ LIỆU DEMO" in response.text and "Đã dự báo 26" in response.text
    with app.app_context():
        assert Student.query.filter_by(is_demo=True).count()==30
        assert Prediction.query.count()==26 and Recommendation.query.count()>0
        assert Prediction.query.join(Enrollment).filter(Enrollment.current_week<5).count()==0
        assert Alert.query.count()==Prediction.query.filter_by(risk_level="CAO").count()
        assert EmailLog.query.count()==0
        weeks={value[0] for value in db.session.query(Enrollment.current_week).all()}
        assert {1,2,3,4,5,6} <= weeks
    assert auth.get("/analysis").status_code==200
    assert auth.get("/reports?risk=CAO").status_code==200


def test_exact_configured_thresholds(app):
    app.config.update(RISK_MEDIUM_THRESHOLD=.2, RISK_HIGH_THRESHOLD=.7)
    with app.app_context():
        assert risk_level(.19999)=="ON_DINH"
        assert risk_level(.2)=="TRUNG_BINH"
        assert risk_level(.69999)=="TRUNG_BINH"
        assert risk_level(.7)=="CAO"


def test_csrf_login_and_logout(app, client):
    import re
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/login',data={'username':'admin','password':'StrongPass123!'}).status_code==400
    token=re.search(r'name="csrf_token" value="([^"]+)"',client.get('/login').text).group(1)
    assert client.post('/login',data={'username':'admin','password':'StrongPass123!','csrf_token':token}).status_code==302
    assert client.post('/logout').status_code==400
    assert client.post('/logout',data={'csrf_token':token}).status_code==302


def test_smtp_partial_configuration_and_failure(app, monkeypatch):
    import smtplib
    from app.services import send_email
    with app.app_context():
        app.config.update(MAIL_MODE='dev',SMTP_HOST='smtp.example.invalid',SMTP_USERNAME='test',SMTP_PASSWORD=None,SMTP_FROM=None)
        assert send_email('student@example.com','Test','Preview')=='DEV_PREVIEW'
        assert send_email('demo@example.invalid','Test','Preview')=='SKIPPED'
        app.config.update(MAIL_MODE='smtp',SMTP_PASSWORD='test',SMTP_FROM='test@example.com')
        def fail(*args, **kwargs): raise smtplib.SMTPException('unavailable')
        monkeypatch.setattr(smtplib,'SMTP',fail)
        assert send_email('student@example.com','Test','Preview')=='FAILED'
        assert EmailLog.query.filter_by(status='SENT').count()==0

def test_risk_alert_renderer_uses_real_values_and_escapes_html(app):
    from types import SimpleNamespace
    with app.app_context():
        student=SimpleNamespace(full_name='<Sinh viên>')
        enrollment=SimpleNamespace(course=SimpleNamespace(name='Cơ sở dữ liệu'),current_week=5,score=5.8,attendance_rate=7.2,late_submissions=2)
        prediction=SimpleNamespace(risk_level='TRUNG_BINH',probability=.68)
        recommendations=[SimpleNamespace(content='Ôn tập <an toàn>.')]
        plain,html=render_risk_alert_email(student,enrollment,prediction,recommendations)
        assert '&lt;Sinh viên&gt;' in html and '&lt;an toàn&gt;' in html
        for value in ('Cơ sở dữ liệu','Tuần 5','Cần theo dõi','68.0%','5.8','7.2/10','2','Gợi ý cải thiện','Đây là cảnh báo hỗ trợ học tập'):
            assert value in html
        assert 'Điểm chuyên cần: 7.2/10' in plain

def test_risk_alert_email_unicode_round_trip_through_mime(app, monkeypatch):
    import smtplib
    from email import policy
    from email.parser import BytesParser
    from types import SimpleNamespace
    from app.services import send_email
    subject='[TEST] [Cảnh báo học tập] Thông báo nguy cơ học tập'
    student=SimpleNamespace(full_name='Sinh viên DEMO')
    enrollment=SimpleNamespace(course=SimpleNamespace(name='Cơ sở dữ liệu'),current_week=5,score=5.8,attendance_rate=7.2,late_submissions=2)
    prediction=SimpleNamespace(risk_level='TRUNG_BINH',probability=.68)
    recommendation_texts=('Ôn tập lại các nội dung chưa đạt yêu cầu.','Cải thiện mức độ tham gia lớp học.','Hoàn thành bài tập đúng hạn.','Trao đổi với cố vấn học tập nếu cần hỗ trợ.')
    recommendations=[SimpleNamespace(content=value) for value in recommendation_texts]
    captured=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def starttls(self): pass
        def login(self,*args): pass
        def send_message(self,message): captured.append(message.as_bytes())
    with app.app_context():
        for value in (student.full_name,enrollment.course.name,*recommendation_texts,subject):
            assert '?' not in value
        plain,html=render_risk_alert_email(student,enrollment,prediction,recommendations)
        for value in (student.full_name,enrollment.course.name,*recommendation_texts):
            assert value in plain and value in html
        app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
        monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
        assert send_email('student@example.com',subject,plain,html_body=html)=='SENT'
    parsed=BytesParser(policy=policy.default).parsebytes(captured[0])
    text_part=parsed.get_body(preferencelist=('plain',))
    html_part=parsed.get_body(preferencelist=('html',))
    assert parsed['Subject']==subject
    assert text_part.get_content_charset()=='utf-8' and html_part.get_content_charset()=='utf-8'
    for value in (student.full_name,enrollment.course.name,*recommendation_texts):
        assert value in text_part.get_content() and value in html_part.get_content()

def test_admin_email_preview_uses_production_warning_template(auth):
    response=auth.get('/settings/email-preview')
    assert response.status_code==200
    assert 'Sinh viên DEMO' in response.text and 'Cơ sở dữ liệu' in response.text and '7.2/10' in response.text


def test_smtp_success_and_admin_test_email(app, auth, monkeypatch):
    import smtplib
    from app.services import send_email
    class FakeSMTP:
        def __init__(self,*args,**kwargs): self.sent=[]
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def starttls(self): pass
        def login(self,username,password): assert password=='secret'
        def send_message(self,msg): self.sent.append(msg)
    with app.app_context():
        app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
        monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
        assert send_email('student@example.com','Test','Plain',html_body='<p>HTML</p>')=='SENT'
        assert EmailLog.query.filter_by(status='SENT').count()==1
    response=auth.post('/settings/test-email',data={'recipient':'student@example.com'},follow_redirects=True)
    assert response.status_code==200 and b'secret' not in response.data.lower()

def test_admin_demo_risk_email_uses_production_warning_template(app, auth, monkeypatch):
    import smtplib
    from email import policy
    from email.parser import BytesParser
    captured=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def starttls(self): pass
        def login(self,*args): pass
        def send_message(self,message): captured.append(message.as_bytes())
    with app.app_context():
        app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
    monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
    response=auth.post('/settings/email/demo',data={'recipient':'student@example.com'})
    assert response.status_code==200 and response.json['status']=='SENT'
    parsed=BytesParser(policy=policy.default).parsebytes(captured[0])
    html=parsed.get_body(preferencelist=('html',)).get_content()
    plain=parsed.get_body(preferencelist=('plain',)).get_content()
    assert parsed['Subject']=='[TEST] [Cảnh báo học tập] Thông báo nguy cơ học tập'
    for marker in ('Sinh viên DEMO','Cơ sở dữ liệu','7.2/10','Mức nguy cơ tổng quan','Thông tin môn học','Một số chỉ số học tập','Gợi ý cải thiện','Kết quả dự báo mang tính hỗ trợ'):
        assert marker in html
    assert 'Sinh viên DEMO' in plain and 'Cơ sở dữ liệu' in plain and '7.2/10' in plain
    assert 'THÔNG BÁO DEMO' not in html


def test_covan_cannot_send_test_email(app, client):
    from app.models import User
    with app.app_context():
        advisor=User(username='mail-covan',full_name='Cố vấn',role='COVAN'); advisor.set_password('CovanPass123!'); db.session.add(advisor); db.session.commit()
    client.post('/login',data={'username':'mail-covan','password':'CovanPass123!'})
    assert client.post('/settings/test-email',data={'recipient':'student@example.com'}).status_code==403


def test_admin_delete_student_with_dependents_and_covan_forbidden(app, auth, client):
    from app.models import Alert, Course, Enrollment, Prediction, Recommendation, Semester, Student
    with app.app_context():
        s=Student(student_code='DEL1',full_name='Delete Me',class_name='C1'); c=Course(code='DEL',name='Delete Course'); sem=Semester(code='DELSEM',name='Delete Semester'); db.session.add_all([s,c,sem]); db.session.flush(); e=Enrollment(student=s,course=c,semester=sem,current_week=5,score=2,attendance_rate=5,late_submissions=3); db.session.add(e); db.session.flush(); p=Prediction(enrollment=e,probability=.9,risk_level='CAO',model_version='test',week_number=5,factors_json='[]'); db.session.add(p); db.session.flush(); db.session.add_all([Alert(enrollment=e,prediction_id=p.id,title='test'),Recommendation(enrollment_id=e.id,category='DIEM',content='test')]); db.session.commit(); sid=s.id
    assert auth.post(f'/students/{sid}/delete').status_code==302
    with app.app_context():
        assert db.session.get(Student,sid) is None and Enrollment.query.count()==0 and Prediction.query.count()==0 and Alert.query.count()==0 and Recommendation.query.count()==0
        s=Student(student_code='DEL2',full_name='Delete Two',class_name='C1'); db.session.add(s); db.session.commit(); sid=s.id
    client.get('/logout')
    client.post('/login',data={'username':'admin','password':'StrongPass123!'})
    assert client.post(f'/students/{sid}/delete').status_code==302


def test_automation_settings_admin_only_and_smtp_guard(app, auth, client):
    assert auth.post('/settings/automation',data={'key':'auto_prediction_enabled','value':'1'}).json['ok'] is True
    app.config['MAIL_MODE']='dev'
    assert auth.post('/settings/automation',data={'key':'auto_email_enabled','value':'1'}).status_code==400
    client.get('/logout'); client.post('/login',data={'username':'admin','password':'StrongPass123!'})
    assert client.post('/settings/automation',data={'key':'auto_prediction_enabled','value':'0'}).json['ok'] is True


def test_health_checks_artifact_contents(app, client):
    from pathlib import Path
    Path(app.config['MODEL_PATH']).write_bytes(b'invalid model')
    assert client.get('/health').json['model']=='invalid'


def test_advisor_cannot_mutate_admin_data(app, client):
    with app.app_context():
        user=User(username='advisor2',full_name='Advisor',role='COVAN'); user.set_password('test'); db.session.add(user); db.session.commit()
    client.post('/login',data={'username':'advisor2','password':'test'})
    for path in ['/data/import','/data/import/confirm','/data/seed-demo','/data/reset-demo','/admin/users']:
        assert client.post(path).status_code==403

def test_admin_user_management(app, auth):
    created=auth.post('/admin/users',data={'username':'newadvisor','full_name':'New Advisor','email':'new@example.test','role':'COVAN','password':'SecurePass9'},follow_redirects=True)
    assert created.status_code==200 and 'Đã tạo tài khoản' in created.text
    with app.app_context():
        user=User.query.filter_by(username='newadvisor').one()
        assert user.check_password('SecurePass9') and user.password_hash != 'SecurePass9'
        user_id=user.id
    updated=auth.post(f'/admin/users/{user_id}/update',data={'full_name':'Updated Advisor','email':'new@example.test','role':'COVAN','password':'ChangedPass9'},follow_redirects=True)
    assert 'Đã cập nhật tài khoản' in updated.text
    with app.app_context(): assert db.session.get(User,user_id).check_password('ChangedPass9')
    assert auth.post(f'/admin/users/{user_id}/toggle',follow_redirects=True).status_code==200

def test_reset_demo_preserves_non_demo_data(app, auth):
    with app.app_context():
        rows, errors=validate_csv(_csv('REAL1,Real,C1,,M1,Môn 1,HK1,Học kỳ 1,5,7,9,0')); assert not errors
        import_rows(rows)
        demo=Student(student_code='DEMOX',full_name='Demo',class_name='D',is_demo=True); db.session.add(demo); db.session.commit()
    response=auth.post('/data/reset-demo',follow_redirects=True)
    assert response.status_code==200 and 'Đã xóa an toàn 1' in response.text
    with app.app_context():
        assert Student.query.filter_by(student_code='REAL1').count()==1 and Student.query.filter_by(is_demo=True).count()==0


def test_init_db_syncs_demo_accounts(app):
    runner=app.test_cli_runner()
    assert runner.invoke(args=['init-db','--admin-password','Admin@123','--advisor-password','Covan@123']).exit_code==0
    with app.app_context():
        admin=User.query.filter_by(username='admin').first()
        advisor=User.query.filter_by(username='covan').first()
        assert admin and admin.role=='ADMIN' and admin.check_password('Admin@123')
        assert advisor and advisor.role=='COVAN' and advisor.check_password('Covan@123')

def test_weekly_snapshots_and_exact_duplicate_are_atomic(app, auth):
    first="WEEKLY1,Nguyễn Minh Anh,CNTT01,weekly1@example.test,DB101,Cơ sở dữ liệu,2026A,Học kỳ 1,1,8,10,0"
    second="WEEKLY1,Nguyễn Minh Anh,CNTT01,weekly1@example.test,DB101,Cơ sở dữ liệu,2026A,Học kỳ 1,2,7.8,9,1"
    rows,errors=validate_csv(_csv(first+"\n"+second)); assert not errors
    with app.app_context():
        count,batch=import_rows(rows,filename="weekly.csv",imported_by=1)
        assert count==2 and batch.data_type=="USER" and batch.status=="COMPLETED"
        assert {e.current_week for e in Enrollment.query.all()}=={1,2}
        with pytest.raises(ValueError): import_rows([rows[0]],filename="duplicate.csv",imported_by=1)
        assert Enrollment.query.count()==2 and ImportBatch.query.count()==1

def test_preview_then_confirm_creates_user_batch(app, auth):
    upload={"file":(_csv("USR01,Lê Thu Hà,CNTT02,usr01@example.test,AI101,Trí tuệ nhân tạo,2026A,Học kỳ 1,5,7.5,9,1"),"nguoi_dung.csv")}
    preview=auth.post("/data/import",data=upload,content_type="multipart/form-data")
    assert preview.status_code==200 and "Xem trước dữ liệu" in preview.text and "USR01" in preview.text
    with app.app_context(): assert Enrollment.query.count()==0
    result=auth.post("/data/import/confirm",follow_redirects=True)
    assert result.status_code==200 and "Người dùng" in result.text
    with app.app_context():
        batch=ImportBatch.query.one()
        assert batch.filename=="nguoi_dung.csv" and batch.data_type=="USER" and batch.record_count==1
        assert AuditLog.query.filter_by(action="IMPORT_DATA").count()==1

def test_invalid_import_does_not_mutate_database(app, auth):
    bad={"file":(_csv("BAD01,Tên lỗi,C1,bad@example.test,M1,Môn 1,HK1,Học kỳ 1,11,12,101,-1"),"bad.csv")}
    response=auth.post("/data/import",data=bad,content_type="multipart/form-data")
    assert response.status_code==200 and "Không thể xem trước tệp" in response.text
    with app.app_context(): assert Student.query.count()==0 and Enrollment.query.count()==0 and ImportBatch.query.count()==0

def test_delete_one_batch_preserves_other_batch(app, auth):
    with app.app_context():
        one,_=validate_csv(_csv("KEEP1,Phạm Quốc Bảo,C1,keep@example.test,M1,Môn 1,HK1,Học kỳ 1,1,8,9,0"))
        two,_=validate_csv(_csv("KEEP1,Phạm Quốc Bảo,C1,keep@example.test,M1,Môn 1,HK1,Học kỳ 1,2,7,8,1"))
        _,first=import_rows(one,filename="one.csv",imported_by=1)
        import_rows(two,filename="two.csv",imported_by=1); first_id=first.id
    response=auth.post(f"/data/batches/{first_id}/delete",follow_redirects=True)
    assert response.status_code==200
    with app.app_context():
        assert Student.query.filter_by(student_code="KEEP1").count()==1
        assert Enrollment.query.count()==1 and Enrollment.query.one().current_week==2
        assert ImportBatch.query.count()==1 and AuditLog.query.filter_by(action="DELETE_IMPORT_BATCH").count()==1

def test_reset_demo_removes_only_orphan_reference_data(app, auth):
    with app.app_context():
        demo,_=validate_csv(_csv("DEMOZ,Đỗ Thành Nam,C1,demoz@example.test,DEMO101,Môn demo,HKDEMO,Học kỳ demo,5,6,8,1"))
        user,_=validate_csv(_csv("USERZ,Vũ Khánh Linh,C2,userz@example.test,USER101,Môn người dùng,HKUSER,Học kỳ người dùng,5,8,10,0"))
        import_rows(demo,is_demo=True,filename="demo.csv",imported_by=1)
        import_rows(user,filename="user.csv",imported_by=1)
    assert auth.post('/data/reset-demo',follow_redirects=True).status_code==200
    with app.app_context():
        assert Student.query.filter_by(student_code="DEMOZ").count()==0
        assert Course.query.filter_by(code="DEMO101").count()==0 and Semester.query.filter_by(code="HKDEMO").count()==0
        assert Student.query.filter_by(student_code="USERZ").count()==1
        assert Course.query.filter_by(code="USER101").count()==1 and Semester.query.filter_by(code="HKUSER").count()==1

def test_loading_demo_twice_does_not_duplicate(app, auth):
    first=auth.post('/data/seed-demo',follow_redirects=True)
    assert first.status_code==200
    with app.app_context(): before=(Student.query.count(),Enrollment.query.count(),ImportBatch.query.count())
    second=auth.post('/data/seed-demo',follow_redirects=True)
    assert second.status_code==200 and "đã tồn tại" in second.text
    with app.app_context(): assert (Student.query.count(),Enrollment.query.count(),ImportBatch.query.count())==before

def test_analysis_week_four_blocks_backend(app, auth):
    with app.app_context():
        s=Student(student_code="BLOCK4",full_name="Tuần Bốn",class_name="C1"); c=Course(code="B4",name="Môn tuần 4"); sem=Semester(code="B4",name="Học kỳ B4")
        db.session.add_all([s,c,sem]); db.session.flush(); db.session.add(Enrollment(student=s,course=c,semester=sem,current_week=4,score=6,attendance_rate=8,late_submissions=1)); db.session.commit(); course_id=c.id; semester_id=sem.id
    page=auth.get(f"/analysis?semester_id={semester_id}&course_id={course_id}&week=4")
    assert page.status_code==200 and "Hệ thống bắt đầu dự báo nguy cơ từ tuần 5" in page.text
    result=auth.post("/predict/batch",data={"semester_id":semester_id,"course_id":course_id,"week":4},follow_redirects=True)
    assert "Hệ thống bắt đầu dự báo nguy cơ từ tuần 5" in result.text
    with app.app_context(): assert Prediction.query.count()==0

def test_batch_prediction_isolates_course_and_future_week(app, auth):
    with app.app_context():
        import pandas as pd
        model=RandomForestClassifier(n_estimators=20,random_state=42).fit(pd.DataFrame([[2,5,5],[9,9,0]],columns=FEATURES),[1,0])
        joblib.dump({"model":model,"metadata":{"version":"scope-v1","features":FEATURES}},app.config["MODEL_PATH"])
        student=Student(student_code="SCOPE1",full_name="Nguyễn Hải An",class_name="C1"); c1=Course(code="SC1",name="Môn A"); c2=Course(code="SC2",name="Môn B"); sem=Semester(code="SCOPE",name="Học kỳ Scope")
        db.session.add_all([student,c1,c2,sem]); db.session.flush()
        week7=Enrollment(student=student,course=c1,semester=sem,current_week=7,score=2,attendance_rate=5,late_submissions=5)
        future=Enrollment(student=student,course=c1,semester=sem,current_week=8,score=9,attendance_rate=9,late_submissions=0)
        other_course=Enrollment(student=student,course=c2,semester=sem,current_week=7,score=9,attendance_rate=9,late_submissions=0)
        db.session.add_all([week7,future,other_course]); db.session.commit(); course_id=c1.id; semester_id=sem.id; week7_id=week7.id
    for _ in range(3):
        response=auth.post("/predict/batch",data={"semester_id":semester_id,"course_id":course_id,"week":7},follow_redirects=True)
        assert response.status_code==200 and "Đã hoàn thành dự báo cho 1 enrollment" in response.text
    with app.app_context():
        prediction=Prediction.query.one()
        assert prediction.enrollment_id==week7_id and prediction.week_number==7 and prediction.probability>=0
        assert Alert.query.count()<=1

def test_role_avatars_and_student_empty_states(app, client, auth):
    admin_page=auth.get('/students')
    assert admin_page.status_code==200
    assert admin_page.text.count('/static/css/images/admin.png')==3
    assert '/static/css/images/covan.png' not in admin_page.text
    assert 'Chưa có dữ liệu sinh viên' in admin_page.text
    with app.app_context():
        advisor=User(username='avatar-covan',full_name='Cố vấn',role='COVAN'); advisor.set_password('testpass'); db.session.add(advisor)
        student=Student(student_code='VISIBLE1',full_name='Sinh Viên Hiển Thị',class_name='C1'); db.session.add(student); db.session.commit()
    client.post('/logout')
    client.post('/login',data={'username':'avatar-covan','password':'testpass'})
    advisor_page=client.get('/students')
    assert advisor_page.text.count('/static/css/images/covan.png')==3
    assert '/static/css/images/admin.png' not in advisor_page.text
    assert 'Thêm sinh viên' not in advisor_page.text and 'row-menu-trigger' not in advisor_page.text
    no_match=client.get('/students?q=KHONGTONTAI')
    assert 'Không tìm thấy sinh viên phù hợp.' in no_match.text and 'Đặt lại bộ lọc' in no_match.text

def test_analysis_normalizes_stale_dependent_filters_and_uses_real_weeks(app, auth):
    with app.app_context():
        cntt=Major(code='CNTT',name='Công nghệ thông tin')
        qt=Major(code='QTKD',name='Quản trị kinh doanh')
        sem=Semester(code='HK-AN',name='Học kỳ phân tích',is_current=True)
        cs=Course(code='CS101',name='Cơ sở dữ liệu')
        ml=Course(code='ML201',name='Học máy')
        a=Student(student_code='AN-CNTT',full_name='An CNTT',class_name='CNTT 20-01',major=cntt)
        b=Student(student_code='AN-QT',full_name='An QTKD',class_name='QTKD 20-01',major=qt)
        db.session.add_all([cntt,qt,sem,cs,ml,a,b]); db.session.flush()
        db.session.add_all([
            Enrollment(student=a,course=cs,semester=sem,current_week=5,score=6,attendance_rate=8,late_submissions=0),
            Enrollment(student=b,course=ml,semester=sem,current_week=9,score=7,attendance_rate=9,late_submissions=0),
        ]); db.session.commit(); sem_id=sem.id; cs_id=cs.id; ml_id=ml.id
        before=(Prediction.query.count(),Alert.query.count(),EmailLog.query.count())
    page=auth.get(f'/analysis?semester_id={sem_id}&cohort=K20&major=CNTT&class_name=CNTT+20-01&course_id={ml_id}&week=9')
    assert page.status_code==200
    assert '<option value="" selected>Tất cả môn học</option>' in page.text
    assert 'Tuần 5</option>' in page.text and 'Tuần 9</option>' not in page.text
    with app.app_context():
        assert (Prediction.query.count(),Alert.query.count(),EmailLog.query.count())==before

def test_analysis_batch_honors_student_search_and_selected_snapshot(app, auth):
    with app.app_context():
        import pandas as pd
        model=RandomForestClassifier(n_estimators=10,random_state=42).fit(pd.DataFrame([[2,5,5],[9,9,0]],columns=FEATURES),[1,0])
        joblib.dump({'model':model,'metadata':{'version':'analysis-filter-v1','features':FEATURES}},app.config['MODEL_PATH'])
        sem=Semester(code='HK-Q',name='Học kỳ query'); course=Course(code='Q1',name='Môn query')
        one=Student(student_code='FILTER-ONE',full_name='Một',class_name='CNTT 20-01')
        two=Student(student_code='FILTER-TWO',full_name='Hai',class_name='CNTT 20-01')
        db.session.add_all([sem,course,one,two]); db.session.flush()
        db.session.add_all([
            Enrollment(student=one,course=course,semester=sem,current_week=6,score=2,attendance_rate=5,late_submissions=4),
            Enrollment(student=two,course=course,semester=sem,current_week=6,score=9,attendance_rate=9,late_submissions=0),
        ]); db.session.commit(); sem_id=sem.id; course_id=course.id
    response=auth.post('/predict/batch',data={'semester_id':sem_id,'course_id':course_id,'week':6,'q':'FILTER-ONE'},follow_redirects=True)
    assert response.status_code==200 and 'Đã hoàn thành dự báo cho 1 enrollment' in response.text
    with app.app_context():
        prediction=Prediction.query.one()
        assert prediction.enrollment.student.student_code=='FILTER-ONE' and prediction.week_number==6

def test_analysis_all_courses_is_default_and_batch_does_not_send_email(app, auth, monkeypatch):
    import smtplib
    captured=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def starttls(self): pass
        def login(self,*args): pass
        def send_message(self,message): captured.append(message)
    with app.app_context():
        import pandas as pd
        model=RandomForestClassifier(n_estimators=10,random_state=42).fit(pd.DataFrame([[2,5,5],[9,9,0]],columns=FEATURES),[1,0])
        joblib.dump({'model':model,'metadata':{'version':'all-courses-v1','features':FEATURES}},app.config['MODEL_PATH'])
        sem=Semester(code='HK-ALL',name='Học kỳ tất cả'); one=Course(code='ALL1',name='Môn một'); two=Course(code='ALL2',name='Môn hai')
        student=Student(student_code='ALL-STUDENT',full_name='Học nhiều môn',class_name='CNTT 20-01',email='all@example.com')
        db.session.add_all([sem,one,two,student]); db.session.flush()
        db.session.add_all([
            Enrollment(student=student,course=one,semester=sem,current_week=6,score=2,attendance_rate=5,late_submissions=4),
            Enrollment(student=student,course=two,semester=sem,current_week=6,score=9,attendance_rate=9,late_submissions=0),
        ]); db.session.commit(); sem_id=sem.id
        app.config.update(MAIL_MODE='smtp',SMTP_HOST='smtp.example.com',SMTP_USERNAME='test@example.com',SMTP_PASSWORD='secret',SMTP_FROM='test@example.com')
    monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
    page=auth.get(f'/analysis?semester_id={sem_id}&week=6')
    assert page.status_code==200 and 'Tất cả môn học' in page.text and '2 enrollment trong phạm vi' in page.text
    result=auth.post('/predict/batch',data={'semester_id':sem_id,'week':6},follow_redirects=True)
    assert result.status_code==200 and 'Đã hoàn thành dự báo cho 2 enrollment (1 sinh viên' in result.text
    assert not captured
    with app.app_context():
        assert Prediction.query.count()==2
        assert db.session.query(Prediction.enrollment_id).distinct().count()==2

def test_empty_dashboard_onboarding_does_not_seed_on_startup_or_recreate(app, auth):
    with app.app_context():
        assert Student.query.count()==0 and Enrollment.query.count()==0
    page=auth.get('/')
    assert page.status_code==200
    assert 'Bắt đầu sử dụng hệ thống' in page.text
    assert 'Trải nghiệm với dữ liệu mẫu' in page.text and 'TẢI DỮ LIỆU HỌC TẬP' in page.text
    assert 'dashboard-onboarding' in page.text and 'action="/data/import/dashboard"' in page.text
    assert 'data-open-dialog="dashboard-demo-dialog"' in page.text and '.showModal()' in page.text
    with app.app_context():
        assert Student.query.count()==0 and Enrollment.query.count()==0 and ImportBatch.query.count()==0

def test_dashboard_onboarding_when_students_exist_without_academic_data_blocks_demo(app, auth):
    with app.app_context():
        db.session.add(Student(student_code='ONLY-STUDENT',full_name='Chỉ sinh viên',class_name='C1')); db.session.commit()
    page=auth.get('/')
    assert page.status_code==200 and 'DEMO chỉ được khởi tạo trên database trống' in page.text
    assert 'data-open-dialog="dashboard-demo-dialog"' not in page.text
    result=auth.post('/data/seed-demo',follow_redirects=True)
    assert 'Dữ liệu demo đã tồn tại hoặc database đã có dữ liệu' in result.text
    with app.app_context():
        assert Student.query.count()==1 and Enrollment.query.count()==0 and ImportBatch.query.count()==0

def test_dashboard_with_enrollment_is_not_onboarding_even_without_prediction(app, auth):
    with app.app_context():
        student=Student(student_code='HAS-ENROLL',full_name='Có dữ liệu',class_name='C1'); course=Course(code='HAS',name='Môn'); semester=Semester(code='HAS',name='Học kỳ')
        db.session.add_all([student,course,semester]); db.session.flush()
        db.session.add(Enrollment(student=student,course=course,semester=semester,current_week=5,score=6,attendance_rate=8,late_submissions=0)); db.session.commit()
    page=auth.get('/')
    assert page.status_code==200 and 'Bắt đầu sử dụng hệ thống' not in page.text
    with app.app_context(): assert Prediction.query.count()==0 and Student.query.count()==1

def test_dashboard_inline_upload_uses_preview_then_existing_import_confirm(app, auth):
    content=_csv('INLINE1,Nhập trực tiếp,CNTT 20-01,,CS101,Cơ sở dữ liệu,HKINLINE,Học kỳ inline,5,7,8,0')
    preview=auth.post('/data/import/dashboard',data={'file':(content,'inline.csv')},content_type='multipart/form-data',follow_redirects=True)
    assert preview.status_code==200 and 'Sẵn sàng nhập dữ liệu' in preview.text and 'inline.csv' in preview.text
    completed=auth.post('/data/import/confirm',follow_redirects=True)
    assert completed.status_code==200 and 'Bắt đầu sử dụng hệ thống' not in completed.text
    with app.app_context(): assert Enrollment.query.count()==1 and ImportBatch.query.count()==1

def test_dashboard_header_has_single_title_and_product_subtitle(app, auth):
    page=auth.get('/')
    assert page.text.count('Hệ thống phân tích kết quả học tập và dự báo nguy cơ trượt môn')==1
    assert 'Theo dõi kết quả học tập • Phân tích dữ liệu • Cảnh báo sớm' in page.text

def test_dashboard_blocks_demo_when_only_import_history_exists(app, auth):
    with app.app_context():
        admin=User.query.filter_by(username='admin').one()
        db.session.add(ImportBatch(batch_id='history-only',filename='old.csv',data_type='USER',imported_by=admin.id,record_count=0,success_count=0,failed_count=0))
        db.session.commit()
    page=auth.get('/')
    assert 'DEMO chỉ được khởi tạo trên database trống' in page.text
    assert 'data-open-dialog="dashboard-demo-dialog"' not in page.text

def test_seed_demo_rejects_invalid_csrf(app, client):
    import re
    app.config['WTF_CSRF_ENABLED']=True
    login=client.get('/login')
    token=re.search(r'name="csrf_token" value="([^"]+)"',login.text).group(1)
    assert client.post('/login',data={'username':'admin','password':'StrongPass123!','csrf_token':token}).status_code==302
    assert client.post('/data/seed-demo').status_code==400
    with app.app_context():
        assert Student.query.count()==0 and Enrollment.query.count()==0 and ImportBatch.query.count()==0

def test_seed_demo_accepts_valid_csrf_on_empty_database(app, client):
    import re
    app.config['WTF_CSRF_ENABLED']=True
    login=client.get('/login')
    login_token=re.search(r'name="csrf_token" value="([^"]+)"',login.text).group(1)
    assert client.post('/login',data={'username':'admin','password':'StrongPass123!','csrf_token':login_token}).status_code==302
    dashboard=client.get('/')
    assert 'data-open-dialog="dashboard-demo-dialog"' in dashboard.text
    token=re.search(r'name="csrf_token" value="([^"]+)"',dashboard.text).group(1)
    response=client.post('/data/seed-demo',data={'csrf_token':token},follow_redirects=True)
    assert response.status_code==200 and 'Đã tải 30 bản ghi DEMO' in response.text
    with app.app_context():
        assert Student.query.count()==30 and Enrollment.query.count()>0 and ImportBatch.query.count()==1

def test_dashboard_import_preview_does_not_predict_before_confirmation(app, auth):
    content=_csv('PREVIEW1,Xem trước,CNTT 20-01,,CS101,Cơ sở dữ liệu,HKPRE,Học kỳ preview,5,2,5,4')
    response=auth.post('/data/import/dashboard',data={'file':(content,'preview.csv')},content_type='multipart/form-data',follow_redirects=True)
    assert response.status_code==200 and 'Sẵn sàng nhập dữ liệu' in response.text
    with app.app_context():
        assert Enrollment.query.count()==0 and Prediction.query.count()==0

def test_confirmed_import_runs_prediction_for_new_eligible_records_without_smtp(app, auth, monkeypatch):
    import pandas as pd
    import smtplib
    class NoSMTP:
        def __init__(self,*args,**kwargs): raise AssertionError('SMTP must not be used during direct import')
    with app.app_context():
        model=RandomForestClassifier(n_estimators=10,random_state=42).fit(pd.DataFrame([[2,5,4],[9,10,0]],columns=FEATURES),[1,0])
        joblib.dump({'model':model,'metadata':{'version':'import-predict-v1','features':FEATURES}},app.config['MODEL_PATH'])
    monkeypatch.setattr(smtplib,'SMTP',NoSMTP)
    content=_csv('AUTO1,Tự dự báo,CNTT 20-01,auto@example.com,CS101,Cơ sở dữ liệu,HKAUTO,Học kỳ auto,5,2,5,4')
    auth.post('/data/import/dashboard',data={'file':(content,'auto.csv')},content_type='multipart/form-data',follow_redirects=True)
    response=auth.post('/data/import/confirm',follow_redirects=True)
    assert response.status_code==200 and 'Đã dự báo 1' in response.text and 'Không gửi email tự động' in response.text
    with app.app_context():
        assert Enrollment.query.count()==1 and Prediction.query.count()==1 and Alert.query.count()==1 and EmailLog.query.count()==0

def test_confirmed_import_skips_weeks_before_five(app, auth):
    content=_csv('EARLY1,Tuần sớm,CNTT 20-01,,CS101,Cơ sở dữ liệu,HKEARLY,Học kỳ sớm,4,2,5,4')
    auth.post('/data/import/dashboard',data={'file':(content,'early.csv')},content_type='multipart/form-data',follow_redirects=True)
    response=auth.post('/data/import/confirm',follow_redirects=True)
    assert response.status_code==200 and 'Đã dự báo 0; bỏ qua 1' in response.text
    with app.app_context():
        assert Enrollment.query.count()==1 and Prediction.query.count()==0

def test_import_is_retained_when_prediction_model_fails(app, auth):
    content=_csv('KEEP1,Giữ import,CNTT 20-01,,CS101,Cơ sở dữ liệu,HKKEEP,Học kỳ giữ,5,2,5,4')
    auth.post('/data/import/dashboard',data={'file':(content,'keep.csv')},content_type='multipart/form-data',follow_redirects=True)
    response=auth.post('/data/import/confirm',follow_redirects=True)
    assert response.status_code==200 and 'lỗi 1' in response.text
    with app.app_context():
        assert Enrollment.query.count()==1 and ImportBatch.query.count()==1 and Prediction.query.count()==0
