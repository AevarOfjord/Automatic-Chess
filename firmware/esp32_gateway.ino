/*
 * Dual-SCARA ESP-NOW gateway
 *
 * USB newline JSON -> selected arm, and arm status -> USB newline JSON.
 * Radio delivery is not motion completion: only the arm emits DONE/FAULT.
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <esp_now.h>

#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif

static uint8_t WHITE_ARM_MAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01};
static uint8_t BLACK_ARM_MAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x02};
static constexpr size_t MAX_PACKET_BYTES = 240;

static char serialLine[MAX_PACKET_BYTES + 2];
static size_t serialLength = 0;

static void emitGatewayFault(const char *id, const char *arm, const char *detail) {
  StaticJsonDocument<192> response;
  response["id"] = id;
  response["arm"] = arm;
  response["status"] = "FAULT";
  response["detail"] = detail;
  serializeJson(response, Serial);
  Serial.println();
}

static void forwardArmPacket(const uint8_t *data, int length) {
  if (length <= 0 || length > static_cast<int>(MAX_PACKET_BYTES)) return;
  Serial.write(data, length);
  if (data[length - 1] != '\n') Serial.println();
}

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
static void onArmData(const esp_now_recv_info_t *, const uint8_t *data, int length) {
  forwardArmPacket(data, length);
}
#else
static void onArmData(const uint8_t *, const uint8_t *data, int length) {
  forwardArmPacket(data, length);
}
#endif

static bool addPeer(const uint8_t *address) {
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, address, 6);
  peer.channel = 0;
  peer.encrypt = false;
  return esp_now_add_peer(&peer) == ESP_OK;
}

static void routeLine() {
  serialLine[serialLength] = '\0';
  StaticJsonDocument<512> command;
  DeserializationError error = deserializeJson(command, serialLine, serialLength);
  if (error) {
    emitGatewayFault("", "WHITE", "malformed JSON");
    return;
  }
  const char *id = command["id"] | "";
  const char *arm = command["arm"] | "";
  if (strlen(id) == 0 || (strcmp(arm, "WHITE") != 0 && strcmp(arm, "BLACK") != 0)) {
    emitGatewayFault(id, arm, "missing id or invalid arm");
    return;
  }
  const uint8_t *target = strcmp(arm, "WHITE") == 0 ? WHITE_ARM_MAC : BLACK_ARM_MAC;
  esp_err_t result = esp_now_send(target, reinterpret_cast<uint8_t *>(serialLine), serialLength);
  if (result != ESP_OK) emitGatewayFault(id, arm, "ESP-NOW queue failed");
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  if (esp_now_init() != ESP_OK) {
    emitGatewayFault("", "WHITE", "ESP-NOW initialization failed");
    return;
  }
  esp_now_register_recv_cb(onArmData);
  if (!addPeer(WHITE_ARM_MAC) || !addPeer(BLACK_ARM_MAC)) {
    emitGatewayFault("", "WHITE", "peer registration failed");
  }
}

void loop() {
  while (Serial.available()) {
    char value = static_cast<char>(Serial.read());
    if (value == '\n') {
      if (serialLength > 0) routeLine();
      serialLength = 0;
    } else if (serialLength < MAX_PACKET_BYTES) {
      serialLine[serialLength++] = value;
    } else {
      serialLength = 0;
      emitGatewayFault("", "WHITE", "USB packet exceeds 240 bytes");
    }
  }
}
