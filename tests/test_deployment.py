"""Deployment safety checks use a fake Hub; no credentials or network calls."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.deploy_space import (
    MANIFEST, SOURCE_FILES, SPACE_ID, DeploymentFailure, build_bundle, deploy_bundle, main, validate_bundle,
)


SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


class FakeHub:
    def __init__(self):
        self.info = SimpleNamespace(id=SPACE_ID, private=False, sdk="docker", sha="b" * 40)
        self.runtime = SimpleNamespace(hardware="cpu-basic", requested_hardware="cpu-basic")
        self.uploads = []

    def space_info(self, **kwargs):
        assert kwargs == {"repo_id": SPACE_ID, "revision": "main"}
        return self.info

    def get_space_runtime(self, **kwargs):
        assert kwargs == {"repo_id": SPACE_ID}
        return self.runtime

    def upload_folder(self, **kwargs):
        self.uploads.append(kwargs)


@pytest.fixture
def bundle(tmp_path):
    output = tmp_path / "bundle"
    build_bundle(ROOT, output, SHA)
    return output


def test_bundle_excludes_unrelated_files(tmp_path):
    source = tmp_path / "source"
    build_bundle(ROOT, source, SHA)
    for name in (".env", "WORKING_METHOD.md", "private_notes.md", "tests/test_private.py",
                 "src/service_analysis/notes.py", ".github/workflows/ci.yml"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic sentinel - must not be published")
    output = tmp_path / "output"
    build_bundle(source, output, SHA)
    validate_bundle(output, SHA)
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == {*SOURCE_FILES, MANIFEST}


def test_only_expected_space_and_validated_files_uploaded(bundle):
    api = FakeHub()
    deploy_bundle(api, bundle, SHA)
    assert len(api.uploads) == 1
    upload = api.uploads[0]
    assert upload["repo_id"] == SPACE_ID
    assert upload["repo_type"] == "space"
    assert upload["revision"] == "main"
    assert upload["parent_commit"] == api.info.sha
    assert set(upload["allow_patterns"]) == {*SOURCE_FILES, MANIFEST}
    assert SHA in upload["commit_message"]


@pytest.mark.parametrize("hardware,requested", [
    ("cpu-upgrade", "cpu-upgrade"), ("cpu-basic", "t4-small"),
    ("t4-small", "cpu-basic"), (None, None),
])
def test_paid_or_unknown_hardware_blocks_upload(bundle, hardware, requested):
    api = FakeHub()
    api.runtime = SimpleNamespace(hardware=hardware, requested_hardware=requested)
    with pytest.raises(ValueError, match="CPU Basic"):
        deploy_bundle(api, bundle, SHA)
    assert api.uploads == []


def test_building_cpu_basic_space_allowed(bundle):
    api = FakeHub()
    api.runtime.hardware = None
    deploy_bundle(api, bundle, SHA)
    assert len(api.uploads) == 1


@pytest.mark.parametrize("field,value", [
    ("sdk", "gradio"), ("private", True), ("id", "other/space"), ("sha", None),
])
def test_wrong_space_configuration_blocks_upload(bundle, field, value):
    api = FakeHub()
    setattr(api.info, field, value)
    with pytest.raises(ValueError):
        deploy_bundle(api, bundle, SHA)
    assert api.uploads == []


def test_wrong_target_fails_before_api(bundle):
    with pytest.raises(ValueError, match="target"):
        deploy_bundle(object(), bundle, SHA, "other/space")


@pytest.mark.parametrize("mutation", ["extra", "modified", "missing", "wrong_sha", "symlink"])
def test_corrupted_bundle_fails_before_api(bundle, mutation):
    sha = SHA
    if mutation == "extra":
        (bundle / "private_notes.txt").write_text("synthetic sentinel")
    elif mutation == "modified":
        (bundle / "app.py").write_text("changed")
    elif mutation == "missing":
        (bundle / "app.py").unlink()
    elif mutation == "wrong_sha":
        sha = "c" * 40
    elif mutation == "symlink":
        (bundle / "app.py").unlink()
        (bundle / "app.py").symlink_to(ROOT / "app.py")
    with pytest.raises(ValueError):
        deploy_bundle(object(), bundle, sha)


@pytest.mark.parametrize("sha", ["", "main", "a" * 39, "A" * 40, "a" * 40 + "\n"])
def test_invalid_commit_does_not_write_bundle(tmp_path, sha):
    output = tmp_path / "bundle"
    with pytest.raises(ValueError):
        build_bundle(ROOT, output, sha)
    assert not output.exists()


def test_source_symlink_refused_before_output(tmp_path):
    source = tmp_path / "source"
    build_bundle(ROOT, source, SHA)
    (source / "app.py").unlink()
    (source / "app.py").symlink_to(ROOT / "app.py")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="Symlinks"):
        build_bundle(source, output, SHA)
    assert not output.exists()


def test_missing_token_has_no_network_or_provider_dependency(monkeypatch):
    monkeypatch.delenv("HF_DEPLOY_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="not configured"):
        main()


def test_existing_output_not_overwritten(bundle):
    with pytest.raises(FileExistsError):
        build_bundle(ROOT, bundle, SHA)


@pytest.mark.parametrize("operation,stage", [
    ("space_info", "SPACE_LOOKUP_FAILED"),
    ("get_space_runtime", "HARDWARE_LOOKUP_FAILED"),
    ("upload_folder", "UPLOAD_FAILED"),
])
@pytest.mark.parametrize("status,suffix", [(403, "_HTTP_403"), (404, "_HTTP_404"), (None, "_RUNTIMEERROR"), ("private text", "_RUNTIMEERROR")])
def test_provider_failures_publish_only_safe_stage_and_http_code(bundle, monkeypatch, operation, stage, status, suffix):
    api = FakeHub()

    def fail(**kwargs):
        error = RuntimeError("synthetic credential sentinel must remain private")
        error.response = SimpleNamespace(status_code=status)
        raise error

    monkeypatch.setattr(api, operation, fail)
    with pytest.raises(DeploymentFailure) as caught:
        deploy_bundle(api, bundle, SHA)
    assert str(caught.value) == stage + suffix
    assert caught.value.__suppress_context__
    assert api.uploads == []
