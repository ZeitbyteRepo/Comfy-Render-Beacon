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
    assert "constexpr uint16_t kMainStageHeight = 203;" in source
    assert "constexpr uint16_t kMasterRingSize = 116;" in source
    assert "constexpr uint16_t kStageLineWidth = 210;" in source
    assert "constexpr uint16_t kStageLineHeight = 20;" in source
    assert "lv_obj_t *stageBays[3];" in source
    assert "lv_obj_t *stageArcs[3];" not in source
    assert "lv_obj_t *stageIndexLabels[3];" in source
    assert "lv_obj_t *stageNameLabels[3];" in source
    assert "lv_obj_t *stageStateLabels[3];" in source


def test_three_stage_rows_replace_stage_radials_and_scan_animation():
    source = source_text()
    assert "3 * kStageLineHeight + 4" in source
    assert "stageScanLines" not in source
    assert "animateActiveStage" not in source
    assert source.count("masterArc = makeArc") == 1


def test_recent_media_uses_three_bounded_right_column_thumbnails():
    source = source_text()
    assert "constexpr uint16_t kThumbnailWidth = 112;" in source
    assert "constexpr uint16_t kThumbnailHeight = 64;" in source
    assert "lv_obj_t *thumbnailSlots[3];" in source
    assert 'stateDocument["recent_media"]' in source
    assert '"/thumb.jpg"' in source
    assert "kMaxThumbnailBytes = 16 * 1024" in source
    assert "thumbnailRetryAt = now + kTakeoverRetryIntervalMs" in source


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


def test_firmware_hard_caps_state_and_jpeg_and_releases_json_before_media():
    source = source_text()
    assert "constexpr size_t kMaxStateBytes = 12 * 1024;" in source
    assert "constexpr size_t kMaxPreviewBytes = 64 * 1024;" in source
    assert "readBoundedBody(request, payload, kMaxStateBytes)" in source
    assert "request.getString()" not in source
    release = source.index("JSON document and response storage are released before JPEG allocation")
    allocate = source.index("tryPendingTakeover(millis())")
    assert release < allocate


def test_still_hold_clock_starts_only_after_successful_jpeg_draw():
    source = source_text()
    function = source[source.index("bool beginTakeover"):source.index("void advanceTakeover")]
    draw = function.index("if (!fetchTakeoverFrame(0))")
    clock = function.index("takeoverStartedAt = millis();")
    assert draw < clock
    assert "takeoverStartedAt = now" not in function


def test_running_state_retains_ready_completion_without_acknowledging_it():
    source = source_text()
    update = source[source.index("void updateState()"):source.index("String configPage")]
    retry = source[source.index("void tryPendingTakeover"):source.index("void updateState()")]
    assert "retainNewestReadyCompletion(sequence, completed);" in update
    assert 'lastMode == "running"' not in update.split("retainNewestReadyCompletion(sequence, completed);")[0].split("else if", 1)[-1]
    assert 'lastMode == "running"' in retry
    assert "lastCompletionSequence = pendingCompletionSequence;" not in retry.split("if (beginTakeover(pendingMedia))")[0]


def test_active_takeover_keeps_one_newest_completion_pending_without_interrupt():
    source = source_text()
    retain = source[source.index("void retainNewestReadyCompletion"):source.index("void tryPendingTakeover")]
    retry = source[source.index("void tryPendingTakeover"):source.index("void updateState()")]
    assert "sequence <= pendingCompletionSequence" in retain
    assert "pendingCompletionSequence = sequence;" in retain
    assert "takeoverKind != TakeoverKind::None" in retry
    assert 'lastMode == "running" && takeoverKind != TakeoverKind::None' not in source
    assert "takeoverKind == TakeoverKind::None && thumbnailsDirty" in source


def test_completion_acknowledgment_occurs_only_after_successful_takeover_start():
    source = source_text()
    begin = source[source.index("bool beginTakeover"):source.index("void advanceTakeover")]
    retry = source[source.index("void tryPendingTakeover"):source.index("void updateState()")]
    assert begin.index("if (!fetchTakeoverFrame(0))") < begin.index("return true;")
    success = retry.index("if (beginTakeover(pendingMedia))")
    acknowledge = retry.index("lastCompletionSequence = pendingCompletionSequence;")
    assert success < acknowledge


def test_failed_takeover_start_retries_then_is_deliberately_acknowledged():
    source = source_text()
    retry = source[source.index("void tryPendingTakeover"):source.index("void updateState()")]
    assert "constexpr uint8_t kMaxTakeoverStartAttempts = 3;" in source
    assert "constexpr uint32_t kTakeoverRetryIntervalMs = 2000;" in source
    assert "++pendingTakeoverAttempts;" in retry
    assert "pendingTakeoverAttempts >= kMaxTakeoverStartAttempts" in retry
    assert "pendingTakeoverRetryAt = now + kTakeoverRetryIntervalMs;" in retry
    assert retry.count("lastCompletionSequence = pendingCompletionSequence;") == 2


def test_preparing_sequence_is_not_consumed_before_it_becomes_ready():
    source = source_text()
    update = source[source.index("void updateState()"):source.index("String configPage")]
    preparing = update[update.index("} else if (sequence > pendingCompletionSequence)"):
                       update.index("    }\n  }  // JSON document")]
    assert "clearPendingCompletion();" in preparing
    assert "lastCompletionSequence = sequence;" not in preparing


def test_bridge_epoch_change_rebases_sequence_and_clears_pending_without_replay():
    source = source_text()
    rebase = source[source.index("void rebaseCompletionEpoch"):source.index("void retainNewestReadyCompletion")]
    update = source[source.index("void updateState()"):source.index("String configPage")]
    assert 'stateDocument["bridge_instance_epoch"]' in update
    assert "bridgeInstanceEpoch = epoch;" in rebase
    assert "lastCompletionSequence = sequence;" in rebase
    assert "clearPendingCompletion();" in rebase
    epoch_branch = update[update.index("if (bridgeInstanceEpoch.isEmpty()"):
                          update.index("} else if (sequence > lastCompletionSequence")]
    assert "rebaseCompletionEpoch(epoch, sequence);" in epoch_branch
    assert "retainNewestReadyCompletion" not in epoch_branch


def test_render_metadata_and_five_instruments_match_approved_layout():
    source = source_text()
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
