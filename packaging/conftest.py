"""Readers for the packaging suite: ``debian/`` as dpkg-buildpackage will see it.

The tests next door are assertions only; the parsing lives here, the way
``daemon/conftest.py`` holds that suite's shared machinery. Nothing here builds
or installs anything — every reader works off the files in the tree, so the suite
runs on a machine with no debhelper and no root.

Run: ``python3 -m pytest packaging -q`` (or ``cd packaging && python3 -m pytest``).
"""
from __future__ import annotations

import ast
import configparser
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBIAN = ROOT / "debian"

# debian/rules generates these install sources at build time, so they are absent
# from a clean checkout and cannot be checked for existence.
BUILT_BY_RULES = "debian/icons/"

PACKAGE_DIRS = {"gigactld": "daemon", "gigactl_gui": "gui"}


def text(rel: str) -> str:
    return (ROOT / rel).read_text()


def rules_variable(name: str) -> str:
    """One ``NAME = value`` declaration from debian/rules."""
    match = re.search(rf"^{name}\s*=\s*(.*)$", text("debian/rules"), re.MULTILINE)
    assert match, f"debian/rules should declare {name}"
    return match.group(1).strip()


def private_dir() -> str:
    """The private module directory, without its leading slash (as dpkg paths go).

    debian/rules is the single declaration; everything else is checked against it.
    """
    return rules_variable("PRIVATE_DIR").lstrip("/")


def install_lines() -> list[tuple[str, str]]:
    """``debian/install`` as (source glob, destination dir) pairs."""
    pairs = []
    for line in text("debian/install").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        assert len(fields) == 2, f"expected 'source dest', got {line!r}"
        pairs.append((fields[0], fields[1]))
    return pairs


def link_lines() -> list[tuple[str, str]]:
    """``debian/links`` as (target, symlink) pairs, both in-package paths."""
    pairs = []
    for line in text("debian/links").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        assert len(fields) == 2, f"expected 'target link', got {line!r}"
        pairs.append((fields[0], fields[1]))
    return pairs


def installed_files() -> dict[str, str]:
    """Every path the package provides -> where it came from.

    Globs are expanded against the tree; sources debian/rules generates keep
    their literal name, since they do not exist until it has run. Symlinks from
    debian/links count as provided paths — /usr/bin/gigactl-gui is one, and a
    caller asking "does the package provide this binary" means either.
    """
    files = {}
    for source, dest in install_lines():
        if source.startswith(BUILT_BY_RULES):
            files[f"{dest}/{Path(source).name}"] = source
            continue
        for match in sorted(ROOT.glob(source)):
            files[f"{dest}/{match.name}"] = str(match.relative_to(ROOT))
    for target, link in link_lines():
        files[link] = f"symlink -> {target}"
    return files


def desktop_entry(rel: str) -> configparser.SectionProxy:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / rel)
    return parser["Desktop Entry"]


def unit_values(rel: str, key: str) -> list[str]:
    """A systemd unit's values for one key (units allow repeats, so a list)."""
    return [m.group(1).strip() for m in
            re.finditer(rf"^{key}=(.*)$", text(rel), re.MULTILINE)]


def control_text() -> str:
    """debian/control as dpkg reads it: comment lines removed.

    Policy 5.1 allows comments in a source package's control file and this one
    uses them to record why a dependency is there. dpkg strips them when it
    generates the binary package's control, so no test should be able to see a
    package name that only ever appeared in prose.
    """
    return "\n".join(line for line in text("debian/control").splitlines()
                     if not line.startswith("#"))


def control_field(field: str) -> str:
    """One field of the *binary* package stanza, unwrapped onto a single line."""
    stanza = control_text().split("\nPackage: ")[1]
    match = re.search(rf"^{field}:(.*?)(?=\n\S|\Z)", stanza, re.MULTILINE | re.DOTALL)
    assert match, f"debian/control's binary stanza has no {field}"
    return " ".join(match.group(1).split())


def icon_sizes() -> list[str]:
    return rules_variable("ICON_SIZES").split()


def changelog_version() -> str:
    return subprocess.run(
        ["dpkg-parsechangelog", "-l", str(DEBIAN / "changelog"), "-S", "Version"],
        capture_output=True, text=True, check=True).stdout.strip()


def launcher_programs() -> dict[str, str]:
    """The launcher's ``PROGRAMS`` map: invoked name -> package it runs."""
    match = re.search(r"^PROGRAMS = (\{.*?\})$", text("debian/bin/launcher"),
                      re.MULTILINE | re.DOTALL)
    assert match, "debian/bin/launcher should declare a PROGRAMS map"
    return ast.literal_eval(match.group(1))


def pyproject_scripts(rel: str) -> dict[str, str]:
    """A pyproject's ``[project.scripts]`` as name -> "module:function"."""
    section = text(rel).split("[project.scripts]")[1].split("\n[")[0]
    return dict(re.findall(r'^(\S+) = "(.+?)"$', section, re.MULTILINE))


# --- manual pages -----------------------------------------------------------

def manpages() -> list[str]:
    """Paths listed in debian/gigactl.manpages, in order."""
    return [line.split("#", 1)[0].strip()
            for line in text("debian/gigactl.manpages").splitlines()
            if line.split("#", 1)[0].strip()]


def shell_subcommands(tool: str) -> set[str]:
    """The subcommands a shell tool advertises in its own usage() text.

    Only the literal words — the ``<pct>`` and ``<color>`` placeholders are
    arguments, not subcommands, and are described in the man page's own prose.
    """
    usage = text(tool).split("usage() {", 1)[1].split("EOF", 2)[1]
    words = re.findall(rf"^  {tool} (\S+)", usage, re.MULTILINE)
    return {w for w in words if w.isalpha()}


def rendered(page: str) -> str:
    """A man page's text with roff's escapes flattened, for content matching.

    A literal hyphen-minus in roff is ``\\-`` — a bare ``-`` is a typographic
    hyphen — so an option reads ``\\-\\-tray`` in the source and ``--tray`` on
    screen. Tests care about the latter.
    """
    return text(page).replace("\\-", "-").replace("\\fI", "").replace("\\fR", "")


def long_options(rel: str) -> set[str]:
    """Long options a Python entry point actually looks for in argv."""
    return set(re.findall(r'"(--[a-z][a-z-]*)"', text(rel)))


# --- the workflows ----------------------------------------------------------
#
# The readers default to the release workflow, which is what test_release.py
# reads; test_ci.py passes TEST_WORKFLOW.

RELEASE_WORKFLOW = ".github/workflows/release.yml"
TEST_WORKFLOW = ".github/workflows/test.yml"


def workflow(path: str = RELEASE_WORKFLOW) -> dict:
    """A workflow, parsed. YAML reads bare ``on`` as True, so it is normalised
    back to the string GitHub actually means."""
    import yaml

    parsed = yaml.safe_load(text(path))
    if True in parsed:
        parsed["on"] = parsed.pop(True)
    return parsed


def only_job(path: str = RELEASE_WORKFLOW) -> dict:
    """The workflow's single job. Each workflow has one thing to do: the release
    builds and attaches, the test workflow runs the suites."""
    jobs = workflow(path)["jobs"]
    assert len(jobs) == 1, f"expected one job, got {sorted(jobs)}"
    return next(iter(jobs.values()))


def run_steps(path: str = RELEASE_WORKFLOW) -> list[tuple[str, str]]:
    """Every step that runs a shell script, as (name, script)."""
    return [(step.get("name", "<unnamed>"), step["run"])
            for step in only_job(path)["steps"] if "run" in step]


def workflow_shell(path: str = RELEASE_WORKFLOW) -> str:
    """All of the workflow's shell, concatenated — for 'is X done anywhere' checks."""
    return "\n".join(script for _name, script in run_steps(path))


def commands(script: str) -> str:
    """A script's executable lines: comments and blanks dropped, so a test asking
    'does it run X' is not answered by a comment mentioning X."""
    return "\n".join(line for line in script.splitlines()
                     if line.strip() and not line.strip().startswith("#"))


def workflow_commands(path: str = RELEASE_WORKFLOW) -> str:
    return "\n".join(commands(script) for _name, script in run_steps(path))


def apt_installed() -> set[str]:
    """Exact package names the workflow installs by hand (not via build-dep)."""
    packages = set()
    for line in workflow_commands().splitlines():
        if "apt-get install" not in line:
            continue
        args = line.split("apt-get install", 1)[1].split()
        packages.update(a for a in args if not a.startswith("-"))
    return packages


def build_depends() -> list[str]:
    """Bare package names from debian/control's Build-Depends."""
    match = re.search(r"^Build-Depends:(.*?)(?=^\S)", control_text(),
                      re.MULTILINE | re.DOTALL)
    assert match, "debian/control has no Build-Depends"
    names = []
    for entry in match.group(1).split(","):
        entry = entry.strip()
        if entry:
            names.append(entry.split()[0].split(":")[0])
    return names


# --- what the shipped code actually needs at runtime -------------------------
#
# The Depends field used to be checked against a hand-written list, so it could
# only catch a dependency somebody remembered to add — which is how a GTK app
# drawing with cairo shipped without the PyGObject cairo bridge and died on
# every draw callback (#25). These readers derive the requirement from the code
# the package installs instead, so a new import has to be declared or explained.

def shipped_python_modules() -> list[Path]:
    """Every .py file the package installs, as paths in the source tree."""
    return sorted(ROOT / source for path, source in installed_files().items()
                  if path.endswith(".py"))


def _first_party() -> set[str]:
    """Module names that resolve inside the package itself, not to a dependency."""
    names = set(PACKAGE_DIRS)
    for module in shipped_python_modules():
        names.add(module.stem)
    return names


def third_party_imports() -> set[str]:
    """Top-level module names the shipped code imports from outside itself.

    Relative imports are internal by definition. Absolute ones are checked
    against the standard library and the package's own modules, so what is left
    is exactly the set that has to come from a Debian package.
    """
    external = set()
    for module in shipped_python_modules():
        tree = ast.parse(module.read_text(), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                external.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                external.add(node.module.split(".")[0])
    import sys
    return external - sys.stdlib_module_names - _first_party()


def required_gi_namespaces() -> set[tuple[str, str]]:
    """(namespace, version) pairs the shipped code pins with gi.require_version.

    A namespace has to be present before ``from gi.repository import X`` works,
    and each comes from its own gir1.2-* package — so pinning one is a runtime
    dependency in exactly the way an import is.
    """
    pairs = set()
    for module in shipped_python_modules():
        pairs.update(re.findall(r'require_version\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)',
                                module.read_text()))
    return pairs
