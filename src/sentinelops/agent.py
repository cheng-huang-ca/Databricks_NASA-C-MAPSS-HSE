"""The OSHA assistant as an MLflow ResponsesAgent for Model Serving, and a client that evaluates it.

The served agent runs `sentinelops.answers.Assistant` unchanged: the evaluated prompt, chat model,
256-dimension exact index, k and calibrated decline threshold. Serving endpoints can't read Delta
without a SQL warehouse, so the index and the document text are model artifacts. The question is
embedded and the answer generated through pay-per-token endpoints, which the logged model declares
as resources: the endpoint then gets short-lived credentials for exactly those two (automatic
authentication passthrough), and `WorkspaceClient()` picks them up. The agent is single-turn: it
answers the latest user message and ignores earlier turns.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Callable, Generator, Mapping

import mlflow
import numpy as np
from mlflow.entities import SpanType
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent

from sentinelops.answers import ERROR, MESSAGES, PROMPT_VERSION, Assistant, ChatClient, responses_output
from sentinelops.embeddings import ENDPOINT as EMBEDDING_ENDPOINT, format_query
from sentinelops.retrieval import ExactIndex

MODEL_NAME = "osha_assistant"
ARTIFACT_FILES = {"config": "config.json", "index_ids": "index_ids.npy", "index_vectors": "index_vectors.npy",
                  "documents": "documents.json"}
CONFIG_KEYS = ("chat_endpoint", "embedding_endpoint", "dimensions", "k", "min_score", "max_tokens",
               "reasoning_effort", "prompt_version")


def latest_user_text(items) -> str:
    """Text of the last user message in a Responses `input` list (dicts or pydantic items)."""
    for item in reversed(list(items or [])):
        item = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "".join(part.get("text") or "" for part in content or []
                           if part.get("type") in ("input_text", "output_text", "text"))
        if text.strip():
            return text.strip()
    raise ValueError("The request has no user message with text")


def save_artifacts(directory, index: ExactIndex, documents: Mapping[int, str], config: dict) -> dict[str, str]:
    """Write the index, each indexed report's text and the config; returns {artifact name: path}."""
    missing = set(CONFIG_KEYS) - set(config)
    if missing:
        raise ValueError(f"Config lacks {sorted(missing)}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {name: str(directory / file) for name, file in ARTIFACT_FILES.items()}
    np.save(paths["index_ids"], np.asarray(index.ids, dtype=np.int64))
    np.save(paths["index_vectors"], index.matrix)
    texts = {str(int(i)): documents[int(i)] for i in index.ids}
    Path(paths["documents"]).write_text(json.dumps(texts), encoding="utf-8")
    Path(paths["config"]).write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    return paths


def load_artifacts(paths: Mapping[str, str]) -> tuple[ExactIndex, dict[int, str], dict]:
    """Inverse of save_artifacts, refusing artifacts that don't match this code's prompt or shape."""
    index = ExactIndex(np.load(paths["index_ids"]), np.load(paths["index_vectors"]))
    documents = {int(i): text for i, text in json.loads(Path(paths["documents"]).read_text(encoding="utf-8")).items()}
    config = json.loads(Path(paths["config"]).read_text(encoding="utf-8"))
    if config["prompt_version"] != PROMPT_VERSION:
        raise ValueError(f"Artifacts were built for {config['prompt_version']}, this code is {PROMPT_VERSION}")
    if config["dimensions"] != index.dimensions or set(documents) != set(index.ids.tolist()):
        raise ValueError("Index dimensions or document IDs don't match the config")
    return index, documents, config


def _post_json(url: str, auth: Callable[[], dict], body: dict, *, post, sleep, timeout: float, retries: int,
               backoff_seconds: float, name: str) -> dict:
    """POST with the same visible retry rule as ChatClient: 429 and 5xx back off, anything else fails."""
    for attempt in range(retries + 1):
        response = post(url, headers=auth(), json=body, timeout=timeout)
        if response.status_code not in ChatClient.RETRY_STATUSES or attempt == retries:
            break
        delay = response.headers.get("Retry-After")
        sleep(float(delay) if delay and delay.isdigit() else backoff_seconds * 2 ** attempt)
    if response.status_code != 200:
        raise RuntimeError(f"{name} returned HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


class RestClient:
    """Base for the REST clients below; `auth()` returns request headers, `post` is injectable for tests."""

    def __init__(self, host: str, auth: Callable[[], dict], endpoint: str, *, timeout: float = 60, retries: int = 4,
                 backoff_seconds: float = 1.0, post=None, sleep=time.sleep):
        if post is None:
            import requests
            post = requests.post
        self.url = f"{host.rstrip('/')}/serving-endpoints/{endpoint}/invocations"
        self.endpoint, self.auth, self.post, self.sleep = endpoint, auth, post, sleep
        self.timeout, self.retries, self.backoff_seconds = timeout, retries, backoff_seconds

    @classmethod
    def from_workspace(cls, endpoint: str, **kwargs):
        from databricks.sdk import WorkspaceClient
        config = WorkspaceClient().config
        return cls(config.host, config.authenticate, endpoint, **kwargs)

    def _post(self, body: dict) -> dict:
        return _post_json(self.url, self.auth, body, post=self.post, sleep=self.sleep, timeout=self.timeout,
                          retries=self.retries, backoff_seconds=self.backoff_seconds, name=self.endpoint)


class EmbeddingClient(RestClient):
    """One query embedding per request (the workspace throttles by input count, not tokens)."""

    def __call__(self, text: str) -> np.ndarray:
        return np.asarray(self._post({"input": [text]})["data"][0]["embedding"], dtype=np.float32)


class EndpointClient(RestClient):
    """Asks a deployed ResponsesAgent one question; returns its JSON response."""

    def __call__(self, question: str) -> dict:
        return self._post({"input": [{"role": "user", "content": question}]})


class OshaAgent(ResponsesAgent):
    """Serves Assistant.answer. Built in load_context from the logged artifacts, or given one (tests)."""

    def __init__(self, assistant: Assistant | None = None):
        super().__init__()
        self.assistant = assistant

    def load_context(self, context):
        index, documents, config = load_artifacts(context.artifacts)
        chat = ChatClient.from_workspace(config["chat_endpoint"], max_tokens=config["max_tokens"],
                                         reasoning_effort=config["reasoning_effort"])
        embed = EmbeddingClient.from_workspace(config["embedding_endpoint"])
        self.assistant = Assistant(index, documents, embed_query=lambda question: embed(format_query(question)),
                                   chat=chat, min_score=config["min_score"], k=config["k"],
                                   model=config["chat_endpoint"])

    def _answer(self, request: ResponsesAgentRequest) -> tuple[dict, dict]:
        result = self.assistant.answer(latest_user_text(request.input))
        text = result["output"][0]["content"][0]["text"]
        return self.create_text_output_item(text=text, id=f"msg_{uuid.uuid4().hex}"), result["custom_outputs"]

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        item, custom_outputs = self._answer(request)
        return ResponsesAgentResponse(output=[item], custom_outputs=custom_outputs)

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        # The answer is checked before it's shown, so it can't stream token by token: one final event.
        item, custom_outputs = self._answer(request)
        yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)


class EndpointAssistant:
    """The deployed agent behind the evaluation harness's `answer(question)` interface.

    It returns the endpoint's answer and custom outputs unchanged. The endpoint's own trace stays
    on the serving side, so a local RETRIEVER span re-creates the reports it retrieved (text from
    Gold, by ID) for the groundedness judge, as the in-process Assistant's trace has them.
    """

    def __init__(self, query: Callable[[str], dict], documents: Mapping[int, str]):
        self.query, self.documents = query, documents

    @mlflow.trace(name="retrieve", span_type=SpanType.RETRIEVER)
    def retrieved(self, ids: list[int]) -> list[dict]:
        return [{"page_content": self.documents[int(i)],
                 "metadata": {"doc_uri": f"osha-sir:{int(i)}", "report_id": int(i)}} for i in ids]

    @mlflow.trace(name="osha_answer_endpoint", span_type=SpanType.CHAIN)
    def answer(self, question: str) -> dict:
        try:
            response = self.query(question)
            text = response["output"][-1]["content"][0]["text"]
        except Exception as error:  # The caller gets an error row, never unverified text.
            mlflow.update_current_trace(tags={"sentinelops.status": ERROR})
            return responses_output(MESSAGES[ERROR], {"status": ERROR, "error": str(error)[:300]})
        custom = response.get("custom_outputs") or {}
        self.retrieved(custom.get("retrieved_ids") or [])
        mlflow.update_current_trace(tags={"sentinelops.status": custom.get("status", ERROR)})
        return responses_output(text, custom)
