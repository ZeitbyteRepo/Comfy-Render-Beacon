from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "firmware" / "src" / "sd_readonly_probe.cpp"
PLATFORMIO = ROOT / "firmware" / "platformio.ini"
RUNBOOK = ROOT / "firmware" / "SD_READONLY_PROBE.md"


def probe_source() -> str:
    return SOURCE.read_text()


def test_probe_is_a_separate_build_target_from_production_firmware():
    config = PLATFORMIO.read_text()

    production = config.split("[env:hosyond-esp32-32e-st7796]", 1)[1].split(
        "[env:sd-readonly-probe]", 1
    )[0]
    probe = config.split("[env:sd-readonly-probe]", 1)[1]
    assert "+<main.cpp>" in production
    assert "+<fonts/>" in production
    assert "sd_readonly_probe.cpp" not in production
    assert "+<sd_readonly_probe.cpp>" in probe
    assert "main.cpp" not in probe


def test_probe_uses_audited_hspi_wiring_and_registration_parameters():
    source = probe_source()

    assert "SPIClass sdSpi(HSPI);" in source
    assert "static_assert(kSdCs == 5" in source
    assert "kSdSck == 18 && kSdMiso == 19 && kSdMosi == 23" in source
    assert "static_assert(kExternalSpiCs == 21" in source
    assert "static_assert(TFT_SCLK == 14 && TFT_MOSI == 13 && TFT_MISO == 12" in source
    assert "sdSpi.begin(18, 19, 23, 5);" in source
    assert 'SD.begin(5, sdSpi, 1000000U, "/sd", 5, false);' in source

    cs_high = source.index("digitalWrite(5, HIGH);")
    spi_class = source.index("static SPIClass sdSpi(HSPI);")
    spi_begin = source.index("sdSpi.begin(18, 19, 23, 5);")
    mount = source.index('SD.begin(5, sdSpi, 1000000U, "/sd", 5, false);')
    assert cs_high < spi_class < spi_begin < mount


def test_probe_compiles_no_format_write_delete_or_serial_path():
    source = probe_source()

    forbidden = [
        "FILE_WRITE",
        "SD.format",
        "SD.remove",
        "SD.rename",
        "SD.mkdir",
        "SD.rmdir",
        "file.write",
        "testFile.write",
        "Serial.",
    ]
    for token in forbidden:
        assert token not in source

    opens = re.findall(r"SD\.open\(([^;]+)\);", source)
    assert opens == ["kTestPath, FILE_READ", '"/", FILE_READ'] or opens == [
        '"/", FILE_READ',
        "kTestPath, FILE_READ",
    ]
    assert source.count("SD.begin(") == 1
    assert 'SD.exists(kTestPath)' in source


def test_probe_validates_card_capacity_root_and_only_optional_fixture():
    source = probe_source()

    assert "SD.cardType()" in source
    assert "result.cardType != CARD_NONE" in source
    assert "SD.cardSize()" in source
    assert "result.capacityBytes > 0" in source
    assert 'SD.open("/", FILE_READ)' in source
    assert "root.isDirectory()" in source
    assert 'constexpr char kTestPath[] = "/RB_SD_TEST.TXT";' in source
    assert "constexpr size_t kTestPreviewBytes = 40;" in source
    assert '"NOT PRESENT (optional)"' in source


def test_probe_has_bounded_tft_pass_fail_output_and_documented_rst_audit():
    source = probe_source()
    docs = RUNBOOK.read_text()
    config = PLATFORMIO.read_text()

    assert 'success ? "PASS" : "FAIL"' in source
    assert '"Mount /sd"' in source
    assert '"Card type"' in source
    assert '"Capacity"' in source
    assert '"Root directory"' in source
    assert "delay(1000);" in source
    assert "-D TFT_RST=12" in config
    assert "vendor definition uses `TFT_RST=-1`" in docs
    assert "unresolved" in docs.lower()
