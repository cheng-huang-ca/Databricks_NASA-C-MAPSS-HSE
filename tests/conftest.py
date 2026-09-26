from pathlib import Path
import sys
import types
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def load_pipeline():
    """Execute a pipeline source file with stand-ins for Spark and dbutils; returns its globals.
    Constants can then be compared with the Python side, and dataset functions called.
    `declared` lists every decorator call as (decorator, args, kwargs, function name)."""
    def load(relative_path: str, conf: dict) -> dict:
        fake = {name: types.ModuleType(name) for name in ("pyspark", "pyspark.pipelines", "pyspark.sql")}
        fake["pyspark"].pipelines = fake["pyspark.pipelines"]
        fake["pyspark.sql"].functions, fake["pyspark.sql"].Window = mock.MagicMock(), mock.MagicMock()
        declared = []

        def recorder(kind):
            def decorator(*args, **kwargs):
                def register(function):
                    declared.append((kind, args, kwargs, function.__name__))
                    return function
                return register
            return decorator

        for kind in ("table", "materialized_view", "temporary_view", "append_flow", "expect_all_or_drop",
                     "expect_all_or_fail"):
            setattr(fake["pyspark.pipelines"], kind, recorder(kind))
        spark = mock.MagicMock()
        spark.conf.get.side_effect = lambda key, default=None: conf.get(key, default)
        dbutils = mock.MagicMock()
        dbutils.secrets.get.return_value = "listen-key"
        namespace = {"spark": spark, "dbutils": dbutils}
        with mock.patch.dict(sys.modules, fake):
            exec(compile((ROOT / relative_path).read_text(encoding="utf-8"), relative_path, "exec"), namespace)
        namespace["declared"] = declared
        return namespace
    return load
