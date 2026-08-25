# Bằng chứng RTO/RPO - Lab 23

Các số liệu trong report đo trực tiếp từ log JSONL. Mỗi mốc đều có đường dẫn và số dòng evidence tương ứng

## 1. Drill 1 - baseline không có DR

| Chỉ số               |     Giá trị đo được | Evidence                        |
| -------------------- | ------------------: | ------------------------------- |
| Outage               | 2026-08-25T09:44:24 | `chaos/chaos-events.jsonl:4`    |
| Request lỗi đầu tiên |    4.3 s sau outage | `reports/drill-1-nodr.jsonl:14` |
| Phục hồi             |            Không có | `reports/drill-1-nodr.jsonl:14` |
| RTO verdict          |         NO_RECOVERY | `reports/drill-1-nodr.jsonl:17` |

Drill 1 cho thấy khi chưa có cơ chế DR, Region A bị lỗi thì người dùng tiếp tục nhận lỗi và hệ thống không tự phục hồi

## 2. Drill 2 - đã bật DR

| Mốc sự kiện                                | Giây tính từ outage | Evidence                          |
| ------------------------------------------ | ------------------: | --------------------------------- |
| Outage                                     |               0.0 s | `chaos/chaos-events.jsonl:8`      |
| Người dùng gặp lỗi đầu tiên                |               2.2 s | `reports/drill-2-withdr.jsonl:23` |
| Health check phát hiện Region A không khỏe |              18.3 s | `reports/health-events.jsonl:2`   |
| Khôi phục snapshot xong                    |              20.7 s | `reports/failover-events.jsonl:2` |
| Region B sẵn sàng                          |              28.0 s | `reports/failover-events.jsonl:4` |
| DNS chuyển sang Region B                   |              28.0 s | `reports/failover-events.jsonl:5` |
| Request thành công đầu tiên từ Region B    |              31.8 s | `reports/drill-2-withdr.jsonl:36` |

| Chỉ số              |                  Kết quả đo | Mục tiêu | Kết luận |
| ------------------- | --------------------------: | -------: | -------- |
| RTO - Inference API |                      31.8 s |    300 s | PASS     |
| RPO - Vector DB     | 28.02 s / 14 documents lost |    300 s | PASS     |

Thông tin snapshot đã khôi phục, RPO và số document bị mất được ghi tại `reports/failover-events.jsonl:2`.

## 3. Phân rã RTO

Bốn thành phần dưới đây cộng lại thành 31.8 s, bằng với RTO đo từ request phục hồi đầu tiên của load generator.

| Thành phần                            |  Giây | Cách đo và evidence                                                                                       | Hướng cải thiện                                         |
| ------------------------------------- | ----: | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Phát hiện bằng health check           | 18.3s | Alert tại `reports/health-events.jsonl:2`; detection floor cấu hình là 5.0s x 3 = 15.0s                   | Chỉ giảm interval sau khi đánh giá rủi ro flapping      |
| Restore snapshot và kích hoạt runbook |  2.4s | Từ lúc detect đến lúc restore xong: `reports/health-events.jsonl:2` đến `reports/failover-events.jsonl:2` | Chuẩn bị sẵn snapshot và rút gọn bước xác nhận vận hành |
| GPU pool warm-up                      |  7.3s | `waited_s: 7.34` tại `reports/failover-events.jsonl:4`                                                    | Duy trì warm pool nhỏ nếu chi phí phù hợp               |
| DNS/LB TTL cache                      |  3.8s | Từ cutover đến recovery: `reports/failover-events.jsonl:5` đến `reports/drill-2-withdr.jsonl:36`          | Giảm TTL và theo dõi tác động đến cache                 |
