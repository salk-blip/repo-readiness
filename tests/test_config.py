import os

from app.config import load_environment


def test_dotenv_loads_model_and_key(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    path = tmp_path / ".env"
    path.write_text('\ufeffAI_API_KEY="synthetic-test-key"\nAI_MODEL=test-model\n', encoding="utf-8")
    load_environment(path)
    assert os.environ["AI_API_KEY"] == "synthetic-test-key"
    assert os.environ["AI_MODEL"] == "test-model"
    monkeypatch.delenv("AI_API_KEY")
    monkeypatch.delenv("AI_MODEL")


def test_explicit_environment_takes_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MODEL", "deployment-model")
    path = tmp_path / ".env"
    path.write_text("AI_MODEL=file-model\n", encoding="utf-8")
    load_environment(path)
    assert os.environ["AI_MODEL"] == "deployment-model"


def test_missing_env_is_optional(tmp_path):
    load_environment(tmp_path / "missing.env")
