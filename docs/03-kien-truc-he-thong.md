# 3. Kiến trúc hệ thống

Ứng dụng dùng Flask application factory. Routes xử lý HTTP và phân quyền; services chứa import, dự báo, risk threshold, khuyến nghị và email; SQLAlchemy models quản lý lưu trữ. Jinja/CSS/Chart.js tạo giao diện. ML artifact được joblib hóa và nạp khi dự báo.

Luồng dữ liệu: CSV → validate/preview → transaction database → feature vector → Random Forest → prediction → recommendation/alert → dashboard/report. SQLite phục vụ dev/test; production dùng MySQL qua `DATABASE_URL`.
