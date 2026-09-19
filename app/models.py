from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from .extensions import db

def now(): return datetime.now(timezone.utc)

class User(UserMixin, db.Model):
    __table_args__ = (db.CheckConstraint("role IN ('ADMIN','COVAN')", name="ck_user_role"),)
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="COVAN")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    @property
    def is_active(self): return self.active
    def set_password(self, value): self.password_hash = generate_password_hash(value)
    def check_password(self, value): return check_password_hash(self.password_hash, value)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    class_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(150))
    is_demo = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    enrollments = db.relationship("Enrollment", back_populates="student", cascade="all, delete-orphan")

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)

class Semester(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    is_current = db.Column(db.Boolean, nullable=False, default=False)

class Enrollment(db.Model):
    __table_args__ = (
        db.UniqueConstraint("student_id", "course_id", "semester_id", "current_week", name="uq_enrollment_snapshot"),
        db.CheckConstraint("score >= 0 AND score <= 10", name="ck_enrollment_score"),
        db.CheckConstraint("attendance_rate >= 0 AND attendance_rate <= 100", name="ck_enrollment_attendance"),
        db.CheckConstraint("late_submissions >= 0", name="ck_enrollment_late"),
        db.CheckConstraint("current_week >= 1", name="ck_enrollment_week"),
    )
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey("semester.id"), nullable=False)
    current_week = db.Column(db.Integer, nullable=False, default=5)
    score = db.Column(db.Float, nullable=False)
    attendance_rate = db.Column(db.Float, nullable=False)
    late_submissions = db.Column(db.Integer, nullable=False, default=0)
    import_batch_id = db.Column(db.Integer, db.ForeignKey("import_batch.id"), index=True)
    final_failed = db.Column(db.Boolean)
    student = db.relationship("Student", back_populates="enrollments")
    course = db.relationship("Course")
    semester = db.relationship("Semester")
    predictions = db.relationship("Prediction", back_populates="enrollment", cascade="all, delete-orphan")
    import_batch = db.relationship("ImportBatch", back_populates="enrollments")

class ImportBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(36), unique=True, nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    data_type = db.Column(db.String(10), nullable=False)  # DEMO or USER
    imported_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    imported_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    record_count = db.Column(db.Integer, nullable=False)
    success_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="COMPLETED")
    user = db.relationship("User")
    enrollments = db.relationship("Enrollment", back_populates="import_batch")

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    target = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class Prediction(db.Model):
    __table_args__ = (
        db.UniqueConstraint("enrollment_id", "model_version", name="uq_prediction_snapshot_model"),
        db.CheckConstraint("probability >= 0 AND probability <= 1", name="ck_prediction_probability"),
        db.CheckConstraint("risk_level IN ('CAO','TRUNG_BINH','ON_DINH')", name="ck_prediction_risk"),
        db.CheckConstraint("week_number >= 5", name="ck_prediction_week"),
    )
    id = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("enrollment.id"), nullable=False, index=True)
    probability = db.Column(db.Float, nullable=False)
    risk_level = db.Column(db.String(20), nullable=False, index=True)
    model_version = db.Column(db.String(60), nullable=False)
    factors_json = db.Column(db.Text, nullable=False, default="[]")
    # Snapshot at prediction time.  Enrollment.current_week can change after import.
    week_number = db.Column(db.Integer, nullable=False, default=5)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    enrollment = db.relationship("Enrollment", back_populates="predictions")

class Alert(db.Model):
    __table_args__ = (db.UniqueConstraint("enrollment_id", "prediction_id"),)
    id = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("enrollment.id"), nullable=False, index=True)
    prediction_id = db.Column(db.Integer, db.ForeignKey("prediction.id"), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="MOI", index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    enrollment = db.relationship("Enrollment")

class Recommendation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("enrollment.id"), nullable=False, index=True)
    category = db.Column(db.String(30), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient = db.Column(db.String(150), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    preview = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class ModelVersion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.String(60), unique=True, nullable=False)
    metrics_json = db.Column(db.Text, nullable=False)
    features_json = db.Column(db.Text, nullable=False)
    artifact_path = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    trained_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
