"""The probe command line: list, run, and what a run leaves behind."""
from pathlib import Path

from amanat.probes import __main__ as cli
from amanat.probes import catalogue, runner
from amanat.registry import store
from tests.test_probes_cashfree import happy, harness

PID = "cashfree_preauth.partial_capture"


def go(argv, respond=happy, tmp=None):
    lines = []
    code = cli.main(argv, harness=harness(respond), store_dir=tmp, out=lines.append)
    return code, "\n".join(lines)


def test_list_names_every_probe_with_its_question():
    code, text = go(["list"])
    assert code == 0
    for p in catalogue.PROBES.values():
        assert p.probe_id in text and p.summary.split(".")[0] in text


def test_a_dry_run_prints_the_findings_and_records_nothing(tmp_path):
    code, text = go(["run", PID, "--dry-run"], tmp=tmp_path)
    assert code == 0 and "partial_debit" in text and "supported" in text
    assert list(tmp_path.iterdir()) == []


def test_a_run_appends_one_verifiable_record_per_probe(tmp_path):
    code, text = go(["run", PID], tmp=tmp_path)
    path = tmp_path / "probes.cashfree_preauth.jsonl"
    assert code == 0 and store.verify(path).length == 1
    assert runner.latest_findings(path)[(PID, "partial_debit")]["supported"] is True
    assert "recorded" in text


def test_running_a_rail_without_naming_probes_runs_them_all(tmp_path):
    code, _ = go(["run", "--rail", "cashfree_preauth"], tmp=tmp_path)
    assert code == 0
    assert store.verify(tmp_path / "probes.cashfree_preauth.jsonl").length == len(catalogue.for_rail("cashfree_preauth"))


def test_an_unknown_probe_is_refused_by_name(tmp_path):
    code, text = go(["run", "cashfree_preauth.nope"], tmp=tmp_path)
    assert code == 2 and "cashfree_preauth.nope" in text


def test_a_run_that_learned_nothing_fails_so_a_scheduled_job_goes_red(tmp_path):
    code, text = go(["run", PID], respond=lambda m, p, b: (401, {"message": "authentication failed"}), tmp=tmp_path)
    assert code == 1 and "inconclusive" in text
    assert store.verify(tmp_path / "probes.cashfree_preauth.jsonl").length == 1     # the outage is evidence too


def test_the_default_store_is_the_committed_one():
    assert cli.DEFAULT_STORE_DIR == store.STORE_DIR


def test_verbose_prints_every_exchange_with_its_status(tmp_path):
    code, text = go(["run", PID, "--dry-run", "-v"], tmp=tmp_path)
    assert code == 0
    for label in ("order_create", "pay_collect", "simulate", "capture"):
        assert label in text
    assert "HTTP 200" in text
