from minicpm5_network_doctor.cli import main


def test_list_tools_does_not_require_model_server(capsys) -> None:
    assert main(["--list-tools"]) == 0
    output = capsys.readouterr().out
    assert "resolve_dns" in output
    assert "inspect_tls" in output
    assert "run_shell" not in output
