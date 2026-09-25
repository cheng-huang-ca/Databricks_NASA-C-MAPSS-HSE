import re
from pathlib import Path
from types import SimpleNamespace

import mlflow
import numpy as np
import pytest
import yaml
from mlflow.types.responses import ResponsesAgentRequest

from sentinelops import agent
from sentinelops.agent import (ARTIFACT_FILES, EmbeddingClient, EndpointAssistant, EndpointClient, OshaAgent,
                               latest_user_text, load_artifacts, save_artifacts)
from sentinelops.answers import ANSWERED, ERROR, PROMPT_VERSION, Assistant
from sentinelops.retrieval import ExactIndex

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {"chat_endpoint": "chat", "embedding_endpoint": "embed", "dimensions": 4, "k": 2, "min_score": 0.5,
          "max_tokens": 64, "reasoning_effort": "low", "prompt_version": PROMPT_VERSION}
DOCUMENTS = {10: "doc ten", 11: "doc eleven", 12: "doc twelve"}


@pytest.fixture(scope="module", autouse=True)
def local_tracking(tmp_path_factory):
    """Traces go to a throwaway store; the experiment's artifacts stay out of the working tree."""
    root = tmp_path_factory.mktemp("mlflow")
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{(root / 'mlflow.db').as_posix()}")
    mlflow.set_experiment(experiment_id=mlflow.create_experiment("agent", artifact_location=root.as_uri()))
    yield
    mlflow.set_tracking_uri(previous)


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code, self.body, self.headers, self.text = status_code, body, headers or {}, str(body)

    def json(self):
        return self.body


def reply(text):
    return {"text": text, "reasoning": "", "finish_reason": "stop", "input_tokens": 100, "output_tokens": 20,
            "attempts": 1}


def index():
    """Three orthogonal 4-d documents."""
    return ExactIndex(np.array([10, 11, 12]), np.eye(4, dtype=np.float32)[:3])


def assistant(text):
    """Every question embeds towards document 11 (score 1.0) and the model replies `text`."""
    return Assistant(index(), DOCUMENTS, embed_query=lambda question: np.array([0, 2, 0, 0], dtype=np.float32),
                     chat=lambda messages: reply(text), min_score=0.5, k=2)


def test_latest_user_text_takes_the_last_user_message():
    class Item:  # pydantic-like request items
        def __init__(self, **fields):
            self.fields = fields

        def model_dump(self):
            return self.fields

    items = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"},
             Item(role="user", content=[{"type": "input_text", "text": " How do "},
                                        {"type": "input_text", "text": "falls happen? "}])]
    assert latest_user_text(items) == "How do falls happen?"
    assert latest_user_text([{"role": "user", "content": "only"}, {"role": "assistant", "content": "x"}]) == "only"
    for bad in ([{"role": "assistant", "content": "x"}], [{"role": "user", "content": "  "}], []):
        with pytest.raises(ValueError):
            latest_user_text(bad)


def test_artifacts_round_trip_and_refuse_mismatches(tmp_path):
    paths = save_artifacts(tmp_path / "ok", index(), {**DOCUMENTS, 99: "not indexed"}, CONFIG)
    assert set(paths) == set(ARTIFACT_FILES)
    loaded, texts, config = load_artifacts(paths)
    assert texts == DOCUMENTS and config == CONFIG  # only indexed reports are packaged
    assert loaded.ids.tolist() == [10, 11, 12] and np.array_equal(loaded.matrix, index().matrix)
    with pytest.raises(ValueError, match="built for old"):
        load_artifacts(save_artifacts(tmp_path / "stale", index(), DOCUMENTS, {**CONFIG, "prompt_version": "old"}))
    with pytest.raises(ValueError, match="dimensions"):
        load_artifacts(save_artifacts(tmp_path / "wide", index(), DOCUMENTS, {**CONFIG, "dimensions": 8}))
    with pytest.raises(ValueError, match="lacks"):
        save_artifacts(tmp_path / "partial", index(), DOCUMENTS, {"k": 2})


def test_embedding_client_retries_throttling_and_returns_one_vector():
    responses = [FakeResponse(429, headers={"Retry-After": "2"}), FakeResponse(200, {"data": [{"embedding": [0.6, 0.8]}]})]
    calls, sleeps = [], []

    def post(url, headers, json, timeout):
        calls.append((url, json))
        return responses.pop(0)

    client = EmbeddingClient("https://host/", lambda: {"Authorization": "Bearer x"}, "embed", post=post,
                             sleep=sleeps.append)
    vector = client("Instruct: q")
    assert vector.dtype == np.float32 and vector.tolist() == pytest.approx([0.6, 0.8])
    assert calls == [("https://host/serving-endpoints/embed/invocations", {"input": ["Instruct: q"]})] * 2
    assert sleeps == [2.0]


def test_endpoint_client_asks_one_question_and_fails_fast_on_client_errors():
    calls = []

    def post(url, headers, json, timeout):
        calls.append(json)
        return FakeResponse(200, {"output": []})

    assert EndpointClient("https://host", dict, "agent", post=post)("Why?") == {"output": []}
    assert calls == [{"input": [{"role": "user", "content": "Why?"}]}]
    client = EndpointClient("https://host", dict, "agent", post=lambda *a, **k: FakeResponse(403, "denied"), sleep=None)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        client("q")


def test_agent_predict_and_stream_return_the_checked_answer():
    served = OshaAgent(assistant("Workers were caught [11]."))
    request = ResponsesAgentRequest(input=[{"role": "user", "content": "How?"}])
    body = served.predict(request).model_dump()
    assert body["output"][0]["content"][0]["text"] == "Workers were caught [11]."
    assert body["custom_outputs"]["status"] == ANSWERED and body["custom_outputs"]["retrieved_ids"][0] == 11
    events = list(served.predict_stream(request))
    assert len(events) == 1 and events[0].type == "response.output_item.done"
    assert events[0].model_dump()["item"]["content"][0]["text"] == "Workers were caught [11]."


def test_load_context_builds_the_evaluated_assistant_from_artifacts(tmp_path, monkeypatch):
    embedded = []

    def embed(text):
        embedded.append(text)
        return np.array([0, 1, 0, 0], dtype=np.float32)

    monkeypatch.setattr(agent.ChatClient, "from_workspace",
                        classmethod(lambda cls, endpoint, **kwargs: lambda messages: reply("Doc [11].")))
    monkeypatch.setattr(agent.EmbeddingClient, "from_workspace", classmethod(lambda cls, endpoint, **kwargs: embed))
    served = OshaAgent()
    served.load_context(SimpleNamespace(artifacts=save_artifacts(tmp_path, index(), DOCUMENTS, CONFIG)))
    custom = served.predict(ResponsesAgentRequest(input=[{"role": "user", "content": "q"}])).model_dump()["custom_outputs"]
    assert custom["status"] == ANSWERED and custom["min_score"] == 0.5 and custom["retrieved_ids"][0] == 11
    assert embedded == ["Instruct: Given a workplace safety question, retrieve OSHA severe injury reports relevant "
                        "to it\nQuery:q"]


def test_endpoint_assistant_returns_the_endpoints_answer_and_traces_its_reports():
    response = {"output": [{"type": "message", "id": "m", "role": "assistant",
                            "content": [{"type": "output_text", "text": "Caught [11]."}]}],
                "custom_outputs": {"status": ANSWERED, "retrieved_ids": [11, 10], "top1_score": 0.9}}
    out = EndpointAssistant(lambda question: response, DOCUMENTS).answer("q")
    assert out["output"][0]["content"][0]["text"] == "Caught [11]." and out["custom_outputs"] == response["custom_outputs"]
    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id())
    spans = {span.name: span for span in trace.data.spans}
    assert spans["retrieve"].span_type == "RETRIEVER"
    assert [doc["page_content"] for doc in spans["retrieve"].outputs] == ["doc eleven", "doc ten"]
    assert trace.info.tags["sentinelops.status"] == ANSWERED

    def unavailable(question):
        raise RuntimeError("sentinelops-osha-agent returned HTTP 503")
    assert EndpointAssistant(unavailable, DOCUMENTS).answer("q")["custom_outputs"]["status"] == ERROR


def test_agent_jobs_are_dev_only_and_serve_the_evaluated_configuration():
    config = yaml.safe_load((ROOT / "resources" / "agent.yml").read_text())
    assert set(config) == {"targets"} and set(config["targets"]) == {"dev"}
    jobs = config["targets"]["dev"]["resources"]["jobs"]
    assert set(jobs) == {"osha_agent_log", "osha_agent_deploy", "osha_agent_eval"}
    evaluated = yaml.safe_load((ROOT / "resources" / "osha.yml").read_text())["resources"]["jobs"]["osha_answer_eval"]

    def params(job):
        values = job["tasks"][0]["spark_python_task"]["parameters"]
        return dict(zip(values[::2], values[1::2]))

    same = ("--embedding-endpoint", "--chat-endpoint", "--dimensions", "--k", "--max-tokens", "--reasoning-effort")
    assert {key: params(jobs["osha_agent_log"])[key] for key in same} == {key: params(evaluated)[key] for key in same}
    for key in ("--question-sets", "--judge-endpoint", "--workers"):
        assert params(jobs["osha_agent_eval"])[key] == params(evaluated)[key]
    assert params(jobs["osha_agent_deploy"])["--endpoint-name"] == params(jobs["osha_agent_eval"])["--endpoint-name"]
    assert "databricks-agents==1.12.0" in jobs["osha_agent_deploy"]["environments"][0]["spec"]["dependencies"]
    # agents.deploy's flag is `scale_to_zero`; it silently ignored `scale_to_zero_enabled` (run 1016924296649877).
    (call,) = re.findall(r"agents\.deploy\([^)]*\)", (ROOT / "jobs" / "deploy_osha_agent.py").read_text())
    assert "scale_to_zero=True" in call and "scale_to_zero_enabled" not in call
    assert (ROOT / "src" / "sentinelops" / "agent_model.py").exists()
