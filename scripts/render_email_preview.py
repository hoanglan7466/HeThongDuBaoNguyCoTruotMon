"""Create a local, non-delivery preview from the production warning templates."""
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app import create_app
from app.services import render_risk_alert_email


def main():
    app=create_app({"TESTING":True})
    student=SimpleNamespace(full_name="Sinh viên DEMO")
    enrollment=SimpleNamespace(course=SimpleNamespace(name="Cơ sở dữ liệu"),current_week=5,score=5.8,attendance_rate=7.2,late_submissions=2)
    prediction=SimpleNamespace(risk_level="TRUNG_BINH",probability=.68)
    recommendations=[SimpleNamespace(content=value) for value in (
        "Ôn tập lại các nội dung chưa đạt yêu cầu.",
        "Cải thiện mức độ tham gia lớp học.",
        "Hoàn thành bài tập đúng hạn.",
        "Trao đổi với cố vấn học tập nếu cần hỗ trợ.",
    )]
    with app.app_context():
        _,html=render_risk_alert_email(student,enrollment,prediction,recommendations)
    path=ROOT/"artifacts"/"email-preview.html"
    path.parent.mkdir(exist_ok=True)
    path.write_text(html,encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
