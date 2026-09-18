import os
from datetime import timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template
from dotenv import load_dotenv
from .extensions import csrf, db, login_manager

def create_app(test_config=None):
    app=Flask(__name__,instance_relative_config=True)
    root=Path(app.root_path).parent
    load_dotenv(root / ".env", override=False)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY") or os.urandom(32),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL",f"sqlite:///{root/'instance'/'app.db'}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=2*1024*1024,
        WTF_CSRF_TIME_LIMIT=7200,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE","false").lower()=="true",
        RISK_MEDIUM_THRESHOLD=float(os.getenv("RISK_MEDIUM_THRESHOLD","0.35")),
        RISK_HIGH_THRESHOLD=float(os.getenv("RISK_HIGH_THRESHOLD","0.65")),
        MODEL_PATH=str(root/"machine_learning"/"models"/"random_forest.joblib"),
        SHOW_DEMO_ACCOUNTS=os.getenv("SHOW_DEMO_ACCOUNTS","true").lower()=="true",
        SMTP_HOST=os.getenv("SMTP_HOST"),SMTP_PORT=int(os.getenv("SMTP_PORT","587")),SMTP_USERNAME=os.getenv("SMTP_USERNAME"),SMTP_PASSWORD=os.getenv("SMTP_PASSWORD"),SMTP_FROM=os.getenv("SMTP_FROM"),SMTP_USE_TLS=os.getenv("SMTP_USE_TLS","true").lower()=="true")
    if test_config: app.config.update(test_config)
    if not 0 <= app.config["RISK_MEDIUM_THRESHOLD"] < app.config["RISK_HIGH_THRESHOLD"] <= 1:
        raise ValueError("Risk thresholds must satisfy 0 <= medium < high <= 1.")
    (root/"instance").mkdir(exist_ok=True); db.init_app(app); login_manager.init_app(app); csrf.init_app(app)
    login_manager.login_view="main.login"; login_manager.login_message="Vui lòng đăng nhập để tiếp tục."
    from .models import User
    @login_manager.user_loader
    def load_user(uid): return db.session.get(User,int(uid))
    from .routes import bp
    app.register_blueprint(bp)
    from .models import Semester, Student
    @app.context_processor
    def dataset_context():
        return {"dataset_is_demo":db.session.query(Student.id).filter(Student.is_demo.is_(True)).first() is not None,"current_semester":Semester.query.filter_by(is_current=True).order_by(Semester.id.desc()).first()}
    @app.template_filter("status_label")
    def status_label(value): return {"MOI":"Mới","DA_XEM":"Đã xem","DA_XU_LY":"Đã xử lý"}.get(value,value)
    @app.template_filter("risk_label")
    def risk_label(value): return {"CAO":"Nguy cơ cao","TRUNG_BINH":"Cần theo dõi","ON_DINH":"Ổn định"}.get(value,value)
    @app.template_filter("category_label")
    def category_label(value): return {"DIEM":"Kết quả học tập","CHUYEN_CAN":"Chuyên cần","NOP_BAI":"Tiến độ bài tập","DUY_TRI":"Duy trì kết quả"}.get(value,value)
    from .cli import register_commands
    register_commands(app)
    @app.get("/health")
    def health():
        try: db.session.execute(db.text("SELECT 1")); database="ok"
        except Exception: database="error"
        from .services import model_bundle, smtp_configured
        try: model_bundle(); model="ready"
        except FileNotFoundError: model="missing"
        except Exception: model="invalid"
        return jsonify(status="ok" if database=="ok" else "degraded",database=database,model=model,smtp="configured" if smtp_configured() else "dev-fallback"),200 if database=="ok" else 503
    @app.errorhandler(404)
    def not_found(e): return render_template("error.html",code=404,message="Không tìm thấy trang."),404
    @app.errorhandler(400)
    def bad_request(e): return render_template("error.html",code=400,message="Yêu cầu không hợp lệ."),400
    @app.errorhandler(403)
    def forbidden(e): return render_template("error.html",code=403,message="Bạn không có quyền thực hiện thao tác này."),403
    @app.errorhandler(413)
    def too_large(e): return render_template("error.html",code=413,message="Tệp tải lên vượt quá giới hạn 2 MB."),413
    @app.errorhandler(500)
    def server_error(e): db.session.rollback(); return render_template("error.html",code=500,message="Hệ thống gặp sự cố. Vui lòng thử lại."),500
    with app.app_context(): db.create_all()
    return app
