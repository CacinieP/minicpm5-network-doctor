"""Install both release distributions in fresh environments and smoke-test offline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import venv
from pathlib import Path

PACKAGE = "minicpm-network-doctor"
MODULE = "minicpm_network_doctor"
EXPECTED_TOOLS = [
    "resolve_dns",
    "test_tcp",
    "test_http",
    "inspect_tls",
    "inspect_proxy_environment",
    "inspect_hosts_file",
    "system_network_context",
]

NETWORK_GUARD = textwrap.dedent(
    """\
    import sys

    class NetworkAccessBlocked(BaseException):
        pass

    def guard(event, args):
        if event in {
            "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
            "socket.gethostbyaddr", "socket.sendto", "socket.sendmsg",
            "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
        }:
            raise NetworkAccessBlocked("Offline package smoke blocked: " + event)

    sys.addaudithook(guard)
    sys._network_doctor_smoke_guard = True
    """
)

PACKAGE_PROBE = textwrap.dedent(
    """\
    import json
    import sys
    from importlib.metadata import version
    from pathlib import Path

    if not getattr(sys, "_network_doctor_smoke_guard", False):
        raise RuntimeError("Offline smoke guard did not load")

    import minicpm_network_doctor as package
    from minicpm_network_doctor.prompt import load_system_prompt
    from minicpm_network_doctor.tools import TOOLS, USER_AGENT

    installed_version = version("minicpm-network-doctor")
    if installed_version != package.__version__:
        raise RuntimeError("Installed metadata and package versions disagree")
    if USER_AGENT != "minicpm-network-doctor/" + installed_version:
        raise RuntimeError("Diagnostic User-Agent and package versions disagree")
    if not Path(package.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("Imported package is outside the clean virtual environment")
    if not load_system_prompt().strip():
        raise RuntimeError("Packaged system prompt is empty")
    print(json.dumps({"version": installed_version, "tools": list(TOOLS)}))
    """
)


def run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: int = 30,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command!r}\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def verify_distribution(artifact: Path, work: Path, environment: dict[str, str]) -> str:
    work.mkdir()
    environment_path = work / "venv"
    venv.EnvBuilder(with_pip=True).create(environment_path)
    binaries = environment_path / ("Scripts" if os.name == "nt" else "bin")
    python = binaries / ("python.exe" if os.name == "nt" else "python")
    console = binaries / (f"{PACKAGE}.exe" if os.name == "nt" else PACKAGE)

    print(f"Installing {artifact.name} in {environment_path}", flush=True)
    run(
        [
            str(python),
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            str(artifact),
        ],
        cwd=work,
        environment=environment,
        timeout=300,
    )

    guard_path = work / "offline_guard"
    guard_path.mkdir()
    (guard_path / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    smoke_environment = {
        key: value
        for key, value in environment.items()
        if not key.startswith(("MINICPM_", "OPENAI_"))
    }
    smoke_environment["PYTHONPATH"] = str(guard_path)
    smoke_environment["PYTHONSAFEPATH"] = "1"
    metadata = json.loads(
        run(
            [str(python), "-c", PACKAGE_PROBE],
            cwd=work,
            environment=smoke_environment,
        )
    )
    version = metadata["version"]
    if metadata["tools"] != EXPECTED_TOOLS:
        raise RuntimeError(f"Packaged registry does not contain the seven tools: {artifact.name}")

    for entrypoint in ([str(console)], [str(python), "-m", MODULE]):
        output = run([*entrypoint, "--version"], cwd=work, environment=smoke_environment).strip()
        if output != f"{PACKAGE} {version}":
            raise RuntimeError(f"Unexpected CLI version from {artifact.name}: {output!r}")
        help_output = run([*entrypoint, "--help"], cwd=work, environment=smoke_environment)
        if "--list-tools" not in help_output or "--check-server" not in help_output:
            raise RuntimeError(f"Incomplete CLI help from {artifact.name}")
        schemas = json.loads(
            run(
                [*entrypoint, "--list-tools", "--json"],
                cwd=work,
                environment=smoke_environment,
            )
        )
        valid_schemas = (
            isinstance(schemas, list)
            and len(schemas) == len(EXPECTED_TOOLS)
            and all(
                isinstance(item, dict)
                and item.get("type") == "function"
                and isinstance(item.get("function"), dict)
                and isinstance(item["function"].get("parameters"), dict)
                and item["function"]["parameters"].get("additionalProperties") is False
                for item in schemas
            )
        )
        if not valid_schemas or [item["function"]["name"] for item in schemas] != EXPECTED_TOOLS:
            raise RuntimeError(f"Unexpected CLI tool schemas from {artifact.name}")

    run([str(python), "-m", "pip", "check"], cwd=work, environment=smoke_environment)
    print(f"PASS {artifact.name}: isolated install, resources, versions, offline CLI", flush=True)
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dist", type=Path, help="Directory containing one wheel and one source tarball"
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Parent directory outside the checkout for retained verification environments",
    )
    args = parser.parse_args()
    dist = args.dist.resolve()
    wheels = sorted(dist.glob("*.whl"))
    sources = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error(f"Expected exactly one wheel and one source tarball in {dist}")
    if not all(artifact.is_file() for artifact in [*wheels, *sources]):
        parser.error("Distribution artifacts must be files")

    checkout = Path(__file__).resolve().parents[1]
    parent = (args.work_dir or Path(tempfile.gettempdir())).resolve()
    if parent.is_relative_to(checkout):
        parser.error("Verification work directory must be outside the checkout")
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="network-doctor-package-", dir=parent))
    print(f"Verification files will be retained in {work}", flush=True)

    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"):
        environment.pop(key, None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    try:
        wheel_version = verify_distribution(wheels[0], work / "wheel", environment)
        source_version = verify_distribution(sources[0], work / "sdist", environment)
        if wheel_version != source_version:
            raise RuntimeError("Wheel and source distribution versions disagree")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}\nVerification files retained in {work}", file=sys.stderr)
        return 1
    print(f"PASS: both distributions verified for {PACKAGE} {wheel_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
