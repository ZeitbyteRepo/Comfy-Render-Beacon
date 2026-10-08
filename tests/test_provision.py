from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "provision.py"
spec = spec_from_file_location("render_beacon_provision", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)


def test_provision_command_does_not_expose_credentials_as_plaintext():
    line = module.encode_command("Studio WiFi", "correct horse battery staple", "http://192.168.1.2:8220")
    assert line.startswith(b"RB1\t")
    assert line.endswith(b"\n")
    assert b"Studio WiFi" not in line
    assert b"correct horse" not in line


def test_provision_command_round_trips_all_fields():
    fields = module.decode_command(module.encode_command("ssid", "password", "http://192.168.1.2:8220"))
    assert fields == ("ssid", "password", "http://192.168.1.2:8220")
