"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import statistics
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Write one timestamped runbook event."""
    record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
              "step": n, "name": name, **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(json.dumps(record))
    return record


def confirm(auto: bool, msg: str) -> bool:
    return True if auto else input(f"{msg} [y/N] ").strip().lower() in {"y", "yes"}


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    started = time.time()
    probes = []
    for region in (primary, target):
        try:
            response = httpx.get(f"{URL[region]}/readyz", timeout=2.0)
            probes.append({"region": region, "ready": response.status_code == 200})
        except httpx.HTTPError as exc:
            probes.append({"region": region, "ready": False, "error": str(exc)})
    step(1, "xac_nhan_outage", probes=probes)
    if not confirm(auto, f"Fail over from region-{primary} to region-{target}?"):
        step(2, "thong_bao_incident", announced=False, reason="operator_declined")
        return {"ok": False, "error": "operator_declined"}
    step(2, "thong_bao_incident", announced=True, primary=primary, target=target)
    result = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_ok=result.get("ok"), failover=result)
    step(4, "verify_state_replica", target=target, restored=result.get("restored"), rpo=result.get("rpo"))
    step(5, "dns_cutover", target=target, ok=result.get("ok"))
    if not result.get("ok"):
        step(7, "post_incident", ok=False, elapsed_s=round(time.time() - started, 2),
             rto_command="python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl")
        return result
    latencies, failures = [], 0
    for _ in range(10):
        begun = time.perf_counter()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", timeout=5.0)
            failures += response.status_code != 200
        except httpx.HTTPError:
            failures += 1
        latencies.append((time.perf_counter() - begun) * 1000)
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
    step(6, "verify_golden_signals", requests=10, error_rate=failures / 10,
         p95_latency_ms=round(p95, 2))
    step(7, "post_incident", ok=True, elapsed_s=round(time.time() - started, 2),
         rto_command="python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl")
    return {**result, "golden_signals": {"requests": 10, "failures": failures,
                                           "p95_latency_ms": round(p95, 2)}}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
