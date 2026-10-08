from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "firmware" / "src" / "main.cpp"
LV_CONF = ROOT / "firmware" / "include" / "lv_conf.h"
PLATFORMIO = ROOT / "firmware" / "platformio.ini"
FONT_DIR = ROOT / "firmware" / "src" / "fonts"


def source_text() -> str:
    return SOURCE.read_text()


def test_process_rack_uses_approved_mixed_case_typography():
    source = source_text()

    assert '#include "fonts/instrument_sans_12.h"' in source
    assert '#include "fonts/instrument_sans_29.h"' in source
    assert 'lv_label_set_text(clockCaption, "Clock")' in source
    assert 'lv_label_set_text(renderTimeCaption, "Render time")' in source
    assert '"Prepare"' in source
    assert '"Generate"' in source
    assert '"Finish"' in source
    assert '"RENDER / BEACON"' not in source
    assert '"SYSTEM READY"' not in source
    assert '"NODE PROGRESS"' not in source
    assert 'showOffline("BRIDGE UNAVAILABLE")' not in source


def test_process_rack_geometry_matches_approved_exact_resolution_comp():
    source = source_text()

    assert "constexpr uint16_t kMainStageX = 18;" in source
    assert "constexpr uint16_t kMainStageY = 45;" in source
    assert "constexpr uint16_t kMainStageWidth = 444;" in source
    assert "constexpr uint16_t kMainStageHeight = 162;" in source
    assert "constexpr uint16_t kStageBayWidth = 78;" in source
    assert "constexpr uint16_t kStageBayHeight = 128;" in source
    assert "constexpr uint16_t kStageRingSize = 60;" in source
    assert "constexpr uint16_t kMasterRingSize = 116;" in source
    assert "constexpr uint16_t kRackMasterGap = 24;" in source
    assert "lv_obj_t *stageBays[3];" in source
    assert "lv_obj_t *stageArcs[3];" in source
    assert "lv_obj_t *stageIndexLabels[3];" in source
    assert "lv_obj_t *stageNameLabels[3];" in source
    assert "lv_obj_t *stageStateLabels[3];" in source


def test_active_scan_motion_is_bounded_to_radial_zone():
    source = source_text()

    assert "constexpr uint16_t kScanStartY = 23;" in source
    assert "constexpr uint16_t kScanTravel = 68;" in source
    assert "lv_obj_t *stageScanLines[3];" in source
    assert "void animateActiveStage(uint32_t now)" in source
    assert "triangleWave(now, 1400, kScanTravel)" in source


def test_real_preview_replaces_only_process_rack_and_keeps_master_visible():
    source = source_text()

    assert "constexpr uint16_t kPreviewPanelX = 47;" in source
    assert "constexpr uint16_t kPreviewPanelY = 54;" in source
    assert "constexpr uint16_t kPreviewPanelWidth = 246;" in source
    assert "constexpr uint16_t kPreviewPanelHeight = 128;" in source
    assert "constexpr uint16_t kPreviewDecodedWidth = 140;" in source
    assert "constexpr uint16_t kPreviewDecodedHeight = 88;" in source
    assert "lv_obj_add_flag(processRack, LV_OBJ_FLAG_HIDDEN);" in source
    assert "lv_obj_clear_flag(previewPanel, LV_OBJ_FLAG_HIDDEN);" in source
    assert "lv_obj_add_flag(masterArc, LV_OBJ_FLAG_HIDDEN);" not in source


def test_firmware_consumes_normalized_pipeline_without_raw_node_names():
    source = source_text()

    assert "DynamicJsonDocument stateDocument(kMaxStateBytes);" in source
    assert "StaticJsonDocument<kMaxStateBytes> stateDocument;" not in source
    assert "StaticJsonDocument<kMaxStateBytes> document;" not in source
    assert 'stateDocument["pipeline"]' in source
    assert '["master_percent"]' in source
    assert '["active_stage"]' in source
    assert '["stages"]' in source
    assert '["metadata"]' in source
    assert "void updatePipeline(JsonObjectConst pipeline)" in source
    assert "node_class" not in source


def test_v2_left_rail_is_queue_model_and_icon_only():
    source = source_text()
    assert 'lv_label_set_text(queueCaption, "Queue")' in source
    assert 'rail["model_name"]' in source
    assert 'rail["modality_icon"]' in source
    assert "modalityIconBars[3]" in source
    assert "modalityLabel" not in source
    assert "modality_label" not in source


def test_completed_takeover_uses_one_jpeg_buffer_and_exact_loop_contract():
    source = source_text()
    assert source.count("std::vector<uint8_t> jpegBytes;") == 1
    assert "constexpr uint8_t kCompletedVideoLoops = 3;" in source
    assert "takeoverLoops >= kCompletedVideoLoops" in source
    assert "kCompletedCardHoldMs = 10000" in source
    assert "TJpgDec.setCallback(fullScreenJpegBlock);" in source
    assert '"/v2/media/"' in source
    assert "MP4" not in source
    assert "AudioFile" not in source


def test_render_metadata_and_five_instruments_match_approved_layout():
    source = source_text()

    assert "lv_obj_t *renderMetaStrip;" in source
    for label in ["GPU", "CPU", "VRAM", "RAM", "GPU °C"]:
        assert f'"{label}"' in source
    assert "lv_obj_t *metricBars[5];" in source
    assert "lv_obj_set_size(metricBars[index], 80, 4);" in source
    assert 'document["system"]["cpu_percent"]' in source
    assert 'document["system"]["ram_used_bytes"]' in source
    assert 'document["gpu"]["memory_used_mib"]' in source


def test_master_progress_is_pipeline_progress_not_node_progress():
    source = source_text()

    assert "lv_arc_set_value(masterArc, masterPercent);" in source
    assert 'document["active"]["progress"]["percent"]' not in source
    assert 'lv_label_set_text(masterCaption, "Master flow")' in source


def test_removed_decorative_machine_parts_do_not_return():
    source = source_text()

    assert "feedPackets" not in source
    assert "controlDots" not in source
    assert "scannerHead" not in source
    assert "addCornerMark" not in source


def test_serial_provisioning_and_nvs_keys_remain_backward_compatible():
    source = source_text()

    assert 'preferences.getString("wifi_ssid", "")' in source
    assert 'preferences.getString("wifi_password", "")' in source
    assert 'preferences.getString("bridge_url", kDefaultBridgeUrl)' in source
    assert 'preferences.putString("wifi_ssid", ssid)' in source
    assert 'preferences.putString("wifi_password", password)' in source
    assert 'preferences.putString("bridge_url", bridge)' in source
    assert "void handleSerialProvisioning()" in source
    assert "Serial.readStringUntil('\\n')" in source
    assert 'Serial.println("RB1 OK")' in source
    assert "handleSerialProvisioning();" in source


def test_custom_font_budget_disables_unused_montserrat_sizes():
    config = LV_CONF.read_text()

    assert "#define LV_FONT_MONTSERRAT_12 0" in config
    assert "#define LV_FONT_MONTSERRAT_16 0" in config
    assert "#define LV_FONT_MONTSERRAT_20 0" in config
    assert "#define LV_FONT_MONTSERRAT_24 0" in config
    assert "#define LV_FONT_MONTSERRAT_28 0" in config
    font_12 = FONT_DIR / "instrument_sans_12.c"
    font_29 = FONT_DIR / "instrument_sans_29.c"
    assert font_12.is_file()
    assert font_29.is_file()
    assert ".bitmap_format = 0" in font_12.read_text()
    assert ".bitmap_format = 0" in font_29.read_text()
    assert "-D LV_LVGL_H_INCLUDE_SIMPLE=1" in PLATFORMIO.read_text()
    assert "-D ARDUINOJSON_USE_LONG_LONG=1" in PLATFORMIO.read_text()
    assert "#define LV_USE_ARC 1" in config
