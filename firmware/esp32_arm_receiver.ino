/*
 * Dual-SCARA arm controller
 *
 * Required libraries:
 *   ArduinoJson 6.x
 *   FastAccelStepper
 *
 * Set ARM_ID and GATEWAY_MAC independently when flashing each robot.
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <FastAccelStepper.h>
#include <WiFi.h>
#include <esp_now.h>

#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif

#define ARM_ID "WHITE"
static uint8_t GATEWAY_MAC[] = {0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA};

static constexpr int J1_STEP_PIN = 12;
static constexpr int J1_DIR_PIN = 14;
static constexpr int J2_STEP_PIN = 27;
static constexpr int J2_DIR_PIN = 26;
static constexpr int ENABLE_PIN = 13;
static constexpr int J1_HOME_PIN = 32;
static constexpr int J2_HOME_PIN = 35;
static constexpr int ESTOP_PIN = 39;
static constexpr int PICKUP_SENSOR_PIN = 36;
static constexpr int MAGNET_PIN = 23;

// Calibrate these values for the actual reductions and microstepping.
static constexpr float J1_STEPS_PER_DEG = 44.4444f;
static constexpr float J2_STEPS_PER_DEG = 44.4444f;
static constexpr uint32_t HOMING_TIMEOUT_MS = 30000;
static constexpr size_t MAX_PACKET_BYTES = 240;
static constexpr size_t MAX_WAYPOINTS = 4;

struct Waypoint {
  float shoulderDeg;
  float elbowDeg;
  float fixedZCompatibility;
  uint32_t speedHz;
  uint32_t acceleration;
};

enum MotionState { IDLE, HOMING, TRAJECTORY, MAGNET_SETTLING, FAULTED };

FastAccelStepperEngine stepperEngine;
FastAccelStepper *j1 = nullptr;
FastAccelStepper *j2 = nullptr;
MotionState state = IDLE;
Waypoint waypoints[MAX_WAYPOINTS];
size_t waypointCount = 0;
size_t waypointIndex = 0;
uint32_t stateStartedMs = 0;
uint32_t magnetSettleMs = 0;
bool homeDone[2] = {false, false};
char activeId[20] = "";
char lastCompletedId[20] = "";

volatile bool packetPending = false;
volatile int pendingLength = 0;
uint8_t pendingPacket[MAX_PACKET_BYTES + 1];

static void sendStatus(const char *id, const char *status, const char *detail = "") {
  StaticJsonDocument<224> response;
  response["id"] = id;
  response["arm"] = ARM_ID;
  response["status"] = status;
  if (strlen(detail)) response["detail"] = detail;
  JsonObject telemetry = response.createNestedObject("telemetry");
  telemetry["pickup"] = digitalRead(PICKUP_SENSOR_PIN) == LOW;
  char wire[MAX_PACKET_BYTES + 1];
  size_t length = serializeJson(response, wire, sizeof(wire));
  esp_now_send(GATEWAY_MAC, reinterpret_cast<uint8_t *>(wire), length);
}

static void stopMotors() {
  if (j1) j1->forceStop();
  if (j2) j2->forceStop();
}

static void enterFault(const char *detail) {
  stopMotors();
  digitalWrite(MAGNET_PIN, LOW);
  state = FAULTED;
  sendStatus(activeId, "FAULT", detail);
}

static void finishCommand() {
  strncpy(lastCompletedId, activeId, sizeof(lastCompletedId) - 1);
  lastCompletedId[sizeof(lastCompletedId) - 1] = '\0';
  sendStatus(activeId, "DONE");
  activeId[0] = '\0';
  state = IDLE;
}

static void configureMove(FastAccelStepper *motor, int32_t target, uint32_t speed, uint32_t accel) {
  motor->setSpeedInHz(speed < 100 ? 100 : speed);
  motor->setAcceleration(accel < 100 ? 100 : accel);
  motor->moveTo(target);
}

static void startWaypoint(size_t index) {
  Waypoint &point = waypoints[index];
  configureMove(j1, lroundf(point.shoulderDeg * J1_STEPS_PER_DEG), point.speedHz, point.acceleration);
  configureMove(j2, lroundf(point.elbowDeg * J2_STEPS_PER_DEG), point.speedHz, point.acceleration);
}

static bool allStopped() {
  return !j1->isRunning() && !j2->isRunning();
}

static void startHoming() {
  state = HOMING;
  stateStartedMs = millis();
  homeDone[0] = homeDone[1] = false;
  configureMove(j1, -200000, 800, 600);
  configureMove(j2, -200000, 800, 600);
  sendStatus(activeId, "STARTED");
}

static bool loadTrajectory(JsonVariantConst payload, bool parkCommand) {
  waypointCount = 0;
  if (parkCommand) {
    JsonArrayConst point = payload["p"].as<JsonArrayConst>();
    if (point.size() != 5) return false;
    waypoints[0] = {
      point[0].as<float>(), point[1].as<float>(), point[2].as<float>(),
      point[3].as<uint32_t>(), point[4].as<uint32_t>()
    };
    waypointCount = 1;
  } else {
    JsonArrayConst points = payload["p"].as<JsonArrayConst>();
    if (points.size() == 0 || points.size() > MAX_WAYPOINTS) return false;
    for (JsonArrayConst point : points) {
      if (point.size() != 5) return false;
      waypoints[waypointCount++] = {
        point[0].as<float>(), point[1].as<float>(), point[2].as<float>(),
        point[3].as<uint32_t>(), point[4].as<uint32_t>()
      };
    }
  }
  waypointIndex = 0;
  state = TRAJECTORY;
  stateStartedMs = millis();
  sendStatus(activeId, "STARTED");
  startWaypoint(0);
  return true;
}

static void handleCommand(const uint8_t *data, int length) {
  StaticJsonDocument<512> document;
  if (deserializeJson(document, data, length)) {
    sendStatus("", "FAULT", "malformed JSON");
    return;
  }
  const char *id = document["id"] | "";
  const char *target = document["arm"] | "";
  const char *action = document["action"] | "";
  if (strcmp(target, ARM_ID) != 0 || strlen(id) == 0) return;

  if (strcmp(id, lastCompletedId) == 0) {
    sendStatus(id, "DONE", "duplicate suppressed");
    return;
  }
  if (strcmp(id, activeId) == 0) {
    sendStatus(id, "ACCEPTED", "already executing");
    return;
  }
  if (state != IDLE && strcmp(action, "STOP") != 0 && strcmp(action, "STATUS") != 0
      && strcmp(action, "HOME") != 0) {
    sendStatus(id, "FAULT", "arm busy");
    return;
  }

  if (strcmp(action, "STATUS") == 0) {
    sendStatus(id, state == FAULTED ? "FAULT" : "DONE");
    return;
  }
  strncpy(activeId, id, sizeof(activeId) - 1);
  activeId[sizeof(activeId) - 1] = '\0';
  sendStatus(activeId, "ACCEPTED");

  if (strcmp(action, "STOP") == 0) {
    stopMotors();
    digitalWrite(MAGNET_PIN, LOW);
    state = IDLE;
    finishCommand();
  } else if (strcmp(action, "HOME") == 0) {
    state = IDLE;  // HOME is the only command that clears a prior FAULTED state.
    startHoming();
  } else if (state == FAULTED) {
    sendStatus(activeId, "FAULT", "home required after fault");
  } else if (strcmp(action, "SET_MAGNET") == 0) {
    digitalWrite(MAGNET_PIN, document["payload"]["on"].as<bool>() ? HIGH : LOW);
    // The PC sends the required pickup/release dwell with this command. Keep
    // the arm controller busy until it has elapsed so the next trajectory
    // cannot begin while the puck is still settling under the magnet.
    magnetSettleMs = document["payload"]["settle_ms"] | 40;
    if (magnetSettleMs > 3000) {
      enterFault("magnet settle exceeds limit");
    } else if (magnetSettleMs == 0) {
      finishCommand();
    } else {
      state = MAGNET_SETTLING;
      stateStartedMs = millis();
      sendStatus(activeId, "STARTED");
    }
  } else if (strcmp(action, "EXECUTE_TRAJECTORY") == 0) {
    if (!loadTrajectory(document["payload"], false)) enterFault("invalid trajectory");
  } else if (strcmp(action, "PARK") == 0) {
    if (!loadTrajectory(document["payload"], true)) enterFault("invalid park target");
  } else {
    enterFault("unknown action");
  }
}

static void copyIncoming(const uint8_t *data, int length) {
  if (length <= 0 || length > static_cast<int>(MAX_PACKET_BYTES) || packetPending) return;
  memcpy(pendingPacket, data, length);
  pendingLength = length;
  packetPending = true;
}

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
static void onCommand(const esp_now_recv_info_t *, const uint8_t *data, int length) {
  copyIncoming(data, length);
}
#else
static void onCommand(const uint8_t *, const uint8_t *data, int length) {
  copyIncoming(data, length);
}
#endif

static bool addGatewayPeer() {
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, GATEWAY_MAC, 6);
  peer.channel = 0;
  peer.encrypt = false;
  return esp_now_add_peer(&peer) == ESP_OK;
}

void setup() {
  Serial.begin(115200);
  pinMode(ENABLE_PIN, OUTPUT);
  pinMode(MAGNET_PIN, OUTPUT);
  pinMode(J1_HOME_PIN, INPUT_PULLUP);
  pinMode(J2_HOME_PIN, INPUT_PULLUP);
  pinMode(ESTOP_PIN, INPUT_PULLUP);
  pinMode(PICKUP_SENSOR_PIN, INPUT_PULLUP);
  digitalWrite(MAGNET_PIN, LOW);

  stepperEngine.init();
  j1 = stepperEngine.stepperConnectToPin(J1_STEP_PIN);
  j2 = stepperEngine.stepperConnectToPin(J2_STEP_PIN);
  if (!j1 || !j2) {
    state = FAULTED;
    return;
  }
  j1->setDirectionPin(J1_DIR_PIN);
  j2->setDirectionPin(J2_DIR_PIN);
  for (FastAccelStepper *motor : {j1, j2}) {
    motor->setEnablePin(ENABLE_PIN);
    motor->setAutoEnable(true);
  }

  WiFi.mode(WIFI_STA);
  if (esp_now_init() != ESP_OK || !addGatewayPeer()) {
    state = FAULTED;
    return;
  }
  esp_now_register_recv_cb(onCommand);
}

void loop() {
  if (digitalRead(ESTOP_PIN) == LOW && state != FAULTED) {
    enterFault("emergency stop");
  }

  if (packetPending) {
    noInterrupts();
    int length = pendingLength;
    uint8_t local[MAX_PACKET_BYTES + 1];
    memcpy(local, pendingPacket, length);
    packetPending = false;
    interrupts();
    handleCommand(local, length);
  }

  if (state == HOMING) {
    FastAccelStepper *motors[] = {j1, j2};
    int pins[] = {J1_HOME_PIN, J2_HOME_PIN};
    for (int index = 0; index < 2; ++index) {
      if (!homeDone[index] && digitalRead(pins[index]) == LOW) {
        motors[index]->forceStopAndNewPosition(0);
        homeDone[index] = true;
      }
    }
    if (homeDone[0] && homeDone[1]) {
      finishCommand();
    } else if (millis() - stateStartedMs > HOMING_TIMEOUT_MS) {
      enterFault("homing timeout");
    }
  } else if (state == TRAJECTORY && allStopped()) {
    ++waypointIndex;
    if (waypointIndex >= waypointCount) finishCommand();
    else startWaypoint(waypointIndex);
  } else if (state == MAGNET_SETTLING && millis() - stateStartedMs >= magnetSettleMs) {
    finishCommand();
  }
  delay(1);
}
