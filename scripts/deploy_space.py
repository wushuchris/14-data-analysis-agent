"""Publish an allowlisted, tested bundle to an existing CPU Basic Docker Space."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


SPACE_ID = "FlyingNunchucks/14-data-analysis-agent"
SOURCE_FILES = (
    "README.md", "LICENSE", "Dockerfile", ".dockerignore", "requirements.txt", "app.py",
    "src/service_analysis/__init__.py",
    "src/service_analysis/analysis.py",
    "src/service_analysis/charts.py",
    "src/service_analysis/decomposition.py",
    "src/service_analysis/demo_data.py",
    "src/service_analysis/execution.py",
    "src/service_analysis/findings.py",
    "src/service_analysis/model_adapter.py",
    "src/service_analysis/planning.py",
    "src/service_analysis/quality.py",
    "src/service_analysis/reporting.py",
    "src/service_analysis/schemas.py",
    "src/service_analysis/workload.py",
)
MANIFEST = "deployment.json"


def validate_sha(sha: str) -> None:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid source commit")


def checked_file(root: Path, relative: str) -> Path:
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
        raise ValueError("Symlinks are not permitted in deployment files")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Missing or unsafe deployment file")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_bundle(root: Path, output: Path, source_sha: str) -> None:
    validate_sha(source_sha)
    # Validate all sources before writing anything. Output must be a new directory.
    sources = {name: checked_file(root, name) for name in SOURCE_FILES}
    output.mkdir(parents=True, exist_ok=False)
    for name, source in sources.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    manifest = {"source_commit": source_sha,
                "files": {name: digest(output / name) for name in SOURCE_FILES}}
    (output / MANIFEST).write_text(json.dumps(manifest, sort_keys=True) + "\n")


def validate_bundle(bundle: Path, source_sha: str) -> None:
    validate_sha(source_sha)
    entries = list(bundle.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError("Symlinks are not permitted in deployment bundles")
    actual = {path.relative_to(bundle).as_posix() for path in entries if path.is_file()}
    if actual != {*SOURCE_FILES, MANIFEST}:
        raise ValueError("Deployment bundle file list mismatch")
    manifest = json.loads(checked_file(bundle, MANIFEST).read_text())
    expected = {"source_commit": source_sha,
                "files": {name: digest(checked_file(bundle, name)) for name in SOURCE_FILES}}
    if manifest != expected:
        raise ValueError("Deployment bundle provenance mismatch")


class DeploymentFailure(ValueError):
    """Application-owned stage code; provider messages never reach public logs."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def hub_call(stage: str, operation, **kwargs):
    try:
        return operation(**kwargs)
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        suffix = f"_HTTP_{status}" if type(status) is int and 100 <= status <= 599 else ""
        raise DeploymentFailure(stage + suffix) from None


def deploy_bundle(api, bundle: Path, source_sha: str, repo_id: str = SPACE_ID) -> None:
    if repo_id != SPACE_ID:
        raise ValueError("Unexpected deployment target")
    validate_bundle(bundle, source_sha)
    # Read an existing Space only. Never create repositories, change hardware,
    # configure storage, set secrets, or make inference calls.
    info = hub_call("SPACE_LOOKUP_FAILED", api.space_info, repo_id=repo_id, revision="main")
    if info.id != SPACE_ID or info.private or info.sdk != "docker" or not info.sha:
        raise ValueError("Expected an existing public Docker Space")
    runtime = hub_call("HARDWARE_LOOKUP_FAILED", api.get_space_runtime, repo_id=repo_id)
    hardware = (runtime.hardware, runtime.requested_hardware)
    if any(value not in (None, "cpu-basic") for value in hardware) or "cpu-basic" not in hardware:
        raise ValueError("Expected CPU Basic hardware")
    hub_call("UPLOAD_FAILED", api.upload_folder,
        repo_id=repo_id, repo_type="space", revision="main",
        parent_commit=info.sha, folder_path=str(bundle),
        allow_patterns=[*SOURCE_FILES, MANIFEST],
        # Mirror only this dedicated demo target. Hub preserves .gitattributes.
        delete_patterns="*",
        commit_message=f"Deploy tested GitHub commit {source_sha}",
    )


def main() -> None:
    token = os.environ.get("HF_DEPLOY_TOKEN", "").strip()
    if not token:
        raise SystemExit("HF_DEPLOY_TOKEN is not configured")
    source_sha = os.environ.get("GITHUB_SHA", "")
    try:
        from huggingface_hub import HfApi

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            build_bundle(root, bundle, source_sha)
            deploy_bundle(HfApi(token=token), bundle, source_sha)
    except DeploymentFailure as error:
        raise SystemExit(f"Space sync failed: {error.code}") from None
    except ValueError as error:
        # Only exact application-owned messages may be translated into log codes.
        codes = {
            "Expected an existing public Docker Space": "SPACE_CONFIGURATION_MISMATCH",
            "Expected CPU Basic hardware": "HARDWARE_NOT_CONFIRMED_CPU_BASIC",
            "Unexpected deployment target": "TARGET_MISMATCH",
        }
        code = codes.get(str(error), "BUNDLE_VALIDATION_FAILED")
        raise SystemExit(f"Space sync failed: {code}") from None
    except Exception:
        raise SystemExit("Space sync failed: UNEXPECTED_DEPLOYMENT_FAILURE") from None
    print(f"Synced tested commit {source_sha} to {SPACE_ID}. Check the Space build and live app separately.")


if __name__ == "__main__":
    main()
