# 5. Machine Learning

Pipeline: tạo/đọc dữ liệu → validate → chia train/test có stratify → RandomForestClassifier với `random_state=42` và class weight → đánh giá → lưu model/metadata → load và predict. Features: `score`, `attendance_rate`, `late_submissions`; không thêm thuộc tính vô nghĩa.

Metrics gồm accuracy, precision, recall, F1, confusion matrix; tất cả được sinh từ lần chạy thật. Dataset đi kèm để phát triển là synthetic và metadata ghi rõ điều này. Feature importance chỉ phản ánh đóng góp trong model, không chứng minh quan hệ nhân quả.

Pipeline hiện tạo 1.000 dòng synthetic độc lập, chia 750 dòng train và 250 dòng test có stratify với seed 42. Metadata lưu riêng `dataset_rows`, `training_rows`, `test_samples` và ROC-AUC tính từ xác suất trên test. Không scale ba feature số vì Random Forest không yêu cầu bước đó; thứ tự feature được dùng chung cho train và inference. Nhãn được sinh từ công thức có nhiễu trên ba feature, không đưa nhãn cuối kỳ vào đầu vào. Không fit trên test; kết quả chỉ xác minh pipeline synthetic, chưa chứng minh khả năng tổng quát hóa cho sinh viên thật. CLI hiện chưa huấn luyện từ dữ liệu CSV nhập vào ứng dụng.

Khuyến nghị là các quy tắc theo điểm, chuyên cần và nộp trễ, không phải mô hình AI sinh nội dung. Ngưỡng nguy cơ lấy từ cấu hình chung và được kiểm tra `0 <= medium < high <= 1`.
