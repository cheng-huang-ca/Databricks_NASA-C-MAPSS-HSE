"""Genie evaluation from this machine, as the signed-in user (Genie's LLM use is free for users;
the SQL runs on the space's warehouse, which bills while up).

  python scripts/genie_eval.py ask <space_id> <warehouse_id> <catalog> <out.json>
      References first (each benchmark's SQL on the warehouse), then every benchmark question asked
      once in a new conversation, compared with sentinelops.genie_eval.compare. Run it before the
      benchmarks are deployed, so the questions are held out.
  python scripts/genie_eval.py benchmarks <space_id> <out.json>
      Starts a benchmark eval run on the deployed space, waits, and appends its results to out.json.
"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import yaml  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

from sentinelops import genie_eval  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def statement_rows(w, warehouse_id: str, sql: str) -> tuple[list, list]:
    response = w.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=sql, wait_timeout="50s")
    while response.status.state.value in ("PENDING", "RUNNING"):
        time.sleep(3)
        response = w.statement_execution.get_statement(response.statement_id)
    if response.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"Reference SQL failed: {response.status.as_dict()}")
    columns = [c.name for c in response.manifest.schema.columns]
    return columns, (response.result.data_array or []) if response.result else []


def ask(w, space_id: str, question: str) -> dict:
    started = time.monotonic()
    message = w.genie.start_conversation_and_wait(space_id, question)
    out = {"status": message.status.value if message.status else None, "text": None, "sql": None,
           "columns": None, "rows": None}
    for attachment in message.attachments or []:
        if attachment.text and attachment.text.content:
            out["text"] = attachment.text.content
        if attachment.query:
            out["sql"] = attachment.query.query
            result = w.genie.get_message_attachment_query_result(space_id, message.conversation_id, message.id,
                                                                 attachment.attachment_id).statement_response
            if result and result.manifest:
                out["columns"] = [c.name for c in result.manifest.schema.columns]
                out["rows"] = (result.result.data_array or []) if result.result else []
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def main():
    w = WorkspaceClient()
    mode = sys.argv[1]
    if mode == "ask":
        space_id, warehouse_id, catalog, out_path = sys.argv[2:6]
        items = genie_eval.benchmarks(yaml.safe_load((ROOT / "resources/analytics.yml").read_text(encoding="utf-8")), catalog)
        references = {}
        for item in items:  # Fixed before any question is asked.
            if item["sql"] not in references:
                references[item["sql"]] = statement_rows(w, warehouse_id, item["sql"])
        results = []
        for item in items:
            columns, rows = references[item["sql"]]
            try:
                genie = ask(w, space_id, item["question"])
            except Exception as error:  # Record and continue; one failure shouldn't hide the rest.
                genie = {"error": str(error)[:500]}
            verdict = genie_eval.compare(rows, genie.get("rows"))
            results.append({"id": item["id"], "question": item["question"], "reference_columns": columns,
                            "reference_rows": rows, "genie": genie, **verdict})
            print(json.dumps({"id": item["id"], "verdict": verdict["verdict"], "seconds": genie.get("seconds")}), flush=True)
            time.sleep(5)  # Stay well inside Genie's per-workspace question rate.
        summary = {"questions": len(results), "match": sum(r["verdict"] == "match" for r in results),
                   "mismatch": sum(r["verdict"] == "mismatch" for r in results),
                   "no_result": sum(r["verdict"] == "no_result" for r in results)}
        Path(out_path).write_bytes((json.dumps({"space": space_id, "method": "Conversation API, one new conversation "
                                                "per question, asked once; references run first on the warehouse",
                                                "summary": summary, "results": results}, indent=1) + "\n").encode())
        print(json.dumps(summary))
    elif mode == "benchmarks":
        space_id, out_path = sys.argv[2:4]
        started = time.monotonic()
        run_id = w.genie.genie_create_eval_run(space_id).eval_run_id
        while True:
            run = w.genie.genie_get_eval_run(space_id, run_id)
            if run.eval_run_status and run.eval_run_status.value not in ("NOT_STARTED", "RUNNING"):
                break
            time.sleep(10)
        results, token = [], None
        while True:
            page = w.genie.genie_list_eval_results(space_id, run_id, page_token=token)
            results += page.eval_results or []
            token = page.next_page_token
            if not token:
                break
        details = []
        for result in results:
            detail = w.genie.genie_get_eval_result_details(space_id, run_id, result.result_id)
            details.append({"benchmark_question_id": result.benchmark_question_id, "question": result.question,
                            "assessment": detail.assessment.value if detail.assessment else None,
                            "assessment_reasons": [str(r.value if hasattr(r, "value") else r) for r in detail.assessment_reasons or []],
                            "actual_response": detail.as_dict().get("actual_response")})
        path = Path(out_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["benchmark_run"] = {"eval_run": run.as_dict(), "seconds": round(time.monotonic() - started, 1),
                                "results": sorted(details, key=lambda d: d["benchmark_question_id"] or "")}
        path.write_bytes((json.dumps(doc, indent=1) + "\n").encode())
        print(json.dumps({k: v for k, v in run.as_dict().items() if k.startswith("num_") or k == "eval_run_status"}))
        for d in doc["benchmark_run"]["results"]:
            print(d["benchmark_question_id"], d["assessment"], d["assessment_reasons"])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
