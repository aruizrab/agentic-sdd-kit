"""named-pipeline.sh: a copy of the feature pipeline whose meta.name names the feature."""
import shutil
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SCRIPT = KIT / "tools/sdd/scripts/named-pipeline.sh"
PIPELINE = KIT / "tools/sdd/pipeline/feature-pipeline.js"


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tools/sdd/pipeline").mkdir(parents=True)
    shutil.copy(PIPELINE, tmp_path / "tools/sdd/pipeline/feature-pipeline.js")
    return tmp_path


def _run(cwd, *args):
    return subprocess.run([str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


def test_copy_differs_only_in_the_name_line(tmp_path):
    repo = _repo(tmp_path)
    r = _run(repo, "F011", "user-login", "spec")
    assert r.returncode == 0, r.stderr
    out = Path(r.stdout.strip())
    assert out == repo / ".wt/pipeline-runs/F011-user-login-spec.js"
    src = PIPELINE.read_text().splitlines()
    copy = out.read_text().splitlines()
    assert len(src) == len(copy)
    diff = [(a, b) for a, b in zip(src, copy) if a != b]
    assert diff == [("  name: 'feature-pipeline',", "  name: 'F011-user-login-spec',")]


def test_rejects_names_that_need_quoting(tmp_path):
    repo = _repo(tmp_path)
    r = _run(repo, "F011", "user login")
    assert r.returncode == 2
    assert not (repo / ".wt/pipeline-runs").exists()


def test_fails_when_the_name_line_is_missing(tmp_path):
    repo = _repo(tmp_path)
    src = repo / "tools/sdd/pipeline/feature-pipeline.js"
    src.write_text(src.read_text().replace("name: 'feature-pipeline',", "name: 'other',"))
    r = _run(repo, "F011", "user-login")
    assert r.returncode == 1
    assert not (repo / ".wt/pipeline-runs/F011-user-login.js").exists()
