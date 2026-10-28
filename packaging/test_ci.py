"""The pull request workflow: what code nobody has reviewed yet gets to do.

test.yml runs every suite on every pull request, forks included. Its most
important property never shows up as a failing run: that the code it runs gets a
read-only token and no secrets. A well-meant edit — "CI cannot see secrets on
fork pull requests, use pull_request_target" — would break that silently, so it
is pinned here, along with the mistakes that would make CI quietly test less
than it says.

Run: ``python3 -m pytest packaging -q``
"""
from __future__ import annotations

import subprocess

import pytest
from conftest import TEST_WORKFLOW, commands, only_job, run_steps, workflow

# Every suite in the tree. tools/ joins them once it exists; the workflow
# already runs it behind an `if [ -d tools ]` guard.
SUITES = {"packaging", "cli", "daemon", "gui"}
OPTIONAL_SUITES = {"tools"}


def test_it_runs_on_every_pull_request_and_every_push_to_master():
    triggers = workflow(TEST_WORKFLOW)["on"]
    assert set(triggers) == {"pull_request", "push"}, sorted(triggers)
    assert triggers["push"] == {"branches": ["master"]}


def test_fork_code_never_runs_with_the_repository_s_secrets():
    """pull_request_target runs with this repository's token and secrets, so
    checking out a fork's code under it hands both to whoever opened the pull
    request. pull_request gives a fork a read-only token and no secrets."""
    assert "pull_request_target" not in workflow(TEST_WORKFLOW)["on"]


def test_the_token_is_read_only():
    assert workflow(TEST_WORKFLOW)["permissions"] == {"contents": "read"}
    assert "permissions" not in only_job(TEST_WORKFLOW), "a job must not widen it"


def test_the_runner_is_the_one_the_release_is_built_on():
    """A green pull request should mean what a green tag build means."""
    assert only_job(TEST_WORKFLOW)["runs-on"] == only_job()["runs-on"]


def _pytest_invocations() -> list[list[str]]:
    """Every `python3 -m pytest …` command line in the workflow, tokenised."""
    return [line.split() for _name, script in run_steps(TEST_WORKFLOW)
            for line in commands(script).splitlines() if "-m pytest" in line]


def test_every_suite_runs():
    """A suite dropped from CI is a suite nobody runs before merging."""
    ran = {arg for line in _pytest_invocations() for arg in line}
    assert SUITES <= ran, f"never run: {sorted(SUITES - ran)}"


def test_each_suite_gets_its_own_pytest_invocation():
    """As in release.yml: every suite keeps its helpers in a top-level
    conftest.py, so a shared run imports only one of them and the other suite
    fails to collect."""
    for line in _pytest_invocations():
        named = set(line) & (SUITES | OPTIONAL_SUITES)
        assert len(named) == 1, f"one suite per invocation: {' '.join(line)}"


def test_no_run_step_interpolates_into_the_shell():
    """A branch name or a pull request title is attacker-controlled, and ${{ }}
    inside a run: script is the documented script-injection hole."""
    for name, script in run_steps(TEST_WORKFLOW):
        assert "${{" not in script, f"{name} interpolates into shell directly"


@pytest.mark.parametrize("index", range(len(run_steps(TEST_WORKFLOW))))
def test_every_run_step_is_valid_shell(index):
    name, script = run_steps(TEST_WORKFLOW)[index]
    proc = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                          text=True)
    assert proc.returncode == 0, f"{name}: {proc.stderr}"
