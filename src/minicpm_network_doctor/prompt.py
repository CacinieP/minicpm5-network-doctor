from importlib.resources import files


def load_system_prompt() -> str:
    return (
        files("minicpm_network_doctor")
        .joinpath("system_prompt.md")
        .read_text(encoding="utf-8")
        .strip()
    )
