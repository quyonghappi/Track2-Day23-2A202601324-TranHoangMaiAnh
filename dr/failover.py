"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """Append one timestamped failover event and return it."""
    record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(json.dumps(record))
    return record


def state_of(region: str) -> dict:
    response = httpx.get(f"{URL[region]}/v1/state", timeout=2.0)
    response.raise_for_status()
    return response.json()


def failover(target: str, backend: str, wait: float) -> dict:
    """Restore state, warm the target, then and only then cut DNS over."""
    if wait <= 0:
        raise ValueError("wait must be positive")
    primary = "a" if target == "b" else "b"
    try:
        emit(step="1_verify_target", target=target, state=state_of(target))
    except httpx.HTTPError as exc:
        emit(step="1_verify_target", target=target, state=None, error=str(exc))
    try:
        restored = snapshot.get(target, backend)
        rpo = snapshot.rpo(pathlib.Path(f"state/region-{primary}/vectors.sqlite"),
                           pathlib.Path(f"state/region-{target}/vectors.sqlite"))
    except Exception as exc:
        emit(step="2_restore_snapshot", target=target, ok=False, error=str(exc),
             rpo_seconds=None, docs_lost=None, embed_model_version=None)
        return {"ok": False, "target": target, "error": f"restore_failed: {exc}"}
    emit(step="2_restore_snapshot", target=target, ok=True,
         rpo_seconds=rpo["rpo_seconds"], docs_lost=rpo["docs_lost"],
         embed_model_version=restored.get("embed_model_version"), restore=restored)
    pool_file = pathlib.Path(f"state/region-{target}/pool_state")
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text("full\n", encoding="utf-8")
    emit(step="3_scale_pool", target=target, pool_state="full")
    started = time.monotonic()
    last_reason = "timeout"
    while time.monotonic() - started < wait:
        try:
            response = httpx.get(f"{URL[target]}/readyz", timeout=min(2.0, wait))
            if response.status_code == 200:
                waited = round(time.monotonic() - started, 2)
                emit(step="4_wait_ready", target=target, ok=True, waited_s=waited)
                break
            last_reason = f"readyz_{response.status_code}"
        except httpx.HTTPError as exc:
            last_reason = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    else:
        waited = round(time.monotonic() - started, 2)
        emit(step="4_wait_ready", target=target, ok=False, waited_s=waited, error=last_reason)
        return {"ok": False, "target": target, "error": "target_not_ready", "waited_s": waited}
    pathlib.Path("edge/active_region").write_text(target + "\n", encoding="utf-8")
    emit(step="5_dns_cutover", target=target, ok=True)
    return {"ok": True, "target": target, "restored": restored, "rpo": rpo, "waited_s": waited}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
