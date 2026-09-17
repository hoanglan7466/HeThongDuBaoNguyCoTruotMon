# 2. Phân tích yêu cầu

Hai vai trò là ADMIN và COVAN. ADMIN quản lý nhập dữ liệu, tài khoản, model và báo cáo; COVAN xem dashboard, sinh viên, dự báo, cảnh báo và báo cáo. Luồng chính: nhập dữ liệu hợp lệ → chạy model từ tuần 5 → lưu xác suất/mức nguy cơ → tạo gợi ý và cảnh báo cao → theo dõi xử lý.

Yêu cầu phi chức năng: giao diện tiếng Việt responsive, KPI từ database, không bịa dữ liệu, bảo vệ mật khẩu/session/CSRF, upload giới hạn, cấu hình qua môi trường, lỗi không rò rỉ stack trace.
