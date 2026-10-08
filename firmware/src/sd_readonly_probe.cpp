/*
Render Beacon — one-shot, read-only MicroSD registration diagnostic.
This is built only by the sd-readonly-probe environment; production firmware is excluded.
*/
#include <Arduino.h>
#include <FS.h>
#include <SD.h>
#include <SPI.h>
#include <TFT_eSPI.h>

namespace {
constexpr uint8_t kSdCs = 5;
constexpr uint8_t kSdSck = 18;
constexpr uint8_t kSdMiso = 19;
constexpr uint8_t kSdMosi = 23;
constexpr uint8_t kExternalSpiCs = 21;
constexpr uint32_t kSdFrequencyHz = 1000000U;
constexpr char kMountPoint[] = "/sd";
constexpr uint8_t kMaxOpenFiles = 5;
constexpr char kTestPath[] = "/RB_SD_TEST.TXT";
constexpr size_t kTestPreviewBytes = 40;

static_assert(kSdCs == 5, "Render Beacon MicroSD CS must remain GPIO5");
static_assert(kSdSck == 18 && kSdMiso == 19 && kSdMosi == 23,
              "Render Beacon MicroSD must use the audited HSPI pins");
static_assert(kSdFrequencyHz == 1000000U, "Diagnostic registration must stay at 1 MHz");
static_assert(kMaxOpenFiles == 5, "Diagnostic mount must keep its bounded file limit");
static_assert(kExternalSpiCs == 21, "Shared external SPI connector CS must remain GPIO21");
static_assert(TFT_SCLK == 14 && TFT_MOSI == 13 && TFT_MISO == 12,
              "The TFT must remain on its separate audited SPI bus");
static_assert(TFT_CS != kSdCs && TOUCH_CS != kSdCs,
              "MicroSD CS must be independent of TFT and touch CS");

TFT_eSPI tft;

struct ProbeResult {
  bool mounted = false;
  bool cardRecognized = false;
  bool capacityValid = false;
  bool rootDirectory = false;
  bool testPresent = false;
  bool testReadable = true;
  uint8_t cardType = CARD_NONE;
  uint64_t capacityBytes = 0;
  size_t testBytesRead = 0;
};

const char *cardTypeName(uint8_t type) {
  switch (type) {
    case CARD_MMC:
      return "MMC";
    case CARD_SD:
      return "SDSC";
    case CARD_SDHC:
      return "SDHC/SDXC";
    default:
      return "UNKNOWN";
  }
}

void readOptionalTestFile(ProbeResult &result) {
  if (!SD.exists(kTestPath)) return;

  result.testPresent = true;
  File testFile = SD.open(kTestPath, FILE_READ);
  if (!testFile || testFile.isDirectory()) {
    result.testReadable = false;
    if (testFile) testFile.close();
    return;
  }

  while (result.testBytesRead < kTestPreviewBytes && testFile.available()) {
    const int value = testFile.read();
    if (value < 0) {
      result.testReadable = false;
      break;
    }
    ++result.testBytesRead;
  }
  testFile.close();
}

ProbeResult runProbe() {
  ProbeResult result;

  // De-select the card before configuring its bus. This precedes all SPI/TFT setup.
  pinMode(5, OUTPUT);
  digitalWrite(5, HIGH);
  pinMode(kExternalSpiCs, OUTPUT);
  digitalWrite(kExternalSpiCs, HIGH);
  static SPIClass sdSpi(HSPI);
  sdSpi.begin(18, 19, 23, 5);

  // format_if_empty=false is the hard read-only registration safeguard.
  result.mounted = SD.begin(5, sdSpi, 1000000U, "/sd", 5, false);
  if (!result.mounted) return result;

  result.cardType = SD.cardType();
  result.cardRecognized = result.cardType != CARD_NONE;
  result.capacityBytes = SD.cardSize();
  result.capacityValid = result.capacityBytes > 0;

  File root = SD.open("/", FILE_READ);
  result.rootDirectory = root && root.isDirectory();
  if (root) root.close();

  readOptionalTestFile(result);
  return result;
}

bool passed(const ProbeResult &result) {
  return result.mounted && result.cardRecognized && result.capacityValid &&
         result.rootDirectory && result.testReadable;
}

void drawLine(int16_t y, uint16_t color, const char *label, const char *value) {
  tft.setTextColor(color, TFT_BLACK);
  tft.setCursor(18, y);
  tft.print(label);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setCursor(190, y);
  tft.print(value);
}

void showResult(const ProbeResult &result) {
  const bool success = passed(result);
  const uint16_t statusColor = success ? TFT_GREEN : TFT_RED;
  char value[64];

  tft.fillScreen(TFT_BLACK);
  tft.fillRect(0, 0, 480, 54, statusColor);
  tft.setTextColor(TFT_BLACK, statusColor);
  tft.setTextSize(2);
  tft.setCursor(18, 10);
  tft.print("SD READ-ONLY PROBE");
  tft.setCursor(390, 10);
  tft.print(success ? "PASS" : "FAIL");

  tft.setTextSize(2);
  drawLine(72, result.mounted ? TFT_GREEN : TFT_RED, "Mount /sd", result.mounted ? "OK" : "FAILED");
  snprintf(value, sizeof(value), "%s (type %u)", cardTypeName(result.cardType), result.cardType);
  drawLine(108, result.cardRecognized ? TFT_GREEN : TFT_RED, "Card type", value);
  snprintf(value, sizeof(value), "%llu MiB",
           static_cast<unsigned long long>(result.capacityBytes / (1024ULL * 1024ULL)));
  drawLine(144, result.capacityValid ? TFT_GREEN : TFT_RED, "Capacity", value);
  drawLine(180, result.rootDirectory ? TFT_GREEN : TFT_RED, "Root directory",
           result.rootDirectory ? "OK" : "FAILED");

  if (!result.testPresent) {
    drawLine(216, TFT_YELLOW, "RB_SD_TEST.TXT", "NOT PRESENT (optional)");
  } else if (!result.testReadable) {
    drawLine(216, TFT_RED, "RB_SD_TEST.TXT", "READ FAILED");
  } else {
    snprintf(value, sizeof(value), "READ OK (%u B sampled)",
             static_cast<unsigned>(result.testBytesRead));
    drawLine(216, TFT_GREEN, "RB_SD_TEST.TXT", value);
  }

  tft.setTextSize(1);
  tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  tft.setCursor(18, 284);
  tft.print("One-shot registration only - no format/write/delete paths compiled");
  tft.setCursor(18, 300);
  tft.print("Power-cycle and restore production firmware after recording result");
}
}  // namespace

void setup() {
  // runProbe() asserts SD CS high before either SPI bus or the TFT is initialized.
  const ProbeResult result = runProbe();

  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  tft.init();
  tft.setRotation(1);
  showResult(result);
}

void loop() {
  delay(1000);
}
