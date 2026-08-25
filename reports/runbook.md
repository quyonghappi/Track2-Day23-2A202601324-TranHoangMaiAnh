# Runbook Region A outage

Mục đích: chuyển traffic an toàn từ Region A sang Region B khi A không phục vụ được. Tài liệu có thể dùng cho người ko viết ra hệ thống này. Không dùng --auto khi xử lý incident thật. Mọi lệnh chạy tại thư mục gốc repository, cần Python 3 với hai region và edge đang chạy.

## Vai trò và nguyên tắc an toàn

| Vai trò                 | Trách nhiệm / quyền quyết định                                 |
| ----------------------- | -------------------------------------------------------------- |
| On-call                 | Xác nhận sự cố, mở incident và chạy runbook đến bước xác nhận. |
| Incident Commander (IC) | Phê duyệt cutover và mọi rollback về A.                        |
| DR operator             | Chạy lệnh failover, kiểm tra state và golden signals.          |

Nguyên tắc là chỉ fail over khi A không ready và B ready, không sửa tay edge/active_region, không dùng --i-really-want-both (double outage làm drill invalid). Nếu B không healthy hoặc restore thất bại, dừng cutover, giữ traffic ở region đang healthy và báo IC.

## Quy trình thực thi

| #   | Người làm    | Lệnh copy/paste                                                                              | Thành công khi / nếu không đạt                                                                                                                                                                 |
| --- | ------------ | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0   | On-call      | `curl -fsS http://127.0.0.1:8080/edge/state`                                                 | Lưu kết quả vào incident; biết `active_region`. Nếu edge không trả lời, escalte hạ tầng trước                                                                                                  |
| 1   | On-call      | `curl -i http://127.0.0.1:8001/readyz`<br>`curl -i http://127.0.0.1:8002/readyz`             | A lỗi/timeout, B là HTTP 200. Nếu B không 200, không cutover; escalte IC                                                                                                                       |
| 2   | On-call + IC | Mở incident: ghi UTC, “A down”, kết quả hai probe, và xin IC phê duyệt.                      | Có quyết định rõ ràng trong incident, ko tự động chuyển traffic khi chưa được phê duyệt                                                                                                        |
| 3   | DR operator  | `python3 dr/runbook.py --primary a --target b --backend fs`                                  | Trả lời `y` tại prompt sau khi IC phê duyệt. Lệnh ghi timeline vào `reports/runbook-run.jsonl`; các bước 1→5 vào `reports/failover-events.jsonl`. Nếu trả về `"ok": false`, dừng và escalte IC |
| 4   | DR operator  | `curl -fsS http://127.0.0.1:8002/readyz`<br>`curl -fsS http://127.0.0.1:8080/edge/state`     | B trả HTTP 200 và `active_region` là `b`. `reports/failover-events.jsonl` phải có thứ tự `1_verify_target → 2_restore_snapshot → 3_scale_pool → 4_wait_ready → 5_dns_cutover`.                 |
| 5   | DR operator  | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300`       | Với drill: `"valid": true`, `"warnings": []`, và `rto_verdict` là `PASS`. Lưu toàn bộ JSON output vào incident/postmortem.                                                                     |
| 6   | IC           | Cập nhật incident: active region, RTO/RPO, `docs_lost`, thời điểm cutover và owner theo dõi. | Chỉ đóng incident sau khi B ổn định và theo dõi lỗi/latency bình thường                                                                                                                        |

## Rollback trở lại Region A

Chỉ IC được phép quyết định rollback, không rollback chỉ vì A đã phản hồi. Trước khi chuyển lại A, IC cần xác nhận: A trả HTTP 200 ở lần gọi /readyz, state A đã đồng bộ với B và 10 request qua edge không lỗi. DR operator ghi quyết định vào incident, thực hiện reverse failover theo thay đổi đã được IC phê duyệt, rồi lặp lại bước 4 và 6. Nếu bất kỳ kiểm tra nào thất bại, giữ traffic ở B và tiếp tục khắc phục A.

## Khôi phục sau drill / kiểm tra nhanh

Nếu A bị `netblock`, chạy `python3 chaos/kill_region.py restore --region a --backend bare`. Nếu dùng `--mode stop`, khởi động lại bare stack bằng `bash scripts/up_bare.sh`. Trước drill mới, phải có replication thành công; khi cần, dùng trình tự chuẩn trong `GUIDE.md` gồm ingest + replicate trước traffic/chaos. Log cần đính kèm postmortem: `reports/runbook-run.jsonl`, `reports/failover-events.jsonl`, `reports/health-events.jsonl`, `reports/drill-2-withdr.jsonl`, và output `measure_rto.py`.
