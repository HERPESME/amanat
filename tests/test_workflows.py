"""The scheduled jobs call things that exist, and never write to the repository.

A workflow file cannot be run here, but its commands can be checked: every `python -m` module it
invokes must import, and neither job is given permission to push, so a bad night cannot rewrite
history or the evidence it is there to guard.
"""
import importlib.util
import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
FILES = sorted(WORKFLOWS.glob("*.yml"))


def test_the_three_jobs_exist():
    assert {p.name for p in FILES} >= {"ci.yml", "watch.yml", "probes.yml"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_module_a_workflow_runs_can_be_found(path):
    for module in re.findall(r"python -m ([\w.]+)", path.read_text(encoding="utf-8")):
        assert importlib.util.find_spec(module) is not None, f"{path.name} runs {module}, which does not exist"


@pytest.mark.parametrize("name", ["watch.yml", "probes.yml"])
def test_the_scheduled_jobs_can_only_read_the_repository(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert re.search(r"permissions:\s*\n\s+contents: read", text)
    assert "contents: write" not in text and "git push" not in text and "git commit" not in text


def test_the_probe_job_does_nothing_without_credentials_rather_than_failing():
    text = (WORKFLOWS / "probes.yml").read_text(encoding="utf-8")
    assert "are not set as repository secrets" in text and "exit 0" in text


def test_the_probe_job_runs_the_drift_alarm_after_the_probes():
    text = (WORKFLOWS / "probes.yml").read_text(encoding="utf-8")
    assert text.index("amanat.probes run") < text.index("tests/test_registry_probes.py")


def test_the_credentials_are_never_expanded_into_an_echo_or_a_notice():
    """Naming a secret in a message is fine; expanding its value is not."""
    text = (WORKFLOWS / "probes.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "echo" in line:
            assert "$CASHFREE" not in line and "${CASHFREE" not in line and "secrets." not in line, line
