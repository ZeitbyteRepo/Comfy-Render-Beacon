/*
Render Beacon — read-only ComfyUI process rack for the Hosyond ESP32/ST7796 panel.
The bridge owns workflow interpretation; firmware renders only normalized pipeline state.
*/
#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <TFT_eSPI.h>
#include <TJpg_Decoder.h>
#include <WebServer.h>
#include <WiFi.h>
#include <lvgl.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

#include "fonts/instrument_sans_12.h"
#include "fonts/instrument_sans_29.h"

namespace {
constexpr uint16_t kScreenWidth = 480;
constexpr uint16_t kScreenHeight = 320;
constexpr uint16_t kMainStageX = 18;
constexpr uint16_t kMainStageY = 45;
constexpr uint16_t kMainStageWidth = 444;
constexpr uint16_t kMainStageHeight = 203;
constexpr uint16_t kMasterRingSize = 116;
constexpr uint16_t kV2MainStageX = 110;
constexpr uint16_t kV2MainStageWidth = 352;
constexpr uint16_t kMasterX = 57;
constexpr uint16_t kMasterY = 8;
constexpr uint16_t kStageLinesX = 10;
constexpr uint16_t kStageLinesY = 130;
constexpr uint16_t kStageLineWidth = 210;
constexpr uint16_t kStageLineHeight = 20;
constexpr uint16_t kThumbnailX = 340;
constexpr uint16_t kThumbnailY = 45;
constexpr uint16_t kThumbnailWidth = 112;
constexpr uint16_t kThumbnailHeight = 64;
constexpr uint16_t kThumbnailGap = 4;
constexpr uint32_t kPollIntervalMs = 500;
constexpr uint32_t kPreviewIntervalMs = 1500;
constexpr uint32_t kReconnectIntervalMs = 10000;
constexpr size_t kMaxStateBytes = 12 * 1024;
constexpr size_t kMaxPreviewBytes = 64 * 1024;
constexpr size_t kMaxThumbnailBytes = 16 * 1024;
constexpr uint8_t kCompletedVideoLoops = 3;
constexpr uint8_t kMaxCompletedFrames = 24;
constexpr uint32_t kCompletedCardHoldMs = 10000;
constexpr uint8_t kMaxTakeoverStartAttempts = 3;
constexpr uint32_t kTakeoverRetryIntervalMs = 2000;
constexpr char kDefaultBridgeUrl[] = "http://192.168.1.2:8220";

constexpr uint32_t kInk = 0x10120F;
constexpr uint32_t kSurface = 0x1A1D18;
constexpr uint32_t kSurface2 = 0x252A22;
constexpr uint32_t kTrack = 0x30352D;
constexpr uint32_t kText = 0xEFEEE7;
constexpr uint32_t kMuted = 0x9DA399;
constexpr uint32_t kSecondary = 0xB7BCB2;
constexpr uint32_t kOrange = 0xEB6B45;
constexpr uint32_t kLime = 0xC9D65B;
constexpr uint32_t kBlue = 0x72A5DD;
constexpr uint32_t kDone = 0x69735E;
constexpr uint32_t kDoneText = 0xC9D58F;
constexpr uint32_t kPaper = 0xD8D6CE;

TFT_eSPI tft;
WebServer configServer(80);
Preferences preferences;
lv_disp_draw_buf_t drawBuffer;
lv_color_t lvBuffer[kScreenWidth * 12];

lv_obj_t *mainStage;
lv_obj_t *processRack;
lv_obj_t *stageBays[3];
lv_obj_t *stageIndexLabels[3];
lv_obj_t *stageRingLabels[3];
lv_obj_t *stageNameLabels[3];
lv_obj_t *stageStateLabels[3];
lv_obj_t *stageTopLines[3];
lv_obj_t *thumbnailSlots[3];
lv_obj_t *masterArc;
lv_obj_t *masterValueLabel;
lv_obj_t *masterCaption;
lv_obj_t *activeStageLabel;
lv_obj_t *runDetailLabel;
lv_obj_t *renderMetaStrip;
lv_obj_t *modelLabel;
lv_obj_t *renderSpecsLabel;
lv_obj_t *clockCaption;
lv_obj_t *clockValueLabel;
lv_obj_t *renderTimeCaption;
lv_obj_t *renderTimeValueLabel;
lv_obj_t *metricValueLabels[5];
lv_obj_t *metricBars[5];
lv_obj_t *metricBarFills[5];
lv_obj_t *leftRail;
lv_obj_t *railQueueValue;
lv_obj_t *railModelValue;
lv_obj_t *modalityIconBars[3];

String wifiSsid;
String wifiPassword;
String bridgeUrl = kDefaultBridgeUrl;
String lastMode = "offline";
String lastPromptId;
uint32_t lastPollAt = 0;
uint32_t lastPreviewAt = 0;
uint32_t lastReconnectAt = 0;
int activeStageIndex = -1;
bool hasLivePreview = false;
String recentMediaIds[3];
bool thumbnailsDirty = true;
uint32_t thumbnailRetryAt = 0;
int16_t jpegDrawX = 0;
int16_t jpegDrawY = 0;
uint16_t jpegDrawWidth = kScreenWidth;
uint16_t jpegDrawHeight = kScreenHeight;

enum class TakeoverKind : uint8_t { None, Still, Video, Audio };
TakeoverKind takeoverKind = TakeoverKind::None;
String takeoverMediaId;
String bridgeInstanceEpoch;
uint64_t lastCompletionSequence = 0;
uint8_t takeoverFrame = 0;
uint8_t takeoverFrameCount = 0;
uint8_t takeoverLoops = 0;
uint32_t takeoverStartedAt = 0;
uint32_t takeoverNextFrameAt = 0;
uint32_t takeoverFrameIntervalMs = 500;

std::vector<uint8_t> jpegBytes;

struct CompletedDescriptor {
  String mediaId;
  String kind;
  int frames = 0;
  int loopCount = 0;
  uint32_t frameIntervalMs = 500;
};

CompletedDescriptor pendingMedia;
uint64_t pendingCompletionSequence = 0;
uint8_t pendingTakeoverAttempts = 0;
uint32_t pendingTakeoverRetryAt = 0;

void flushDisplay(lv_disp_drv_t *display, const lv_area_t *area, lv_color_t *pixels) {
  const uint32_t width = area->x2 - area->x1 + 1;
  const uint32_t height = area->y2 - area->y1 + 1;
  tft.startWrite();
  tft.setAddrWindow(area->x1, area->y1, width, height);
  tft.pushColors(reinterpret_cast<uint16_t *>(pixels), width * height, true);
  tft.endWrite();
  lv_disp_flush_ready(display);
}

lv_obj_t *makeBox(lv_obj_t *parent, int x, int y, int width, int height, uint32_t color) {
  lv_obj_t *object = lv_obj_create(parent);
  lv_obj_set_pos(object, x, y);
  lv_obj_set_size(object, width, height);
  lv_obj_set_style_bg_color(object, lv_color_hex(color), 0);
  lv_obj_set_style_bg_opa(object, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(object, 0, 0);
  lv_obj_set_style_radius(object, 0, 0);
  lv_obj_set_style_pad_all(object, 0, 0);
  lv_obj_clear_flag(object, LV_OBJ_FLAG_SCROLLABLE);
  return object;
}

lv_obj_t *makeLabel(lv_obj_t *parent, int x, int y, int width, const lv_font_t *font,
                    uint32_t color, lv_text_align_t alignment) {
  lv_obj_t *label = lv_label_create(parent);
  lv_obj_set_pos(label, x, y);
  lv_obj_set_width(label, width);
  lv_obj_set_style_text_font(label, font, 0);
  lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
  lv_obj_set_style_text_align(label, alignment, 0);
  lv_obj_set_style_bg_opa(label, LV_OPA_TRANSP, 0);
  return label;
}

lv_obj_t *makeArc(lv_obj_t *parent, int x, int y, int size, int width) {
  lv_obj_t *arc = lv_arc_create(parent);
  lv_obj_set_pos(arc, x, y);
  lv_obj_set_size(arc, size, size);
  lv_arc_set_range(arc, 0, 100);
  lv_arc_set_rotation(arc, 270);
  lv_arc_set_bg_angles(arc, 0, 360);
  lv_arc_set_value(arc, 0);
  lv_obj_set_style_arc_width(arc, width, LV_PART_MAIN);
  lv_obj_set_style_arc_width(arc, width, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(arc, lv_color_hex(kTrack), LV_PART_MAIN);
  lv_obj_set_style_arc_color(arc, lv_color_hex(kOrange), LV_PART_INDICATOR);
  lv_obj_remove_style(arc, nullptr, LV_PART_KNOB);
  lv_obj_clear_flag(arc, LV_OBJ_FLAG_CLICKABLE);
  return arc;
}

uint16_t triangleWave(uint32_t now, uint32_t period, uint16_t amplitude) {
  const uint32_t phase = now % period;
  const uint32_t half = period / 2;
  const uint32_t ramp = phase <= half ? phase : period - phase;
  return static_cast<uint16_t>((ramp * amplitude) / half);
}

void setObjectColor(lv_obj_t *object, uint32_t color) {
  lv_obj_set_style_bg_color(object, lv_color_hex(color), 0);
}

void setLabelColor(lv_obj_t *label, uint32_t color) {
  lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
}

void styleStage(size_t index, const char *state, int percent) {
  const bool complete = strcmp(state, "complete") == 0;
  const bool active = strcmp(state, "active") == 0;
  const uint32_t accent = complete ? kDone : (active ? kOrange : kTrack);
  setObjectColor(stageBays[index], active ? 0x241D17 : kSurface2);
  setObjectColor(stageTopLines[index], accent);
  setLabelColor(stageIndexLabels[index], complete ? kDoneText : (active ? kOrange : kSecondary));
  setLabelColor(stageNameLabels[index], active ? kText : kSecondary);
  setLabelColor(stageStateLabels[index], complete ? kDoneText : (active ? kOrange : kMuted));
  setLabelColor(stageRingLabels[index], complete ? kDoneText : (active ? kText : kSecondary));
  char valueText[12];
  snprintf(valueText, sizeof(valueText), complete ? "Done" : "%d%%", percent);
  lv_label_set_text(stageRingLabels[index], valueText);
  lv_label_set_text(stageStateLabels[index], complete ? "Complete" : (active ? "Active" : "Waiting"));
}

void showProcessRack() {
  hasLivePreview = false;
}

void showPreviewPanel() {
  // Recent-completion thumbnails own the right column. Live previews no longer
  // replace progress instruments on this compact layout.
  hasLivePreview = false;
}

void buildUi() {
  lv_obj_t *screen = lv_scr_act();
  lv_obj_set_style_bg_color(screen, lv_color_hex(kInk), 0);
  lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
  lv_obj_set_style_pad_all(screen, 0, 0);
  lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);

  clockCaption = makeLabel(screen, 18, 11, 36, &instrument_sans_12, kMuted, LV_TEXT_ALIGN_LEFT);
  lv_label_set_text(clockCaption, "Clock");
  clockValueLabel = makeLabel(screen, 57, 11, 62, &instrument_sans_12, kText, LV_TEXT_ALIGN_LEFT);
  lv_label_set_text(clockValueLabel, "--:--");
  renderTimeCaption = makeLabel(screen, 326, 11, 78, &instrument_sans_12, kMuted, LV_TEXT_ALIGN_RIGHT);
  lv_label_set_text(renderTimeCaption, "Render time");
  renderTimeValueLabel = makeLabel(screen, 407, 11, 55, &instrument_sans_12, kText, LV_TEXT_ALIGN_RIGHT);
  lv_label_set_text(renderTimeValueLabel, "--:--");

  mainStage = makeBox(screen, kV2MainStageX, kMainStageY, kV2MainStageWidth, kMainStageHeight, kSurface);
  processRack = makeBox(mainStage, kStageLinesX, kStageLinesY,
                        kStageLineWidth, 3 * kStageLineHeight + 4, kSurface);

  const char *defaultNames[3] = {"Prepare", "Generate", "Finish"};
  for (size_t index = 0; index < 3; ++index) {
    const int y = index * (kStageLineHeight + 2);
    stageBays[index] = makeBox(processRack, 0, y, kStageLineWidth, kStageLineHeight, kSurface2);
    stageTopLines[index] = makeBox(stageBays[index], 0, 0, 3, kStageLineHeight, kTrack);
    stageIndexLabels[index] = makeLabel(stageBays[index], 8, 4, 14,
                                        &instrument_sans_12, kSecondary, LV_TEXT_ALIGN_LEFT);
    char indexText[2] = {static_cast<char>('1' + index), '\0'};
    lv_label_set_text(stageIndexLabels[index], indexText);
    stageNameLabels[index] = makeLabel(stageBays[index], 26, 4, 74,
                                       &instrument_sans_12, kSecondary, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(stageNameLabels[index], defaultNames[index]);
    stageStateLabels[index] = makeLabel(stageBays[index], 102, 4, 62,
                                        &instrument_sans_12, kMuted, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(stageStateLabels[index], "Waiting");
    stageRingLabels[index] = makeLabel(stageBays[index], 164, 4, 38,
                                       &instrument_sans_12, kSecondary, LV_TEXT_ALIGN_RIGHT);
    lv_label_set_text(stageRingLabels[index], "0%");
  }

  masterArc = makeArc(mainStage, kMasterX, kMasterY, kMasterRingSize, 5);
  lv_obj_t *masterHub = makeBox(mainStage, 86, 37, 58, 58, kSurface);
  lv_obj_set_style_radius(masterHub, LV_RADIUS_CIRCLE, 0);
  masterValueLabel = makeLabel(mainStage, 86, 43, 58, &instrument_sans_29, kText, LV_TEXT_ALIGN_CENTER);
  lv_label_set_text(masterValueLabel, "0%");
  masterCaption = makeLabel(mainStage, 86, 86, 58, &instrument_sans_12, kSecondary, LV_TEXT_ALIGN_CENTER);
  lv_label_set_text(masterCaption, "Master flow");
  activeStageLabel = makeLabel(mainStage, 10, 112, 210, &instrument_sans_12, kSecondary, LV_TEXT_ALIGN_CENTER);
  lv_label_set_text(activeStageLabel, "Waiting");

  for (size_t index = 0; index < 3; ++index) {
    thumbnailSlots[index] = makeBox(
        mainStage, 230, index * (kThumbnailHeight + kThumbnailGap),
        kThumbnailWidth, kThumbnailHeight, kSurface2);
    lv_obj_set_style_border_width(thumbnailSlots[index], 1, 0);
    lv_obj_set_style_border_color(thumbnailSlots[index], lv_color_hex(kTrack), 0);
  }

  // Retained hidden targets keep the bounded metadata updater simple; model
  // identity remains visible in the exact three-item left rail.
  runDetailLabel = makeLabel(mainStage, 0, 0, 1, &instrument_sans_12, kSecondary, LV_TEXT_ALIGN_LEFT);
  modelLabel = makeLabel(mainStage, 0, 0, 1, &instrument_sans_12, kText, LV_TEXT_ALIGN_LEFT);
  renderSpecsLabel = makeLabel(mainStage, 0, 0, 1, &instrument_sans_12, kText, LV_TEXT_ALIGN_LEFT);
  lv_obj_add_flag(runDetailLabel, LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(modelLabel, LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(renderSpecsLabel, LV_OBJ_FLAG_HIDDEN);

  const char *metricNames[5] = {"GPU", "CPU", "VRAM", "RAM", "GPU °C"};
  const uint32_t metricColors[5] = {kLime, kOrange, kBlue, kPaper, kOrange};
  for (size_t index = 0; index < 5; ++index) {
    const int x = 18 + index * 91;
    lv_obj_t *caption = makeLabel(screen, x, 256, 80, &instrument_sans_12, kMuted, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(caption, metricNames[index]);
    metricValueLabels[index] = makeLabel(screen, x, 270, 80, &instrument_sans_12, kText, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(metricValueLabels[index], "--");
    metricBars[index] = makeBox(screen, x, 286, 80, 4, kTrack);
    lv_obj_set_size(metricBars[index], 80, 4);
    metricBarFills[index] = makeBox(metricBars[index], 0, 0, 0, 4, metricColors[index]);
  }

  // V2's left rail contains exactly queue, curated model name, and a
  // firmware-owned modality icon. There is intentionally no modality label.
  leftRail = makeBox(screen, 0, 45, 92, 203, 0x151813);
  lv_obj_t *queueCaption = makeLabel(leftRail, 10, 10, 72, &instrument_sans_12, kMuted, LV_TEXT_ALIGN_LEFT);
  lv_label_set_text(queueCaption, "Queue");
  railQueueValue = makeLabel(leftRail, 10, 28, 72, &instrument_sans_29, kText, LV_TEXT_ALIGN_LEFT);
  lv_label_set_text(railQueueValue, "0");
  railModelValue = makeLabel(leftRail, 10, 82, 72, &instrument_sans_12, kText, LV_TEXT_ALIGN_LEFT);
  lv_label_set_long_mode(railModelValue, LV_LABEL_LONG_DOT);
  lv_label_set_text(railModelValue, "Unknown model");
  for (size_t index = 0; index < 3; ++index) {
    modalityIconBars[index] = makeBox(leftRail, 10 + index * 10, 154, 6, 20, kMuted);
  }
}

void updateRail(JsonObjectConst rail) {
  const int running = rail["queue"]["running"] | 0;
  const int pending = rail["queue"]["pending"] | 0;
  char queueText[12];
  snprintf(queueText, sizeof(queueText), "%d", running + pending);
  lv_label_set_text(railQueueValue, queueText);
  const char *model = rail["model_name"] | "Unknown model";
  lv_label_set_text(railModelValue, model);
  const char *icon = rail["modality_icon"] | "unknown";
  if (strcmp(icon, "audio") == 0) {
    const int heights[3] = {10, 24, 16};
    for (size_t index = 0; index < 3; ++index) {
      lv_obj_set_pos(modalityIconBars[index], 10 + index * 10, 164 - heights[index] / 2);
      lv_obj_set_size(modalityIconBars[index], 6, heights[index]);
      setObjectColor(modalityIconBars[index], kLime);
    }
  } else if (strcmp(icon, "video") == 0) {
    for (size_t index = 0; index < 3; ++index) {
      lv_obj_set_pos(modalityIconBars[index], 10 + index * 8, 154 + index * 4);
      lv_obj_set_size(modalityIconBars[index], 8, 16 - index * 4);
      setObjectColor(modalityIconBars[index], kOrange);
    }
  } else {
    for (size_t index = 0; index < 3; ++index) {
      lv_obj_set_pos(modalityIconBars[index], 10, 152 + index * 9);
      lv_obj_set_size(modalityIconBars[index], index == 1 ? 26 : 20, 4);
      setObjectColor(modalityIconBars[index], strcmp(icon, "image") == 0 ? kBlue : kMuted);
    }
  }
}

void formatElapsed(uint32_t elapsedMs, char *buffer, size_t size) {
  const uint32_t seconds = elapsedMs / 1000;
  const uint32_t hours = seconds / 3600;
  const uint32_t minutes = (seconds % 3600) / 60;
  const uint32_t remaining = seconds % 60;
  if (hours > 0) {
    snprintf(buffer, size, "%lu:%02lu:%02lu", static_cast<unsigned long>(hours),
             static_cast<unsigned long>(minutes), static_cast<unsigned long>(remaining));
  } else {
    snprintf(buffer, size, "%02lu:%02lu", static_cast<unsigned long>(minutes),
             static_cast<unsigned long>(remaining));
  }
}

void updateMetric(size_t index, int percent, const char *value) {
  percent = std::max(0, std::min(100, percent));
  lv_label_set_text(metricValueLabels[index], value);
  lv_obj_set_width(metricBarFills[index], percent * 80 / 100);
}

void updatePipeline(JsonObjectConst pipeline) {
  const int masterPercent = std::max(0, std::min(100, pipeline["master_percent"] | 0));
  activeStageIndex = pipeline["active_stage"] | 0;
  lv_arc_set_value(masterArc, masterPercent);
  char percentText[8];
  snprintf(percentText, sizeof(percentText), "%d%%", masterPercent);
  lv_label_set_text(masterValueLabel, percentText);
  lv_label_set_text(masterCaption, "Master flow");

  JsonArrayConst stages = pipeline["stages"].as<JsonArrayConst>();
  for (size_t index = 0; index < 3; ++index) {
    JsonObjectConst stage = stages[index].as<JsonObjectConst>();
    const char *name = stage["name"] | "Stage";
    const char *state = stage["state"] | "upcoming";
    const int percent = stage["percent"] | 0;
    lv_label_set_text(stageNameLabels[index], name);
    styleStage(index, state, percent);
  }

  const char *profile = pipeline["profile"] | "t2i";
  JsonObjectConst metadata = pipeline["metadata"].as<JsonObjectConst>();
  JsonObjectConst step = pipeline["step"].as<JsonObjectConst>();
  const char *model = metadata["model"] | "Active model";
  lv_label_set_text(modelLabel, model);
  const int width = metadata["width"] | 0;
  const int height = metadata["height"] | 0;
  const int duration = metadata["duration_seconds"] | 0;
  const int fps = metadata["fps"] | 0;
  const int steps = metadata["steps"] | 0;
  char specs[96];
  if (strcmp(profile, "h3") == 0 && width > 0 && height > 0 && duration > 0 && fps > 0) {
    snprintf(specs, sizeof(specs), "%dx%d · %ds · %d fps", width, height, duration, fps);
  } else if (width > 0 && height > 0 && steps > 0) {
    snprintf(specs, sizeof(specs), "%dx%d · %d steps", width, height, steps);
  } else if (width > 0 && height > 0) {
    snprintf(specs, sizeof(specs), "%dx%d", width, height);
  } else {
    snprintf(specs, sizeof(specs), "Render metadata pending");
  }
  lv_label_set_text(renderSpecsLabel, specs);

  char detail[64];
  if (steps > 0) {
    snprintf(detail, sizeof(detail), "%s · %d steps",
             strcmp(profile, "h3") == 0 ? "H3 Creator" : "Image cascade", steps);
  } else {
    snprintf(detail, sizeof(detail), "%s",
             strcmp(profile, "h3") == 0 ? "H3 Creator" : "Image cascade");
  }
  lv_label_set_text(runDetailLabel, detail);

  const char *activeName = "Stage";
  if (activeStageIndex >= 0 && activeStageIndex < 3) {
    JsonObjectConst activeStage = stages[activeStageIndex].as<JsonObjectConst>();
    activeName = activeStage["name"] | "Stage";
  }
  const int value = step["value"] | 0;
  const int maximum = step["max"] | 0;
  char activeCaption[64];
  if (maximum > 0) {
    snprintf(activeCaption, sizeof(activeCaption), "%s   %d / %d", activeName, value, maximum);
  } else {
    snprintf(activeCaption, sizeof(activeCaption), "%s", activeName);
  }
  lv_label_set_text(activeStageLabel, activeCaption);

  const uint32_t elapsedMs = pipeline["elapsed_ms"] | 0;
  char elapsed[16];
  formatElapsed(elapsedMs, elapsed, sizeof(elapsed));
  lv_label_set_text(renderTimeValueLabel, elapsed);
}

void showEmptyPipeline(const char *message) {
  activeStageIndex = -1;
  lv_arc_set_value(masterArc, 0);
  lv_label_set_text(masterValueLabel, "0%");
  lv_label_set_text(activeStageLabel, message);
  lv_label_set_text(runDetailLabel, "Waiting for pipeline");
  for (size_t index = 0; index < 3; ++index) styleStage(index, "upcoming", 0);
}

void updateTelemetry(JsonDocument &document) {
  char value[20];
  const int gpuPercent = document["gpu"]["utilization_percent"] | 0;
  snprintf(value, sizeof(value), "%d%%", gpuPercent);
  updateMetric(0, gpuPercent, value);

  const int cpuPercentValue = document["system"]["cpu_percent"] | 0;
  snprintf(value, sizeof(value), "%d%%", cpuPercentValue);
  updateMetric(1, cpuPercentValue, value);

  const int vramUsed = document["gpu"]["memory_used_mib"] | 0;
  const int vramTotal = document["gpu"]["memory_total_mib"] | 0;
  const int vramPercent = vramTotal > 0 ? vramUsed * 100 / vramTotal : 0;
  snprintf(value, sizeof(value), "%.1fG", vramUsed / 1024.0f);
  updateMetric(2, vramPercent, value);

  const uint64_t ramUsed = document["system"]["ram_used_bytes"] | static_cast<uint64_t>(0);
  const uint64_t ramTotal = document["system"]["ram_total_bytes"] | static_cast<uint64_t>(0);
  const int ramPercent = ramTotal > 0 ? static_cast<int>(ramUsed * 100 / ramTotal) : 0;
  snprintf(value, sizeof(value), "%.1fG", ramUsed / 1073741824.0f);
  updateMetric(3, ramPercent, value);

  const int temperature = document["gpu"]["temperature_c"] | 0;
  snprintf(value, sizeof(value), "%d°", temperature);
  updateMetric(4, temperature * 100 / 90, value);
}

void updateTelemetryV2(JsonDocument &document) {
  char value[20];
  JsonObjectConst telemetry = document["telemetry"].as<JsonObjectConst>();
  const int gpuPercent = telemetry["gpu"]["utilization_percent"] | 0;
  snprintf(value, sizeof(value), "%d%%", gpuPercent);
  updateMetric(0, gpuPercent, value);
  const int cpuPercentValue = telemetry["system"]["cpu_percent"] | 0;
  snprintf(value, sizeof(value), "%d%%", cpuPercentValue);
  updateMetric(1, cpuPercentValue, value);
  const int vramUsed = telemetry["gpu"]["memory_used_mib"] | 0;
  const int vramTotal = telemetry["gpu"]["memory_total_mib"] | 0;
  snprintf(value, sizeof(value), "%.1fG", vramUsed / 1024.0f);
  updateMetric(2, vramTotal > 0 ? vramUsed * 100 / vramTotal : 0, value);
  const uint64_t ramUsed = telemetry["system"]["ram_used_bytes"] | static_cast<uint64_t>(0);
  const uint64_t ramTotal = telemetry["system"]["ram_total_bytes"] | static_cast<uint64_t>(0);
  snprintf(value, sizeof(value), "%.1fG", ramUsed / 1073741824.0f);
  updateMetric(3, ramTotal > 0 ? static_cast<int>(ramUsed * 100 / ramTotal) : 0, value);
  const int temperature = telemetry["gpu"]["temperature_c"] | 0;
  snprintf(value, sizeof(value), "%d°", temperature);
  updateMetric(4, temperature * 100 / 90, value);
}

bool thumbnailJpegBlock(int16_t x, int16_t y, uint16_t width, uint16_t height,
                        uint16_t *bitmap) {
  if (x >= jpegDrawWidth || y >= jpegDrawHeight) return false;
  const uint16_t drawWidth = std::min<uint16_t>(width, jpegDrawWidth - x);
  const uint16_t drawHeight = std::min<uint16_t>(height, jpegDrawHeight - y);
  tft.pushImage(jpegDrawX + x, jpegDrawY + y, drawWidth, drawHeight, bitmap);
  return true;
}

bool validMediaId(const String &mediaId) {
  if (mediaId.length() != 16) return false;
  for (size_t index = 0; index < mediaId.length(); ++index) {
    const char value = mediaId[index];
    if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f'))) {
      return false;
    }
  }
  return true;
}

bool fetchThumbnail(size_t slot, const String &mediaId) {
  if (slot >= 3 || !validMediaId(mediaId) || WiFi.status() != WL_CONNECTED) return false;
  HTTPClient request;
  request.setTimeout(2500);
  const String url = bridgeUrl + "/v2/media/" + mediaId + "/thumb.jpg";
  if (!request.begin(url)) return false;
  const int status = request.GET();
  const int length = request.getSize();
  if (status != HTTP_CODE_OK || length <= 0 ||
      static_cast<size_t>(length) > kMaxThumbnailBytes) {
    request.end();
    return false;
  }
  jpegBytes.resize(length);
  WiFiClient *stream = request.getStreamPtr();
  size_t offset = 0;
  const uint32_t deadline = millis() + 3000;
  while (offset < jpegBytes.size() && millis() < deadline) {
    const size_t available = stream->available();
    if (available) {
      offset += stream->readBytes(jpegBytes.data() + offset,
                                  std::min(available, jpegBytes.size() - offset));
    } else {
      delay(1);
    }
  }
  request.end();
  if (offset != jpegBytes.size()) {
    std::vector<uint8_t>().swap(jpegBytes);
    return false;
  }
  jpegDrawX = kThumbnailX;
  jpegDrawY = kThumbnailY + slot * (kThumbnailHeight + kThumbnailGap);
  jpegDrawWidth = kThumbnailWidth;
  jpegDrawHeight = kThumbnailHeight;
  TJpgDec.setJpgScale(1);
  TJpgDec.setCallback(thumbnailJpegBlock);
  const bool decoded =
      TJpgDec.drawJpg(0, 0, jpegBytes.data(), jpegBytes.size()) == JDR_OK;
  std::vector<uint8_t>().swap(jpegBytes);
  return decoded;
}

void updateRecentMedia(JsonArrayConst recent) {
  bool changed = false;
  for (size_t index = 0; index < 3; ++index) {
    String nextId;
    if (recent.size() <= 3 && index < recent.size()) {
      JsonObjectConst item = recent[index].as<JsonObjectConst>();
      nextId = String(item["id"] | "");
      const String kind = String(item["kind"] | "");
      if (!validMediaId(nextId) ||
          (kind != "image" && kind != "video" && kind != "audio")) {
        nextId = "";
      }
    }
    if (nextId != recentMediaIds[index]) {
      recentMediaIds[index] = nextId;
      changed = true;
    }
  }
  if (changed) {
    for (lv_obj_t *slot : thumbnailSlots) lv_obj_invalidate(slot);
    thumbnailsDirty = true;
    thumbnailRetryAt = 0;
  }
}

bool drawRecentThumbnails() {
  lv_refr_now(nullptr);
  bool complete = true;
  for (size_t index = 0; index < 3; ++index) {
    if (recentMediaIds[index].isEmpty()) continue;
    if (!fetchThumbnail(index, recentMediaIds[index])) complete = false;
  }
  return complete;
}

bool fullScreenJpegBlock(int16_t x, int16_t y, uint16_t width, uint16_t height,
                         uint16_t *bitmap) {
  if (x >= kScreenWidth || y >= kScreenHeight) return false;
  const uint16_t drawWidth = std::min<uint16_t>(width, kScreenWidth - x);
  const uint16_t drawHeight = std::min<uint16_t>(height, kScreenHeight - y);
  tft.pushImage(x, y, drawWidth, drawHeight, bitmap);
  return true;
}

bool fetchTakeoverFrame(uint8_t frame) {
  if (WiFi.status() != WL_CONNECTED || takeoverMediaId.length() != 16) return false;
  const String url = bridgeUrl + "/v2/media/" + takeoverMediaId + "/frame/" + String(frame) + ".jpg";
  HTTPClient request;
  request.setTimeout(3500);
  if (!request.begin(url)) return false;
  const int status = request.GET();
  const int length = request.getSize();
  if (status != HTTP_CODE_OK || length <= 0 || static_cast<size_t>(length) > kMaxPreviewBytes) {
    request.end();
    return false;
  }
  jpegBytes.resize(length);  // The sole JPEG buffer serves live and completed media.
  WiFiClient *stream = request.getStreamPtr();
  size_t offset = 0;
  const uint32_t deadline = millis() + 4000;
  while (offset < jpegBytes.size() && millis() < deadline) {
    const size_t available = stream->available();
    if (available) {
      offset += stream->readBytes(jpegBytes.data() + offset,
                                  std::min(available, jpegBytes.size() - offset));
    } else {
      delay(1);
    }
  }
  request.end();
  if (offset != jpegBytes.size()) {
    std::vector<uint8_t>().swap(jpegBytes);
    return false;
  }
  TJpgDec.setJpgScale(1);
  TJpgDec.setCallback(fullScreenJpegBlock);
  const bool decoded =
      TJpgDec.drawJpg(0, 0, jpegBytes.data(), jpegBytes.size()) == JDR_OK;
  std::vector<uint8_t>().swap(jpegBytes);
  return decoded;
}

void endTakeover() {
  takeoverKind = TakeoverKind::None;
  takeoverMediaId = "";
  thumbnailsDirty = true;
  thumbnailRetryAt = 0;
  lv_obj_invalidate(lv_scr_act());
  lv_refr_now(nullptr);
}

bool beginTakeover(const CompletedDescriptor &media) {
  if (!validMediaId(media.mediaId) || media.frames < 1 || media.frames > kMaxCompletedFrames) return false;
  if (media.kind != "image" && media.kind != "video" && media.kind != "audio") return false;
  if (media.kind == "video" && media.loopCount != kCompletedVideoLoops) return false;
  takeoverKind = media.kind == "video"
                     ? TakeoverKind::Video
                     : (media.kind == "audio" ? TakeoverKind::Audio : TakeoverKind::Still);
  takeoverMediaId = media.mediaId;
  takeoverFrameCount = static_cast<uint8_t>(media.frames);
  takeoverFrame = 0;
  takeoverLoops = 0;
  takeoverFrameIntervalMs = std::max<uint32_t>(40, media.frameIntervalMs);
  tft.fillScreen(TFT_BLACK);
  if (!fetchTakeoverFrame(0)) {
    endTakeover();
    return false;
  }
  // Hold and animation timing starts only after a complete JPEG draw succeeds.
  takeoverStartedAt = millis();
  takeoverNextFrameAt = takeoverStartedAt + takeoverFrameIntervalMs;
  return true;
}

void advanceTakeover(uint32_t now) {
  if (takeoverKind == TakeoverKind::None) return;
  if (takeoverKind != TakeoverKind::Video) {
    if (now - takeoverStartedAt >= kCompletedCardHoldMs) endTakeover();
    return;
  }
  if (static_cast<int32_t>(now - takeoverNextFrameAt) < 0) return;
  takeoverNextFrameAt = now + takeoverFrameIntervalMs;
  ++takeoverFrame;
  if (takeoverFrame >= takeoverFrameCount) {
    takeoverFrame = 0;
    ++takeoverLoops;
    if (takeoverLoops >= kCompletedVideoLoops) {
      endTakeover();
      return;
    }
  }
  if (!fetchTakeoverFrame(takeoverFrame)) endTakeover();
}

int hexNibble(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  return -1;
}

bool decodeHexField(const String &encoded, String &decoded, size_t maxBytes) {
  if ((encoded.length() % 2) != 0 || encoded.length() / 2 > maxBytes) return false;
  decoded = "";
  decoded.reserve(encoded.length() / 2);
  for (size_t index = 0; index < encoded.length(); index += 2) {
    const int high = hexNibble(encoded[index]);
    const int low = hexNibble(encoded[index + 1]);
    if (high < 0 || low < 0) return false;
    decoded += static_cast<char>((high << 4) | low);
  }
  return true;
}

void handleSerialProvisioning() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (!line.startsWith("RB1\t")) return;
  const int first = line.indexOf('\t');
  const int second = line.indexOf('\t', first + 1);
  const int third = line.indexOf('\t', second + 1);
  if (first < 0 || second < 0 || third < 0 || line.indexOf('\t', third + 1) >= 0) return;
  String ssid;
  String password;
  String bridge;
  if (!decodeHexField(line.substring(first + 1, second), ssid, 32) ||
      !decodeHexField(line.substring(second + 1, third), password, 64) ||
      !decodeHexField(line.substring(third + 1), bridge, 128) || ssid.isEmpty() ||
      !bridge.startsWith("http://")) {
    return;
  }
  preferences.begin("renderbeacon", false);
  preferences.putString("wifi_ssid", ssid);
  preferences.putString("wifi_password", password);
  preferences.putString("bridge_url", bridge);
  preferences.end();
  Serial.println("RB1 OK");
  Serial.flush();
  delay(150);
  ESP.restart();
}

bool readBoundedBody(HTTPClient &request, String &body, size_t maxBytes) {
  const int declared = request.getSize();
  if (declared > static_cast<int>(maxBytes)) return false;
  body = "";
  body.reserve(declared > 0 ? declared : 1024);
  WiFiClient *stream = request.getStreamPtr();
  uint8_t chunk[256];
  size_t received = 0;
  const uint32_t deadline = millis() + 3500;
  while ((declared >= 0 ? received < static_cast<size_t>(declared) : stream->connected()) &&
         static_cast<int32_t>(deadline - millis()) > 0) {
    const size_t available = stream->available();
    if (!available) {
      delay(1);
      continue;
    }
    const size_t count = stream->readBytes(
        chunk, std::min(sizeof(chunk), available));
    if (count == 0) continue;
    if (received + count > maxBytes) return false;
    body.concat(reinterpret_cast<const char *>(chunk), count);
    received += count;
  }
  return received <= maxBytes &&
         (declared < 0 ? !stream->connected() : received == static_cast<size_t>(declared));
}

void clearPendingCompletion() {
  pendingMedia = CompletedDescriptor();
  pendingCompletionSequence = 0;
  pendingTakeoverAttempts = 0;
  pendingTakeoverRetryAt = 0;
}

void rebaseCompletionEpoch(const String &epoch, uint64_t sequence) {
  bridgeInstanceEpoch = epoch;
  lastCompletionSequence = sequence;
  // The bridge may rediscover historical output after a restart. Baseline the
  // new sequence space instead of replaying whatever happened to be current.
  clearPendingCompletion();
}

void retainNewestReadyCompletion(uint64_t sequence, JsonObjectConst completed) {
  if (sequence <= lastCompletionSequence || sequence <= pendingCompletionSequence) return;
  pendingMedia.mediaId = String(completed["id"] | "");
  pendingMedia.kind = String(completed["kind"] | "");
  pendingMedia.frames = completed["frame_count"] | 0;
  pendingMedia.loopCount = completed["loop_count"] | 0;
  pendingMedia.frameIntervalMs = completed["frame_interval_ms"] | 500;
  pendingCompletionSequence = sequence;
  pendingTakeoverAttempts = 0;
  pendingTakeoverRetryAt = 0;
}

void tryPendingTakeover(uint32_t now) {
  if (pendingCompletionSequence == 0 || takeoverKind != TakeoverKind::None ||
      lastMode == "running" || static_cast<int32_t>(now - pendingTakeoverRetryAt) < 0) {
    return;
  }
  if (beginTakeover(pendingMedia)) {
    // A completion is acknowledged only after frame zero has fetched and
    // drawn successfully. Later polls therefore cannot replay this takeover.
    lastCompletionSequence = pendingCompletionSequence;
    clearPendingCompletion();
    return;
  }
  ++pendingTakeoverAttempts;
  if (pendingTakeoverAttempts >= kMaxTakeoverStartAttempts) {
    // Deliberately abandon a persistently invalid/unavailable derivative.
    // This bounds retries while allowing transient manifest/fetch failures.
    lastCompletionSequence = pendingCompletionSequence;
    clearPendingCompletion();
  } else {
    pendingTakeoverRetryAt = now + kTakeoverRetryIntervalMs;
  }
}

void updateState() {
  {
    HTTPClient request;
    request.setTimeout(3000);
    if (!request.begin(bridgeUrl + "/v2/state")) return;
    const int status = request.GET();
    if (status != HTTP_CODE_OK) {
      request.end();
      lastMode = "offline";
      showProcessRack();
      showEmptyPipeline("bridge unavailable");
      return;
    }
    String payload;
    if (!readBoundedBody(request, payload, kMaxStateBytes)) {
      request.end();
      lastMode = "offline";
      showProcessRack();
      showEmptyPipeline("bridge state too large");
      return;
    }
    request.end();
    DynamicJsonDocument stateDocument(kMaxStateBytes);
    const DeserializationError error = deserializeJson(stateDocument, payload);
    payload = String();
    if (error) {
      lastMode = "offline";
      showProcessRack();
      showEmptyPipeline("invalid bridge state");
      return;
    }

    if ((stateDocument["schema_version"] | 0) != 2) {
      lastMode = "offline";
      showProcessRack();
      showEmptyPipeline("unsupported bridge schema");
      return;
    }
    lastMode = String(stateDocument["mode"] | "idle");
    const char *clock = stateDocument["clock"] | "--:--";
    lv_label_set_text(clockValueLabel, clock);
    updateRail(stateDocument["rail"].as<JsonObjectConst>());
    JsonObjectConst pipeline = stateDocument["pipeline"].as<JsonObjectConst>();
    if (!pipeline.isNull()) {
      updatePipeline(pipeline);
    } else {
      showProcessRack();
      showEmptyPipeline(lastMode == "offline" ? "bridge unavailable" : "Waiting");
      lv_label_set_text(renderTimeValueLabel, "--:--");
    }
    updateTelemetryV2(stateDocument);
    updateRecentMedia(stateDocument["recent_media"].as<JsonArrayConst>());

    const String epoch = String(stateDocument["bridge_instance_epoch"] | "");
    const uint64_t sequence = stateDocument["completion_sequence"] | static_cast<uint64_t>(0);
    if (epoch.isEmpty()) {
      lastMode = "offline";
      showProcessRack();
      showEmptyPipeline("invalid bridge epoch");
      return;
    }
    if (bridgeInstanceEpoch.isEmpty() || epoch != bridgeInstanceEpoch) {
      rebaseCompletionEpoch(epoch, sequence);
    } else if (sequence > lastCompletionSequence && sequence > pendingCompletionSequence) {
      const String completionStatus = String(stateDocument["completion_status"] | "none");
      JsonObjectConst completed = stateDocument["completed_media"].as<JsonObjectConst>();
      if (completionStatus == "ready" && !completed.isNull()) {
        // Retain one newest completion even while rendering or while another
        // takeover is active. Eligibility is checked after response release.
        retainNewestReadyCompletion(sequence, completed);
      } else if (completionStatus == "failed") {
        // A terminal conversion failure deliberately consumes the sequence.
        lastCompletionSequence = sequence;
        clearPendingCompletion();
      } else if (sequence > pendingCompletionSequence) {
        // Conversion is still preparing. Drop a superseded descriptor, but do
        // not acknowledge this sequence: the same sequence can become ready.
        clearPendingCompletion();
      }
    }
  }  // JSON document and response storage are released before JPEG allocation.
  tryPendingTakeover(millis());
  const uint32_t now = millis();
  if (takeoverKind == TakeoverKind::None && thumbnailsDirty &&
      static_cast<int32_t>(now - thumbnailRetryAt) >= 0) {
    thumbnailsDirty = !drawRecentThumbnails();
    if (thumbnailsDirty) thumbnailRetryAt = now + kTakeoverRetryIntervalMs;
  }
}

String configPage(const String &message = "") {
  String page = "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>";
  page += "<style>body{font-family:sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem;background:#10120f;color:#efeee7}";
  page += "input{display:block;width:100%;box-sizing:border-box;margin:.4rem 0 1rem;padding:.7rem;background:#252a22;color:#efeee7;border:1px solid #555}";
  page += "button{padding:.7rem 1rem;background:#eb6b45;color:#10120f;border:0}</style></head><body>";
  page += "<h2>Render Beacon setup</h2>";
  if (message.length()) page += "<p>" + message + "</p>";
  page += "<form method='post' action='/save'><label>Wi-Fi SSID</label><input name='ssid' value='" + wifiSsid + "'>";
  page += "<label>Wi-Fi password</label><input name='password' type='password'>";
  page += "<label>Bridge URL</label><input name='bridge' value='" + bridgeUrl + "'>";
  page += "<button type='submit'>Save and restart</button></form></body></html>";
  return page;
}

void startConfigPortal() {
  const uint64_t chip = ESP.getEfuseMac();
  char suffix[7];
  snprintf(suffix, sizeof(suffix), "%06llX", static_cast<unsigned long long>(chip & 0xFFFFFF));
  const String accessPoint = String("RenderBeacon-") + suffix;
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(accessPoint.c_str());
  configServer.on("/", HTTP_GET, [] { configServer.send(200, "text/html", configPage()); });
  configServer.on("/save", HTTP_POST, [] {
    const String ssid = configServer.arg("ssid");
    const String password = configServer.arg("password");
    String bridge = configServer.arg("bridge");
    bridge.trim();
    if (!bridge.startsWith("http://") && !bridge.startsWith("https://")) {
      configServer.send(400, "text/html", configPage("Bridge URL must start with http:// or https://"));
      return;
    }
    preferences.begin("renderbeacon", false);
    preferences.putString("wifi_ssid", ssid);
    if (password.length()) preferences.putString("wifi_password", password);
    preferences.putString("bridge_url", bridge);
    preferences.end();
    configServer.send(200, "text/html", "<p>Saved. Restarting Render Beacon.</p>");
    delay(800);
    ESP.restart();
  });
  configServer.on("/health", HTTP_GET, [] {
    const String body = String("{\"status\":\"ok\",\"wifi_connected\":") +
                        (WiFi.status() == WL_CONNECTED ? "true" : "false") +
                        ",\"bridge\":\"" + bridgeUrl + "\"}";
    configServer.send(200, "application/json", body);
  });
  configServer.begin();
  lv_label_set_text(activeStageLabel, "setup portal active");
  lv_label_set_text(runDetailLabel, accessPoint.c_str());
}

bool loadCredentials() {
  preferences.begin("renderbeacon", true);
  wifiSsid = preferences.getString("wifi_ssid", "");
  wifiPassword = preferences.getString("wifi_password", "");
  bridgeUrl = preferences.getString("bridge_url", kDefaultBridgeUrl);
  preferences.end();
  return wifiSsid.length() > 0;
}

bool connectWiFi(uint32_t timeoutMs) {
  if (!wifiSsid.length()) return false;
  WiFi.mode(WIFI_STA);
  WiFi.begin(wifiSsid.c_str(), wifiPassword.c_str());
  const uint32_t startedAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedAt < timeoutMs) {
    lv_timer_handler();
    delay(20);
  }
  return WiFi.status() == WL_CONNECTED;
}
}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  tft.init();
  tft.setRotation(1);
  tft.setSwapBytes(true);
  tft.fillScreen(TFT_BLACK);

  lv_init();
  lv_disp_draw_buf_init(&drawBuffer, lvBuffer, nullptr, kScreenWidth * 12);
  static lv_disp_drv_t displayDriver;
  lv_disp_drv_init(&displayDriver);
  displayDriver.hor_res = kScreenWidth;
  displayDriver.ver_res = kScreenHeight;
  displayDriver.flush_cb = flushDisplay;
  displayDriver.draw_buf = &drawBuffer;
  lv_disp_drv_register(&displayDriver);

  buildUi();
  showEmptyPipeline("starting");
  lv_timer_handler();

  const bool hasCredentials = loadCredentials();
  if (!hasCredentials || !connectWiFi(15000)) {
    showEmptyPipeline("Wi-Fi setup required");
    startConfigPortal();
  } else {
    updateState();
  }
}

void loop() {
  const uint32_t now = millis();
  handleSerialProvisioning();
  if (takeoverKind == TakeoverKind::None) {
    lv_timer_handler();
  } else {
    advanceTakeover(now);
  }
  configServer.handleClient();

  if (WiFi.status() == WL_CONNECTED) {
    if (now - lastPollAt >= kPollIntervalMs) {
      lastPollAt = now;
      updateState();
    }

  } else if (wifiSsid.length() && now - lastReconnectAt >= kReconnectIntervalMs) {
    lastReconnectAt = now;
    WiFi.disconnect();
    WiFi.begin(wifiSsid.c_str(), wifiPassword.c_str());
    showProcessRack();
    showEmptyPipeline("reconnecting Wi-Fi");
  }

  delay(5);
}
