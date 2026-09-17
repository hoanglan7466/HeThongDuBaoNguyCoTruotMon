# 5. Machine Learning

Pipeline: tạo/đọc dữ liệu → validate → chia train/test có stratify → RandomForestClassifier với `random_state=42` và class weight → đánh giá → lưu model/metadata → load và predict. Features: `score`, `attendance_rate`, `late_submissions`; không thêm thuộc tính vô nghĩa.

Metrics gồm accuracy, precision, recall, F1, confusion matrix; tất cả được sinh từ lần chạy thật. Dataset đi kèm để phát triển là synthetic và metadata ghi rõ điều này. Feature importance chỉ phản ánh đóng góp trong model, không chứng minh quan hệ nhân quả.
