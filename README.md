# Hệ thống phân tích kết quả học tập và dự báo nguy cơ trượt môn

Ứng dụng Flask hỗ trợ cố vấn theo dõi điểm, chuyên cần, nộp bài trễ và dùng `RandomForestClassifier` để ước lượng nguy cơ từ tuần 5. Kết quả là **nguy cơ dự báo**, không phải kết luận chắc chắn.

## Chức năng

- Đăng nhập và phân quyền `ADMIN` / `COVAN`, mật khẩu băm an toàn, CSRF và session bảo vệ.
- Dashboard lấy KPI từ database, biểu đồ Chart.js, empty state và nhãn dữ liệu demo.
- Danh sách, tìm kiếm, phân trang và hồ sơ sinh viên; dự báo đơn, lịch sử và yếu tố liên quan.
- Import CSV hai bước: validation theo dòng → preview → transaction xác nhận.
- Random Forest train thật, lưu artifact/metadata/version/metrics và feature importance.
- Cảnh báo chống trùng theo prediction, gợi ý rule-based, báo cáo và xuất CSV.
- SMTP thật qua biến môi trường; nếu thiếu credential thì ghi email preview vào database/log.
- `GET /health` kiểm tra database, model và SMTP.

## Kiến trúc và công nghệ

Flask application factory; SQLAlchemy; Flask-Login; Flask-WTF; MySQL 8 (SQLite cho dev/test); Pandas, scikit-learn, joblib; Jinja, CSS responsive và Chart.js. Các lớp chính nằm ở `app/models.py`, `app/services.py`, `app/routes.py`; ML artifact ở `machine_learning/models`; schema bootstrap ở `database`; kiểm thử ở `tests`.

## Cài đặt

Yêu cầu Python 3.11+ và MySQL 8 (không bắt buộc khi chạy dev SQLite).

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Tạo database MySQL bằng `database/schema.sql`, tạo user có quyền tối thiểu trên database đó rồi đặt `DATABASE_URL` trong `.env`. Ứng dụng dùng SQLite `instance/app.db` nếu chưa đặt biến này.

## Khởi tạo, train và chạy

```powershell
$env:FLASK_APP="run.py"
flask init-db
flask train-model
flask run --host 127.0.0.1 --port 5000
```

Mở `http://127.0.0.1:5000`. Môi trường DEMO/development có hai tài khoản: **ADMIN** `admin` / `Admin@123` và **CỐ VẤN** `covan` / `Covan@123`. Password chỉ được lưu dạng hash trong database. `flask init-db` tạo mới hoặc cập nhật an toàn hai tài khoản này; chỉ dùng chúng cho DEMO/development.

## Import CSV

Tải mẫu tại `/data/template.csv`. Các cột bắt buộc: `student_code, full_name, class_name, email, course_code, course_name, semester_code, semester_name, current_week, score, attendance_rate, late_submissions`. Điểm 0–10, chuyên cần 0–100, nộp trễ không âm. Dữ liệu lỗi không được ghi.

## Kiểm thử

```powershell
pytest -q
```

## SMTP

Đặt `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` trong `.env`/environment. Thiếu cấu hình sẽ chuyển sang `DEV_PREVIEW`, không gửi email thật và không làm ứng dụng crash.

## Dữ liệu và model

Không có dataset thật của Đại học Đại Nam trong project. Dữ liệu `DEMO###` và dữ liệu train được ghi rõ là giả lập phục vụ kiểm thử. Metrics trong `metadata.json` được sinh từ lần train thật trên synthetic dataset, không đại diện hiệu quả thực tế. Khi có dữ liệu hợp pháp, import CSV và huấn luyện lại theo quy trình quản trị dữ liệu.

## Troubleshooting

Ứng dụng tự nạp `.env` tại thư mục gốc project khi chạy bằng Python hoặc Flask CLI; biến môi trường đã đặt được ưu tiên. Không sao chép đè `.env` khi đã cấu hình. Đặt `SECRET_KEY` ngẫu nhiên ổn định và giữ `.env` ngoài Git. `flask init-db` đồng bộ lại hash của hai tài khoản DEMO theo thông tin ở trên, bao gồm khi nâng cấp từ các tên tài khoản demo cũ.

Chạy trực tiếp trên Windows, không cần kích hoạt venv:

```powershell
.\.venv\Scripts\python.exe run.py
```

Kiểm tra server đang chạy bằng `.\.venv\Scripts\python.exe scripts/verify_local.py`. Script đọc mật khẩu từ `.env`, kiểm tra HTTP thật, hai vai trò và CSRF; tạo dữ liệu DEMO nếu chưa có, chạy dự báo và thêm lịch sử vào database local. Chỉ chạy script này trên môi trường demo.

Logs/PID trong `instance`, model artifact và `.env` không được theo dõi bởi Git. Artifact được tạo lại bằng `flask train-model`; đường dẫn trong metadata database lưu tương đối theo project.

- `Chưa có model`: chạy `flask train-model`.
- MySQL từ chối kết nối: kiểm tra service, user/password và `DATABASE_URL`; bỏ biến để chạy SQLite dev.
- Import lỗi: dùng UTF-8, đúng header và giới hạn 2 MB.
- SMTP không gửi: `/health` sẽ hiện `dev-fallback` nếu chưa có credential.
