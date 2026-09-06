"""Check release versions and bundled skill metadata without a model server."""

from __future__ import annotations

import os
from importlib.metadata import version
from pathlib import Path

import yaml

from minicpm_network_doctor import __version__
from minicpm_network_doctor.prompt import load_system_prompt
from minicpm_network_doctor.tools import USER_AGENT

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    package_version = version("minicpm-network-doctor")
    assert package_version == __version__, (package_version, __version__)
    assert f"minicpm-network-doctor/{package_version}" == USER_AGENT
    assert "fake-ip" in load_system_prompt()
    assert f"## {package_version} — " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        assert os.environ["GITHUB_REF_NAME"] == f"v{package_version}", "tag/version mismatch"

    skill_dir = ROOT / "skills/minicpm-network-doctor"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\n"), "missing skill frontmatter"
    frontmatter = yaml.safe_load(skill.split("---", 2)[1])
    assert frontmatter["name"] == "minicpm-network-doctor"
    assert isinstance(frontmatter["description"], str) and frontmatter["description"].strip()
    assert "TODO" not in skill
    agent = yaml.safe_load((skill_dir / "agents/openai.yaml").read_text(encoding="utf-8"))
    for key in ("display_name", "short_description", "default_prompt"):
        assert isinstance(agent["interface"][key], str) and agent["interface"][key].strip()
    assert "$minicpm-network-doctor" in agent["interface"]["default_prompt"]
    print(f"Version {package_version}, prompt resource, and skill metadata verified.")


if __name__ == "__main__":
    main()
