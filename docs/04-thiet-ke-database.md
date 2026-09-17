# 4. Thiết kế database

Các thực thể: `user`, `student`, `course`, `semester`, `enrollment`, `prediction`, `alert`, `recommendation`, `email_log`, `model_version`. Enrollment liên kết sinh viên–môn–học kỳ và lưu snapshot chỉ số tuần hiện tại. Prediction lưu xác suất, mức nguy cơ, phiên bản và thời gian.

Khóa ngoại duy trì toàn vẹn; unique constraint chống ghi danh trùng và cảnh báo trùng trên cùng prediction; index đặt trên mã sinh viên, mức nguy cơ, trạng thái và các khóa tra cứu. Mật khẩu chỉ lưu hash.
