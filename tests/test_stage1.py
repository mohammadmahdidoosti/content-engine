import json

import pytest
from pydantic import ValidationError

from app.brain.llm import extract_json
from app.brain.mock_data import MOCK
from app.brain.prompts import user_prompt
from app.brain.schemas import SCHEMAS, Infographic, VideoScript


@pytest.mark.parametrize("ctype", list(SCHEMAS))
def test_mock_samples_are_valid(ctype):
    SCHEMAS[ctype].model_validate(MOCK[ctype])


def test_hashtags_cleaned():
    d = dict(MOCK["text"], hashtags=["کاراوان", "#کاراوان", "سفر با کاراوان"])
    post = SCHEMAS["text"].model_validate(d)
    assert post.hashtags == ["#کاراوان", "#سفر_با_کاراوان"]


def test_unknown_icon_falls_back():
    d = json.loads(json.dumps(MOCK["infographic"]))
    d["items"][0]["icon"] = "spaceship"
    assert Infographic.model_validate(d).items[0].icon == "check"


def test_video_duration_computed_and_reindexed():
    d = json.loads(json.dumps(MOCK["video"]))
    for s in d["scenes"]:
        s["index"] = 99
    v = VideoScript.model_validate(d)
    assert v.duration_sec == sum(s["duration_sec"] for s in d["scenes"])
    assert [s.index for s in v.scenes] == list(range(1, len(d["scenes"]) + 1))


def test_video_too_long_rejected():
    d = json.loads(json.dumps(MOCK["video"]))
    for s in d["scenes"]:
        s["duration_sec"] = 12
    with pytest.raises(ValidationError):
        VideoScript.model_validate(d)


def test_extract_json_handles_fences_and_noise():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": 2} thanks') == {"a": 2}


@pytest.mark.parametrize("ctype", list(SCHEMAS))
def test_prompt_contains_schema(ctype):
    p = user_prompt("تست", ctype)
    assert "اسکیمای JSON" in p and '"properties"' in p


def test_job_flow_with_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from app import config
    from app.db import session
    config.get_settings.cache_clear()
    session._engine = session._Session = None

    from app.brain.generate import create_and_run_job
    from app.db.models import JobStatus

    session.init_db()
    for ctype in SCHEMAS:
        job = create_and_run_job("تست", ctype, mock=True)
        assert job.status == JobStatus.generated, job.error
        assert job.output and job.llm_model == "mock"
