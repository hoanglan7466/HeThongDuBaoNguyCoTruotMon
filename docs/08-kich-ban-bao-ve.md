# 8. Kịch bản bảo vệ

## Thông điệp chính

Hệ thống tạo một vòng phản hồi sớm: thu thập ba tín hiệu trực tiếp, ước lượng nguy cơ, giúp cố vấn ưu tiên can thiệp và theo dõi trạng thái. Không tuyên bố model “biết chắc” kết quả.

## Câu hỏi thường gặp

- **Vì sao Random Forest?** Phù hợp dữ liệu bảng, mô hình hóa quan hệ phi tuyến và cho feature importance; vẫn cần đối chứng khi có dữ liệu thật.
- **Metrics có đáng tin?** Metrics là kết quả chạy thật nhưng trên synthetic dataset, chỉ xác minh pipeline kỹ thuật.
- **Bảo vệ dữ liệu thế nào?** Hash mật khẩu, ORM, CSRF, session HttpOnly/SameSite, validation upload, secret qua environment và phân quyền.
- **Khi có dataset thật?** Chuẩn hóa theo CSV contract, kiểm tra chất lượng/đồng thuận sử dụng, tách train/test theo thời gian hoặc cohort, đánh giá bias và hiệu chỉnh threshold trước triển khai.
