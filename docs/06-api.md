# 6. API và routes

- `GET /health`: JSON trạng thái database/model/SMTP, không cần đăng nhập.
- `GET /api/dashboard`: KPI JSON, yêu cầu đăng nhập.
- `/login`, `POST /logout`: xác thực session.
- `/`, `/students`, `/students/<id>`: dashboard và hồ sơ.
- `POST /predict/<enrollment_id>`: dự báo từ tuần 5.
- `/data/import`, `/data/import/confirm`, `/data/template.csv`: import hai bước.
- `/alerts`, `/reports`, `/reports/export.csv`, `/model`: vận hành.

Lỗi API dùng status code HTTP; form có CSRF. Route import/seed chỉ dành cho ADMIN.
