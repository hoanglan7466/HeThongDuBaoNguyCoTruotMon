# Kịch bản demo trước hội đồng (5–7 phút)

## Chuẩn bị

Khởi động bằng lệnh duy nhất trong README, mở `http://127.0.0.1:5000`, dùng tài khoản `admin` / `Admin@123` trong môi trường DEMO/development. Nếu cơ sở dữ liệu trống, bấm **Tải dữ liệu demo**. Giao diện phải hiện rõ **DỮ LIỆU DEMO – KHÔNG PHẢI DỮ LIỆU THẬT**.

## Luồng trình bày

1. **Đăng nhập (30 giây):** giới thiệu hai vai trò ADMIN/COVAN, session, CSRF và mật khẩu băm.
2. **Tổng quan (45 giây):** chỉ ra tổng sinh viên, ba nhóm nguy cơ, biểu đồ, sinh viên cần chú ý và cảnh báo. Nhấn mạnh mọi KPI được truy vấn từ database.
3. **Sinh viên (40 giây):** tìm theo MSSV/họ tên, mở một hồ sơ ở tuần 1–4 để chỉ quy tắc chặn dự báo, rồi mở hồ sơ từ tuần 5 để giới thiệu lớp, môn, điểm, chuyên cần và số lần nộp trễ.
4. **Dữ liệu đến tuần 5 (30 giây):** mở Dữ liệu học tập để chỉ trường tuần. Nhấn mạnh dữ liệu `DEMO###` là giả lập, có nhiều tuần học và không phải dữ liệu thật của Đại học Đại Nam.
5. **Dự báo (75 giây):** bấm **Chạy dự báo** trên bản ghi từ tuần 5. Giải thích xác suất là `predict_proba` của RandomForestClassifier, được phân nhóm theo ngưỡng cấu hình; đây là tín hiệu hỗ trợ, không phải khẳng định chắc chắn sinh viên trượt.
6. **Hỗ trợ và cảnh báo (45 giây):** xem probability, các yếu tố liên quan, gợi ý dựa trên chỉ số thực tế và cảnh báo khi nguy cơ cao. Không có SMTP credential thì email được lưu ở `DEV_PREVIEW`, ứng dụng không lỗi.
7. **Báo cáo (45 giây):** lọc theo sinh viên/mức nguy cơ, xuất CSV UTF-8 và mở file để đối chiếu snapshot tuần dự báo.
8. **Mô hình (30 giây):** mở trang Mô hình, chỉ phiên bản và metrics thực tế của lần train. Nêu rõ tập hiện tại là dữ liệu tổng hợp phục vụ demo.

## Câu hỏi thường gặp về Random Forest

**Vì sao chọn Random Forest?**  Mô hình xử lý tốt quan hệ phi tuyến, ít yêu cầu chuẩn hóa và cho phép xem mức quan trọng của đặc trưng. Implementation dùng 250 cây, độ sâu tối đa 8, `min_samples_leaf=3`, cân bằng lớp và `random_state=42`.

**Dữ liệu đầu vào là gì?**  Ba đặc trưng đúng với implementation: điểm hiện tại, tỷ lệ chuyên cần và số lần nộp bài trễ. Chỉ dự báo từ tuần 5.

**Metrics lấy ở đâu?**  Accuracy, precision, recall, F1 và confusion matrix được tính trên 25% hold-out có stratify sau mỗi lần chạy `flask train-model`, sau đó ghi vào artifact joblib, metadata JSON và bảng ModelVersion; không hard-code trên giao diện.

**Xác suất có phải xác suất trượt chắc chắn không?**  Không. Đây là tỷ lệ phiếu của tập hợp cây sau huấn luyện, dùng để xếp ưu tiên hỗ trợ. Cần hiệu chỉnh và đánh giá lại bằng dữ liệu thật trước vận hành.

**Ngưỡng phân loại là gì?**  Mặc định: dưới 0,35 là ổn định; từ 0,35 đến dưới 0,65 là trung bình; từ 0,65 là cao. Có thể cấu hình bằng biến môi trường.

**Có tránh rò rỉ dữ liệu không?**  Chia train/test trước huấn luyện, test hold-out không tham gia fit. Dataset demo được gắn nhãn tổng hợp và không được trình bày như dữ liệu thực tế.

**Hạn chế chính?**  Dataset huấn luyện hiện là tổng hợp, chỉ có ba đặc trưng và chưa có kiểm định ngoài mẫu theo thời gian. Trước triển khai thật cần dữ liệu hợp pháp, đánh giá fairness, calibration, drift và quy trình con người phê duyệt can thiệp.
