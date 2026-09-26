"""P2 验收测量（二）：驱动真实端到端管道（HTTP API → 真实 DB/浏览器/LLM）

流程：health → 创建项目 → 元素抓取 → Excel 导入 → 批量生成 → 执行 → 终态。
每个里程碑把中间结果写入 data/p2_acceptance/pipeline_state.json，可断点续跑。

用法：python scripts/p2_acceptance/run_pipeline.py [--base http://127.0.0.1:8011]
环境变量 DATABASE_URL 由启动脚本决定（本脚本不关心）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "p2_acceptance" / "pipeline_state.json"
EXCEL = ROOT / "data" / "p2_acceptance" / "cases_120.xlsx"


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def wait_health(base: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=5)
            if r.status_code == 200:
                print(f"[health] ok: {r.json()}")
                return
        except Exception:
            pass
        time.sleep(3)
    raise SystemExit(f"服务未就绪（{timeout}s 超时）: {base}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--skip-generate", action="store_true",
                    help="已有 batch 结果，跳过批量生成（用于续跑）")
    ap.add_argument("--skip-execute", action="store_true",
                    help="已有执行结果，跳过执行（用于续跑）")
    args = ap.parse_args()

    s = load_state()
    client = httpx.Client(base_url=args.base, timeout=60.0)
    wait_health(args.base)

    # ── 1) 创建项目（本地真实目标 + SSRF 放行 8081 端口）──
    if not s.get("project_id"):
        r = client.post("/api/v1/projects/", json={
            "name": "P2_ACCEPTANCE_LOCAL",
            "target_url": "http://localhost:8081",
            "test_path": "/",
            "browser_type": "chromium",
            "headless": 1,
            "platform": "web",
            "config_json": {"allowed_ports": [8081]},
        })
        d = r.json().get("data") or {}
        s["project_id"] = d.get("id")
        print(f"[project] created id={s.get('project_id')} resp={r.status_code}")
        save_state(s)
    pid = s["project_id"]

    # ── 2) 元素抓取（真实浏览器）──
    if not s.get("elements_count"):
        r = client.post(f"/api/v1/projects/{pid}/elements/crawl", json={"max_depth": 1}, timeout=300)
        if r.status_code != 200:
            print(f"[crawl] FAILED resp={r.status_code} body={r.text[:500]}")
            return 5
        d = r.json().get("data") or {}
        s["elements_count"] = d.get("crawled_count", 0)
        print(f"[crawl] count={s['elements_count']} resp={r.status_code}")
        save_state(s)

    # ── 3) Excel 导入（真实文件上传）──
    if not s.get("import_result"):
        with EXCEL.open("rb") as f:
            r = client.post(
                f"/api/v1/projects/{pid}/cases/import",
                files={"file": (EXCEL.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                timeout=120,
            )
        d = r.json().get("data") or {}
        s["import_result"] = d
        print(f"[import] {json.dumps(d, ensure_ascii=False)} resp={r.status_code}")
        save_state(s)
    imp = s["import_result"]

    # ── 4) 列出全部 case id（真实查询）──
    if not s.get("case_ids"):
        all_ids: list[int] = []
        page = 1
        while True:
            r = client.get(f"/api/v1/projects/{pid}/cases/", params={"page": page, "size": 100})
            d = r.json().get("data") or {}
            items = d.get("items") or []
            all_ids += [it["id"] for it in items]
            if page * 100 >= (d.get("total") or 0):
                break
            page += 1
        s["case_ids"] = all_ids
        print(f"[list] total_cases={len(all_ids)}")
        save_state(s)
    case_ids = s["case_ids"]

    # ── 5) 批量生成（真实 LLM）──
    batch = s.get("batch_result")
    if batch is None and not args.skip_generate:
        r = client.post(f"/api/v1/projects/{pid}/cases/generate-batch",
                        json={"case_ids": case_ids}, timeout=60)
        d = r.json().get("data") or {}
        batch_id = d.get("batch_id")
        s["batch_id"] = batch_id
        print(f"[batch] created batch_id={batch_id} total={d.get('total')}")
        save_state(s)
        # 轮询到 frozen
        t0 = time.monotonic()
        while time.monotonic() - t0 < 7200:
            r = client.get(f"/api/v1/projects/{pid}/generate-batch/{batch_id}/status", timeout=60)
            d = r.json().get("data") or {}
            if d.get("frozen") or d.get("status") in ("completed", "failed"):
                batch = d
                s["batch_result"] = batch
                save_state(s)
                print(f"[batch] frozen status={batch.get('status')} "
                      f"kpi_eligible={batch.get('kpi_eligible_count')} "
                      f"first_gen_valid={batch.get('first_gen_valid_count')} "
                      f"elapsed={int(time.monotonic()-t0)}s")
                break
            print(f"[batch] progress {d.get('progress_pct')}% "
                  f"success={d.get('success')} failed={d.get('failed')} skipped={d.get('skipped')} "
                  f"elapsed={int(time.monotonic()-t0)}s")
            time.sleep(10)
        else:
            print("[batch] 轮询超时")
            return 2
        if batch is None:
            print("[batch] 未获得 frozen 结果")
            return 2

    # ── 6) 创建执行（真实 Playwright）──
    # 仅执行【已生成有效代码】的 Case：无代码 Case 无法通过 Admission
    # （Run 快照 code_id 未确定 → 整批拒绝），属产品既定边界，如实记录。
    exec_ids = s.get("executable_case_ids")
    if exec_ids is None and s.get("batch_result"):
        exec_ids = [c["case_id"] for c in (s["batch_result"].get("cases") or [])
                    if c.get("status") == "success"]
        s["executable_case_ids"] = exec_ids
        s["generation_cohort"] = {
            "requested": len(case_ids),
            "executable": len(exec_ids),
            "not_executable": len(case_ids) - len(exec_ids),
        }
        save_state(s)
        print(f"[exec] executable_case_ids={len(exec_ids)} / requested={len(case_ids)}")
    exec_res = s.get("execution_result")
    if exec_res is None and not args.skip_execute:
        r = client.post(f"/api/v1/projects/{pid}/executions", json={
            "case_ids": exec_ids, "mode": "headless",
            "batch_name": "P2_ACCEPTANCE_EXEC",
        }, timeout=60)
        d = r.json().get("data") or {}
        eid = d.get("execution_id")
        s["execution_id"] = eid
        s["execution_create"] = d
        print(f"[exec] created execution_id={eid}")
        save_state(s)
        if not eid:
            print(f"[exec] 创建失败: {json.dumps(d, ensure_ascii=False)}")
            return 3
        # 轮询到终态
        TERMINAL = {"completed", "failed", "stopped", "interrupted"}
        t0 = time.monotonic()
        while time.monotonic() - t0 < 10800:
            r = client.get(f"/api/v1/executions/{eid}/status", timeout=60)
            d = r.json().get("data") or {}
            st = d.get("status")
            if st in TERMINAL:
                s["execution_result"] = d
                save_state(s)
                print(f"[exec] terminal status={st} "
                      f"passed={d.get('passed_cases')} failed={d.get('failed_cases')} "
                      f"elapsed={int(time.monotonic()-t0)}s")
                break
            print(f"[exec] status={st} progress={d.get('progress')} "
                  f"passed={d.get('passed_cases')} failed={d.get('failed_cases')} "
                  f"elapsed={int(time.monotonic()-t0)}s")
            time.sleep(15)
        else:
            print("[exec] 轮询超时")
            return 3

    print("[done] pipeline_state.json 已更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
