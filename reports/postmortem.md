# Postmortem - DR Drill Lab 23

Báo cáo tập trung phân tích hành vi của hệ thống và quy trình vận hành.

## Timeline

| Thời gian           | Sự kiện                                            | Evidence                          |
| ------------------- | -------------------------------------------------- | --------------------------------- |
| 2026-08-25T13:33:06 | Outage của Region A bắt đầu.                       | `chaos/chaos-events.jsonl:8`      |
| 2026-08-25T13:33:08 | Request đầu tiên của người dùng bị lỗi.            | `reports/drill-2-withdr.jsonl:23` |
| 2026-08-25T13:33:24 | Health checker đánh dấu Region A là UNHEALTHY.     | `reports/health-events.jsonl:2`   |
| 2026-08-25T13:33:34 | Region B sẵn sàng và DNS đã cut over.              | `reports/failover-events.jsonl:4` |
| 2026-08-25T13:33:38 | Request thành công đầu tiên được Region B phục vụ. | `reports/drill-2-withdr.jsonl:36` |

## Phân tích RTO/RPO và gap

- RTO target: 300 s; RTO đo được: 31.8 s; gap: thấp hơn mục tiêu 268.2 s.
- RPO target: 300 s; RPO đo được: 28.02 s và 14 documents lost; gap: thấp hơn mục tiêu 271.98 s.
- Thành phần mất nhiều thời gian nhất là health-check detection, với 18.3 s. Detection floor được cấu hình là 15.0 s (interval=5.0s x threshold=3), phần chênh lệch còn lại đến từ timeout và lịch poll.

## Root cause (5 whys)

1. Request của người dùng lỗi vì endpoint Region A đang active không còn trả lời.
2. Traffic vẫn đến Region A cho đến khi health threshold đạt ngưỡng và quy trình failover có kiểm soát hoàn tất
3. Region B được thiết kế ở trạng thái passive --> cần restore snapshot và chờ GPU pool sẵn sàng trước khi chính thức chuyển đổi tên miền
4. Độ trễ là kết quả của anti-flap threshold, snapshot workflow, warm-up và edge TTL; đây không phải lỗi thao tác của một cá nhân.
5. Hệ thống phục hồi vì runbook restore replica, kiểm tra readiness, rồi mới chuyển traffic sau khi Region B có thể phục vụ request.

## Action items

|   # | Action item                                                                            | Owner         | Deadline   | Tác động dự kiến đến RTO/RPO                                           |
| --: | -------------------------------------------------------------------------------------- | ------------- | ---------- | ---------------------------------------------------------------------- |
|   1 | Thử nghiệm health interval 3 s với cùng threshold trong các tình huống lỗi thoáng qua. | SRE           | 2026-09-08 | Giảm detection floor tối đa 6 s nếu false positive vẫn chấp nhận được. |
|   2 | Thử nghiệm standby pool đã warm trong game day tiếp theo.                              | Platform team | 2026-09-15 | Giảm khoảng 7.3 s GPU warm-up, đổi lại là chi phí capacity.            |
|   3 | Giảm replication interval sau khi đo storage và network load.                          | Data platform | 2026-09-15 | Giảm RPO và số document bị mất.                                        |

## Trả lời các câu hỏi bắt buộc

1. Interval x threshold là 5.0s x 3 = 15.0s. Detection quan sát được là 18.3 s, chiếm khoảng 57.5% RTO 31.8s
2. Nếu giảm interval xuống 1s, detection floor lý thuyết giảm 12.0s. Đổi lại là probe nhiều hơn và rủi ro transient failure tức là lỗi thoáng qua,chẳng hạn như lỗi mạng tạm thời, gây ra hiện tượng chập chờ nhiều hơn
3. Nếu Region A mất hoàn toàn trong 6 giờ, 14 documents lost của drill này là các lần ghi xảy ra sau snapshot gần nhất. Khách hàng có thể mất các document đó hoặc nhận kết quả retrieval cũ cho đến khi dữ liệu được tạo lại
