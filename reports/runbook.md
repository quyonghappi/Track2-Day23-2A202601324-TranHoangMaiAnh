# Runbook - Xử lý outage Region A

Phạm vi: Region A đang phục vụ traffic, Region B là region dự phòng. On-call thực hiện các bước kỹ thuật, Incident Commander (IC) chịu trách nhiệm ra quyết định về rủi ro vàrollback.

|   # | Bước                               | Lệnh có thể copy-paste                                                                | Hoàn thành khi                                                                                     | Người phụ trách |
| --: | ---------------------------------- | ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | --------------- |
|   1 | Xác nhận outage                    | `python dr/runbook.py --primary a --target b --backend fs`                            | Dòng JSONL đầu tiên cho thấy A không ready và B có thể truy cập trong `reports/runbook-run.jsonl`. | On-call         |
|   2 | Mở incident và bắt đầu đồng hồ RTO | `python -c "import time; print(time.time())"`                                         | Timestamp incident và primary bị ảnh hưởng được ghi ở step 2 trong `reports/runbook-run.jsonl`.    | On-call / IC    |
|   3 | Restore state và scale pool        | `python dr/runbook.py --primary a --target b --backend fs --auto`                     | `2_restore_snapshot` và `3_scale_pool` thành công trong `reports/failover-events.jsonl`.           | DR operator     |
|   4 | Kiểm tra target ready và replica   | `curl http://127.0.0.1:8002/readyz`                                                   | HTTP 200; step 4 ghi metadata đã restore và RPO trong `reports/runbook-run.jsonl`.                 | DR operator     |
|   5 | Kiểm tra DNS/LB cutover            | `curl http://127.0.0.1:8080/edge/state`                                               | `active_region` là `b` và `5_dns_cutover` thành công trong `reports/failover-events.jsonl`.        | On-call         |
|   6 | Kiểm tra golden signals            | `python dr/runbook.py --primary a --target b --backend fs --auto`                     | Step 6 ghi 10 request, error rate và p95 latency trong `reports/runbook-run.jsonl`.                | SRE             |
|   7 | Đo recovery và kết thúc pha xử lý  | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | Kết quả có `valid: true`, warnings rỗng và `rto_verdict: PASS`.                                    | IC              |

Trong vận hành bình thường, dùng lệnh có màn hình xác nhận, tức không thêm `--auto`. Tham số `--auto` chỉ dùng cho drill có kiểm soát hoặc CI.

## Rollback

Quy trình rollback cụ thể như sau: Không chuyển traffic về Region A chỉ vì process đã phản hồi. IC chỉ phê duyệt rollback khi Region A đã pass `/readyz`, state đã đồng bộ với Region B và 10 probe qua edge đều không lỗi. DR operator thực hiện reverse failover sau khi IC ghi lại quyết định trong incident channel. Nếu Region B không ở trạng thái healthy, restore snapshot thất bại hoặc golden-signal error rate khác 0, dừng cutover và giữ traffic tại region đang trong trạng thái healthy.
