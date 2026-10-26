"""Validation-only CI remains separate from publication and hardware work."""
import subprocess

import yaml

from conftest import text


def test_validation_runs_suites_separately_without_publication_or_hardware():
    body = text(".github/workflows/validate.yml")
    config = yaml.safe_load(body)
    assert config["permissions"] == {"contents": "read"}
    job, = config["jobs"].values()
    assert job["strategy"]["matrix"]["suite"] == ["daemon/tests", "gui/tests", "cli", "packaging"]
    steps = job["steps"]
    tests = [step for step in steps if "pytest" in step.get("run", "")
             and "-m pytest" in step["run"]]
    assert len(tests) == 1
    assert tests[0]["env"]["SUITE"] == "${{ matrix.suite }}"
    assert 'python3 -m pytest "$SUITE"' in tests[0]["run"]
    assert "not hardware" in tests[0]["run"]
    for step in steps:
        if "run" not in step:
            continue
        script = step["run"]
        assert "${{" not in script
        assert subprocess.run(["bash", "-n"], input=script, text=True,
                              capture_output=True).returncode == 0
        for forbidden in ("gh release", "rpmbuild", "dpkg-buildpackage", "systemctl",
                          "modprobe", "pip install .", "apt-get install ./"):
            assert forbidden not in script
