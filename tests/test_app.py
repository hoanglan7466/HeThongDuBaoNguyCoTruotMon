import csv, io, joblib, pytest
from sklearn.ensemble import RandomForestClassifier
from app.extensions import db
from app.models import Alert, Course, EmailLog, Enrollment, Prediction, Recommendation, Semester, Student, User
from app.services import FEATURES, import_rows, predict_enrollment, risk_level, validate_csv

def test_health(client):
    r=client.get("/health"); assert r.status_code==200 and r.json["database"]=="ok"

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
    rows,errors=validate_csv(io.BytesIO((header+"S01,An,C1,a@b.vn,M1,Mon,HK1,Hoc ky,5,12,90,0\n").encode()))
    assert not rows and "Điểm" in errors[0]["message"]

def test_risk_thresholds(app):
    with app.app_context(): assert risk_level(.8)=="CAO" and risk_level(.5)=="TRUNG_BINH" and risk_level(.1)=="ON_DINH"

def test_prediction_and_alert(app,tmp_path):
    with app.app_context():
        import pandas as pd
        x=pd.DataFrame([[2,55,5],[3,60,4],[8,95,0],[9,100,0],[4,65,3],[7,90,1]],columns=FEATURES); y=[1,1,0,0,1,0]
        m=RandomForestClassifier(n_estimators=20,random_state=42).fit(x,y); joblib.dump({"model":m,"metadata":{"version":"test-v1"}},app.config["MODEL_PATH"])
        s=Student(student_code="S1",full_name="SV",class_name="C1"); c=Course(code="C",name="Môn"); sem=Semester(code="HK",name="Học kỳ"); db.session.add_all([s,c,sem]); db.session.flush(); e=Enrollment(student=s,course=c,semester=sem,current_week=5,score=2,attendance_rate=55,late_submissions=5); db.session.add(e); db.session.commit()
        p=predict_enrollment(e); assert p.probability>=.65 and p.week_number==5 and Alert.query.count()==1
        assert Prediction.query.count()==1 and db.session.execute(db.text("SELECT 1")).scalar()==1
        assert Recommendation.query.count()>=1 and EmailLog.query.filter_by(status="DEV_PREVIEW").count()==0
        first_recommendations=Recommendation.query.count(); predict_enrollment(e)
        assert Alert.query.count()==1 and Recommendation.query.count()==first_recommendations

def test_prediction_requires_week_five(app, tmp_path):
    with app.app_context():
        s=Student(student_code="EARLY",full_name="Early",class_name="C1"); c=Course(code="EARLY",name="Môn"); sem=Semester(code="EARLY",name="Học kỳ")
        db.session.add_all([s,c,sem]); db.session.flush(); enrollment=Enrollment(student=s,course=c,semester=sem,current_week=4,score=5,attendance_rate=80,late_submissions=0); db.session.add(enrollment); db.session.commit()
        with pytest.raises(ValueError, match="Chưa đủ dữ liệu"): predict_enrollment(enrollment)
        assert Prediction.query.count()==0

def test_prediction_allows_week_six(app, tmp_path):
    with app.app_context():
        import pandas as pd
        model=RandomForestClassifier(n_estimators=10,random_state=42).fit(pd.DataFrame([[2,55,5],[9,98,0]],columns=FEATURES),[1,0])
        joblib.dump({"model":model,"metadata":{"version":"week-six","features":FEATURES}},app.config["MODEL_PATH"])
        s=Student(student_code="WEEK6",full_name="Week Six",class_name="C1"); c=Course(code="W6",name="Môn tuần 6"); sem=Semester(code="W6",name="Học kỳ")
        db.session.add_all([s,c,sem]); db.session.flush(); enrollment=Enrollment(student=s,course=c,semester=sem,current_week=6,score=2,attendance_rate=55,late_submissions=5); db.session.add(enrollment); db.session.commit()
        assert predict_enrollment(enrollment).week_number==6

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
    valid="S1,An,C1,,M1,Mon,HK1,Hoc ky,5,5,80,0"
    rows,errors=validate_csv(io.BytesIO((",".join(["student_code","full_name","class_name","email","course_code","course_name","semester_code","semester_name","current_week","score","attendance_rate","late_submissions"])+"\n"+valid+"\n"+valid+"\n").encode()))
    assert len(rows)==1 and any("trùng" in error["message"] for error in errors)

def test_import_is_atomic_on_duplicate(app):
    rows,errors=validate_csv(_csv("S1,An,C1,,M1,Mon,HK1,Hoc ky,5,5,80,0")); assert not errors
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

def test_report_filter_and_export(app,auth,tmp_path):
    with app.app_context():
        import pandas as pd
        model=RandomForestClassifier(n_estimators=20,random_state=42).fit(pd.DataFrame([[1,50,5],[9,100,0]],columns=FEATURES),[1,0])
        joblib.dump({"model":model,"metadata":{"version":"filter-v1","features":FEATURES}},app.config["MODEL_PATH"])
        s=Student(student_code="FILTER1",full_name="Nguyễn An",class_name="C1"); c=Course(code="M1",name="Môn 1"); sem=Semester(code="HK1",name="Học kỳ 1")
        db.session.add_all([s,c,sem]); db.session.flush(); course_id, semester_id=c.id, sem.id; e=Enrollment(student=s,course=c,semester=sem,current_week=5,score=1,attendance_rate=50,late_submissions=5); db.session.add(e); db.session.commit(); predict_enrollment(e)
    response=auth.get("/reports?risk=CAO&q=FILTER")
    assert response.status_code==200 and "FILTER1" in response.text
    exported=auth.get("/reports/export.csv?risk=CAO&q=FILTER")
    decoded=exported.data.decode("utf-8-sig")
    assert exported.status_code==200 and exported.data.startswith(b"\xef\xbb\xbf") and "FILTER1" in decoded and "Tuần dự báo" in decoded
    assert auth.get(f"/reports?course_id={course_id}&semester_id={semester_id}").status_code==200

def test_database_constraints(app):
    with app.app_context():
        s=Student(student_code="BAD",full_name="Bad",class_name="C"); c=Course(code="BAD",name="Bad"); sem=Semester(code="BAD",name="Bad"); db.session.add_all([s,c,sem]); db.session.flush()
        db.session.add(Enrollment(student=s,course=c,semester=sem,current_week=5,score=11,attendance_rate=80,late_submissions=0))
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
        x=pd.DataFrame([[1,50,6],[2,55,5],[5,78,2],[6,82,1],[9,98,0],[8,92,0]],columns=FEATURES); y=[1,1,1,0,0,0]
        model=RandomForestClassifier(n_estimators=30,random_state=42).fit(x,y)
        joblib.dump({"model":model,"metadata":{"version":"demo-flow-v1","features":FEATURES}},app.config["MODEL_PATH"])
    response=auth.post("/data/seed-demo",follow_redirects=True)
    assert response.status_code==200 and "DỮ LIỆU DEMO" in response.text
    with app.app_context():
        assert Student.query.filter_by(is_demo=True).count()==30
        assert Prediction.query.count()==18 and Recommendation.query.count()>=18
        assert Alert.query.count()>0 and EmailLog.query.filter_by(status="DEV_PREVIEW").count()==Alert.query.count()
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
        app.config.update(SMTP_HOST='smtp.example.invalid',SMTP_USERNAME='test',SMTP_PASSWORD=None,SMTP_FROM=None)
        assert send_email('demo@example.invalid','Test','Preview')=='DEV_PREVIEW'
        app.config.update(SMTP_PASSWORD='test',SMTP_FROM='test@example.invalid')
        def fail(*args, **kwargs): raise smtplib.SMTPException('unavailable')
        monkeypatch.setattr(smtplib,'SMTP',fail)
        assert send_email('demo@example.invalid','Test','Preview')=='FAILED'
        assert EmailLog.query.filter_by(status='SENT').count()==0


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
        rows, errors=validate_csv(_csv('REAL1,Real,C1,,M1,Môn 1,HK1,Học kỳ 1,5,7,90,0')); assert not errors
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
