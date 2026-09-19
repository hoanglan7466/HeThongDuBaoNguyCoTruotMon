# Câu hỏi phản biện và trả lời dựa trên implementation

**Vì sao dùng Random Forest?** `RandomForestClassifier` xử lý quan hệ phi tuyến giữa điểm, chuyên cần và nộp trễ, không cần chuẩn hóa đặc trưng và có feature importance. Bản hiện tại train 250 cây, `max_depth=8`, `min_samples_leaf=3`, `class_weight="balanced"`, `random_state=42`.

**Input và target của model là gì?** Input đúng ba trường tại thời điểm dự báo: `score`, `attendance_rate`, `late_submissions`. Target nhị phân là nhãn trượt tổng hợp (`failed`) của dataset mô phỏng. Không dùng điểm cuối kỳ hay thông tin sau tuần dự báo làm input.

**Vì sao dự báo từ tuần 5?** Quy tắc nghiệp vụ chặn dự báo chính thức khi `current_week < 5`; giao diện và service đều trả thông báo chưa đủ dữ liệu. Từ tuần 5, hệ thống lưu `week_number` snapshot cùng prediction để lịch sử không bị đổi khi dữ liệu học tập được cập nhật.

**Train/test như thế nào và metrics lấy ở đâu?** Dataset synthetic tạo hai snapshot tuần 5–6 cho mỗi sinh viên mô phỏng, sau đó dùng `GroupShuffleSplit(test_size=.25, random_state=42)` theo sinh viên. Vì vậy snapshot của cùng một sinh viên không đồng thời nằm ở train và test. Accuracy, precision, recall, F1, confusion matrix và ROC-AUC được tính trên tập test hold-out, sau đó lưu trong joblib, `metadata.json` và bảng `model_version`; không có metric hard-code trên UI.

**Có data leakage không?** Không có feature từ tương lai hay kết quả cuối kỳ trong input. Target synthetic được sinh từ latent traits trước khi tạo các chỉ số quan sát được, và split theo sinh viên xảy ra trước `fit`; test set không tham gia huấn luyện. Tuy nhiên đây là dữ liệu mô phỏng, nên trước vận hành thật cần đánh giá theo thời gian bằng dữ liệu hợp pháp.

**Dataset lấy ở đâu? Có phải dữ liệu thật của Đại học Đại Nam?** Không. Dữ liệu `DEMO###` và dataset train là dữ liệu giả lập phục vụ demo/kiểm thử; UI, README và kịch bản demo đều nêu rõ điều này.

**Ngưỡng rủi ro là gì?** Mặc định: dưới 0,35 là ổn định; từ 0,35 đến dưới 0,65 là cần theo dõi; từ 0,65 là nguy cơ cao. Hai ngưỡng lấy từ biến môi trường và được kiểm tra thứ tự hợp lệ khi tạo app.

**Password và phân quyền được xử lý ra sao?** Mật khẩu được băm bằng Werkzeug, không so sánh plaintext trong logic đăng nhập. Flask-WTF bảo vệ CSRF, Flask-Login quản lý session, và mọi route quản trị được kiểm tra role `ADMIN` ở backend. `COVAN` truy cập URL quản trị nhận 403.

**Email có gửi thật không?** Chỉ gửi SMTP khi đủ toàn bộ credential. Nếu chưa cấu hình, cảnh báo được lưu `DEV_PREVIEW` trong `email_log`, không tạo trạng thái `SENT` giả và ứng dụng vẫn hoạt động.

**Nếu model dự báo sai thì sao?** Xác suất chỉ dùng để ưu tiên hỗ trợ; không thay thế quyết định của giảng viên/cố vấn. Cần rà soát dữ liệu, ngưỡng, chất lượng mô hình và can thiệp của con người trước mọi quyết định học vụ.
