/*
 * Dual 3R arm controller — MG995-class PWM hobby servos (3 per arm)
 *
 * Wire waypoint format (6 numbers), unchanged from the PC brain:
 *   [shoulder_deg, elbow_deg, wrist_deg, fixed_z_mm, speed, acceleration]
 * For servos, `speed` is treated as an approximate slew rate in deg/s (clamped
 * to a safe window) and `acceleration` / `fixed_z_mm` are ignored — MG995s are
 * absolute-position PWM servos with no Z axis and no closed-loop accel control.
 *
 * Required libraries:
 *   ArduinoJson 6.x
 *   ESP32Servo   (handles LEDC allocation + microsecond output)
 *
 * Set ARM_ID and GATEWAY_MAC independently when flashing each robot.
 *
 * Servos are absolute-position: there is no homing sweep and no limit switch.
 * On power-up and on HOME the arm drives to HOME_ANGLES, so give the servos a
 * clear path to the home pose before energising the servo supply.
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <WiFi.h>
#include <esp_now.h>

#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif

#define ARM_ID "WHITE"
static uint8_t GATEWAY_MAC[] = {0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA};

// —— Servo signal pins (PWM). No DIR / ENABLE / homing pins for servos. ——
static constexpr int J1_SERVO_PIN = 13;  // shoulder
static constexpr int J2_SERVO_PIN = 25;  // elbow
static constexpr int J3_SERVO_PIN = 26;  // wrist
static constexpr int ESTOP_PIN = 32;         // INPUT_PULLUP, LOW = pressed
static constexpr int PICKUP_SENSOR_PIN = 33; // INPUT_PULLUP, LOW = piece present
static constexpr int MAGNET_PIN = 23;        // HIGH = magnet on

static constexpr size_t MAX_PACKET_BYTES = 240;
// 6 numbers per waypoint; keep max 3 so ESP-NOW frames stay under 240 bytes.
static constexpr size_t MAX_WAYPOINTS = 3;
static constexpr size_t WAYPOINT_FIELDS = 6;

// —— Per-joint angle → microsecond calibration ——
// us = midUs + (logicalDeg - midDeg) * usPerDeg * dir, clamped to [minUs, maxUs].
// Tune midUs / usPerDeg / dir per servo so the commanded logical joint angle
// (matching chess_robot/config.py limits) lands on the true mechanical angle.
struct JointCal {
  int midUs;       // pulse width at midDeg (typ. 1500 us = servo centre)
  float midDeg;    // logical angle that maps to midUs
  float usPerDeg;  // ~2000 us / 180 deg ≈ 11.111
  int dir;         // +1 or -1 depending on servo mounting orientation
  int minUs;
  int maxUs;
};

static JointCal jointCal[3] = {
  {1500, 0.0f, 11.111f, +1, 500, 2500},   // J1 shoulder (config limits -90..90)
  {1500, 90.0f, 11.111f, +1, 500, 2500},  // J2 elbow    (config limits   0..180)
  {1500, 90.0f, 11.111f, +1, 500, 2500},  // J3 wrist    (config limits   0..180)
};

// Home pose — keep in sync with ArmConfig home_* in chess_robot/config.py.
static constexpr float HOME_ANGLES[3] = {-45.0f, 0.0f, 90.0f};

// Software slew limits (deg/s). The wire `speed` field is clamped into this
// window; MG995s cannot usefully track a faster commanded rate.
static constexpr float SLEW_MIN_DPS = 20.0f;
static constexpr float SLEW_MAX_DPS = 150.0f;
static constexpr float ANGLE_EPSILON_DEG = 0.5f;

struct Waypoint {
  float shoulderDeg;
  float elbowDeg;
  float wristDeg;
  float slewDps;
};

enum MotionState { IDLE, MOVING, MAGNET_SETTLING, FAULTED };

Servo servos[3];
MotionState state = IDLE;
Waypoint waypoints[MAX_WAYPOINTS];
size_t waypointCount = 0;
size_t waypointIndex = 0;
float currentDeg[3] = {HOME_ANGLES[0], HOME_ANGLES[1], HOME_ANGLES[2]};
float activeSlewDps = SLEW_MAX_DPS;
uint32_t lastUpdateMs = 0;
uint32_t stateStartedMs = 0;
uint32_t magnetSettleMs = 0;
char activeId[20] = "";
char lastCompletedId[20] = "";

volatile bool packetPending = false;
volatile int pendingLength = 0;
uint8_t pendingPacket[MAX_PACKET_BYTES + 1];

static int angleToUs(int joint, float deg) {
  const JointCal &cal = jointCal[joint];
  float us = cal.midUs + (deg - cal.midDeg) * cal.usPerDeg * cal.dir;
  if (us < cal.minUs) us = cal.minUs;
  if (us > cal.maxUs) us = cal.maxUs;
  return lroundf(us);
}

static void writeJoint(int joint, float deg) {
  currentDeg[joint] = deg;
  servos[joint].writeMicroseconds(angleToUs(joint, deg));
}

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

static void holdServos() {
  // Freeze every joint at its last commanded angle.
  for (int j = 0; j < 3; ++j) writeJoint(j, currentDeg[j]);
}

static void enterFault(const char *detail) {
  holdServos();
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

static float clampSlew(float dps) {
  if (dps < SLEW_MIN_DPS) return SLEW_MIN_DPS;
  if (dps > SLEW_MAX_DPS) return SLEW_MAX_DPS;
  return dps;
}

static void beginMotion() {
  waypointIndex = 0;
  state = MOVING;
  stateStartedMs = millis();
  lastUpdateMs = stateStartedMs;
  activeSlewDps = waypoints[0].slewDps;
  sendStatus(activeId, "STARTED");
}

static bool loadWaypoints(JsonVariantConst payload, bool single) {
  waypointCount = 0;
  JsonArrayConst points = payload["p"].as<JsonArrayConst>();
  if (single) {
    // PARK sends one flat 6-field waypoint under "p".
    if (points.size() != WAYPOINT_FIELDS) return false;
    waypoints[0] = {
      points[0].as<float>(), points[1].as<float>(), points[2].as<float>(),
      clampSlew(points[4].as<float>())
    };
    waypointCount = 1;
    return true;
  }
  if (points.size() == 0 || points.size() > MAX_WAYPOINTS) return false;
  for (JsonArrayConst point : points) {
    if (point.size() != WAYPOINT_FIELDS) return false;
    waypoints[waypointCount++] = {
      point[0].as<float>(), point[1].as<float>(), point[2].as<float>(),
      clampSlew(point[4].as<float>())
    };
  }
  return true;
}

static void loadHome() {
  waypoints[0] = {HOME_ANGLES[0], HOME_ANGLES[1], HOME_ANGLES[2], SLEW_MAX_DPS};
  waypointCount = 1;
  beginMotion();
}

static bool advanceToward(const Waypoint &target, float maxStepDeg) {
  float goals[3] = {target.shoulderDeg, target.elbowDeg, target.wristDeg};
  bool reached = true;
  for (int j = 0; j < 3; ++j) {
    float delta = goals[j] - currentDeg[j];
    if (fabsf(delta) <= ANGLE_EPSILON_DEG) {
      writeJoint(j, goals[j]);
      continue;
    }
    float step = delta > 0 ? maxStepDeg : -maxStepDeg;
    if (fabsf(step) >= fabsf(delta)) {
      writeJoint(j, goals[j]);
    } else {
      writeJoint(j, currentDeg[j] + step);
      reached = false;
    }
  }
  return reached;
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
    holdServos();
    digitalWrite(MAGNET_PIN, LOW);
    state = IDLE;
    finishCommand();
  } else if (strcmp(action, "HOME") == 0) {
    state = IDLE;  // HOME is the only command that clears a prior FAULTED state.
    loadHome();
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
    if (loadWaypoints(document["payload"], false)) beginMotion();
    else enterFault("invalid trajectory");
  } else if (strcmp(action, "PARK") == 0) {
    if (loadWaypoints(document["payload"], true)) beginMotion();
    else enterFault("invalid park target");
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
  pinMode(MAGNET_PIN, OUTPUT);
  pinMode(ESTOP_PIN, INPUT_PULLUP);
  pinMode(PICKUP_SENSOR_PIN, INPUT_PULLUP);
  digitalWrite(MAGNET_PIN, LOW);

  // ESP32Servo needs LEDC timers reserved before attaching servos.
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  const int pins[3] = {J1_SERVO_PIN, J2_SERVO_PIN, J3_SERVO_PIN};
  for (int j = 0; j < 3; ++j) {
    servos[j].setPeriodHertz(50);  // standard analog servo frame
    servos[j].attach(pins[j], jointCal[j].minUs, jointCal[j].maxUs);
    writeJoint(j, HOME_ANGLES[j]);  // start in a known pose
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

  if (state == MOVING) {
    uint32_t now = millis();
    float dt = (now - lastUpdateMs) / 1000.0f;
    lastUpdateMs = now;
    float maxStep = activeSlewDps * dt;
    if (maxStep <= 0.0f) maxStep = ANGLE_EPSILON_DEG;
    if (advanceToward(waypoints[waypointIndex], maxStep)) {
      ++waypointIndex;
      if (waypointIndex >= waypointCount) {
        finishCommand();
      } else {
        activeSlewDps = waypoints[waypointIndex].slewDps;
      }
    }
  } else if (state == MAGNET_SETTLING && millis() - stateStartedMs >= magnetSettleMs) {
    finishCommand();
  }
  delay(1);
}
