import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.errors import ModelStartupError
from app.services.model_registry import discover_artifacts, load_active_model
from conftest import make_artifact, scalar


def _load(db, settings):
    with Session(db) as s:
        return load_active_model(s, settings)


def test_single_artifact_is_registered_and_activated(db, make_settings):
    m = _load(db, make_settings())
    assert m.version == "test-model-a"
    assert scalar(db, "SELECT status FROM model_versions WHERE version='test-model-a'") == "active"
    assert scalar(db, "SELECT count(*) FROM model_versions") == 1


def test_restart_is_idempotent(db, make_settings):
    a, b = _load(db, make_settings()), _load(db, make_settings())
    assert a.model_version_id == b.model_version_id
    assert scalar(db, "SELECT count(*) FROM model_versions") == 1


def test_multiple_artifacts_without_explicit_choice_fails(db, make_settings, artifact_root):
    make_artifact(artifact_root, "test-model-b")
    with pytest.raises(ModelStartupError, match="ACTIVE_MODEL_VERSION"):
        _load(db, make_settings())
    assert scalar(db, "SELECT count(*) FROM model_versions") == 0  # nothing activated arbitrarily


def test_explicit_choice_selects_and_switches_active(db, make_settings, artifact_root):
    make_artifact(artifact_root, "test-model-b")
    _load(db, make_settings(active_model_version="test-model-a"))
    m = _load(db, make_settings(active_model_version="test-model-b"))
    assert m.version == "test-model-b"
    with db.connect() as c:
        rows = dict(c.execute(text("SELECT version, status FROM model_versions")).all())
    assert rows == {"test-model-a": "archived", "test-model-b": "active"}  # previous version preserved


def test_db_active_model_wins_when_several_artifacts_exist(db, make_settings, artifact_root):
    _load(db, make_settings())
    make_artifact(artifact_root, "test-model-b")  # a new artifact appears later
    assert _load(db, make_settings()).version == "test-model-a"


def test_explicit_unknown_version_fails(db, make_settings):
    with pytest.raises(ModelStartupError, match="does not match"):
        _load(db, make_settings(active_model_version="nope"))


def test_no_artifacts_fails_with_training_hint(db, make_settings, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ModelStartupError, match="pm_ml.training"):
        _load(db, make_settings(model_artifact_root=empty))


def test_missing_artifact_root_fails(db, make_settings, tmp_path):
    with pytest.raises(ModelStartupError):
        _load(db, make_settings(model_artifact_root=tmp_path / "does-not-exist"))


def test_db_active_but_artifact_removed_fails(db, make_settings, artifact_root):
    _load(db, make_settings())
    (artifact_root / "test-model-a" / "metadata.json").unlink()
    with pytest.raises(ModelStartupError, match="missing"):
        _load(db, make_settings())


def test_feature_order_mismatch_is_rejected(db, make_settings, artifact_root):
    p = artifact_root / "test-model-a" / "metadata.json"
    meta = json.loads(p.read_text())
    meta["feature_names"].reverse()
    p.write_text(json.dumps(meta))
    with pytest.raises(ModelStartupError, match="failed validation"):
        _load(db, make_settings())


def test_corrupt_model_file_is_rejected(db, make_settings, artifact_root):
    (artifact_root / "test-model-a" / "regressor.json").write_text("not a model")
    with pytest.raises(ModelStartupError, match="failed validation"):
        _load(db, make_settings())


def test_metadata_version_must_match_folder(db, make_settings, artifact_root):
    p = artifact_root / "test-model-a" / "metadata.json"
    meta = json.loads(p.read_text())
    meta["version"] = "something-else"
    p.write_text(json.dumps(meta))
    with pytest.raises(ModelStartupError, match="different version"):
        _load(db, make_settings())


def test_discover_ignores_folders_without_metadata(artifact_root):
    (artifact_root / "junk").mkdir()
    assert list(discover_artifacts(artifact_root)) == ["test-model-a"]
