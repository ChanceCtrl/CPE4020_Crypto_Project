/*
 * EV Charging Station — Sensor Node
 * Arduino UNO R4 WiFi
 *
 * Signing: ECDSA P-256 / SHA-256 via uECC library
 *
 * Compatible with validator.py — sends integer-scaled kwh and price fields
 * so JSON serialization is identical between Arduino and Python.
 *
 * Required Arduino libraries (install via Library Manager):
 *   - WiFiS3       (bundled with R4 board package)
 *   - ArduinoJson  ('ArduinoJson' by Benoit Blanchon)
 *   - Crypto       ('Crypto' by Rhys Weatherley — provides SHA256.h)
 *   - micro-ecc    ('micro-ecc' by kmackay)  ← search for "uECC" or "micro-ecc"
 *
 * SETUP:
 *   1. Run keygen.py on the validator machine.
 *   2. Run extract_key_for_arduino.py — paste output into KEY MATERIAL below.
 *   3. Deploy walletA.pub.hex to all validator nodes (place next to
 * validator.py).
 *   4. Flash and open Serial Monitor at 115200 baud.
 */

#include <Arduino.h>
#include <ArduinoJson.h>
#include <SHA256.h>
#include <WiFiS3.h>
#include <WiFiUdp.h>
#include <uECC.h>

// ---------- NETWORK CONFIG ----------
const char *WIFI_SSID = "hellotest";
const char *WIFI_PASSWORD = "turnaround";
const char *VALIDATOR_HOST = "172.20.10.2";
const uint16_t VALIDATOR_PORT = 4020;
const char *VALIDATOR_PATH = "/publishdata";

// ---------- SENSOR CONFIG ----------
const float MAX_POWER_KW = 150.0f;
const float PRICE_PER_KWH = 0.20f;
const unsigned long SAMPLE_INTERVAL_MS = 5000;
const int POT_PIN = A0;
const int VOLTAGE_PIN = A1;
const int CURRENT_PIN = A2;
const int ADC_RESOLUTION = 16384;
const int ADC_REF_VOLTAGE = 5;
const float VOLTAGE_DIVIDER_RATIO = 6.1538461538;

// ---------- NTP ----------
const char *NTP_HOST = "pool.ntp.org";
const uint16_t NTP_PORT = 123;
const unsigned long NTP_UNIX_OFFSET = 2208988800UL;

// ==========================================================================
// KEY MATERIAL — paste output of extract_key_for_arduino.py here
// ==========================================================================

const uint8_t PRIVATE_KEY[32] = {
    0x84, 0x9A, 0x6B, 0xBC, 0xE8, 0x63, 0x46, 0xB3, 0x1F, 0x5E, 0xB7,
    0xF1, 0xDA, 0x83, 0x18, 0x25, 0xF7, 0x6B, 0xE8, 0xFE, 0x23, 0x48,
    0x58, 0x1F, 0x95, 0xDE, 0x20, 0xD8, 0xFF, 0xCD, 0x1F, 0x60};

const uint8_t PUBLIC_KEY[64] = {
    0x3B, 0x63, 0x9E, 0x4A, 0xFC, 0x51, 0xCB, 0x29, 0xF8, 0x61, 0x9C,
    0xC8, 0x4B, 0x18, 0x5E, 0x6E, 0x15, 0x4B, 0xF0, 0x94, 0x03, 0x19,
    0xE8, 0xD1, 0xDC, 0xEC, 0xAE, 0xCB, 0x3F, 0x46, 0xAB, 0xEB, 0xAE,
    0xBA, 0xD8, 0x8B, 0x2E, 0x58, 0x83, 0x2E, 0x4D, 0xD5, 0x88, 0x03,
    0x39, 0xA5, 0x83, 0x1A, 0xBA, 0xF3, 0xA4, 0x71, 0x1B, 0x9C, 0x7F,
    0xB4, 0xDA, 0x84, 0xA2, 0x64, 0xF3, 0x7D, 0x9D, 0x6F};

// Hex-encoded public key — sent as "walletKey" in every JSON payload
const char WALLET_KEY_HEX[] =
    "3b639e4afc51cb29f8619cc84b185e6e154bf0940319e8d1dcecaecb3f46abebaebad88b2e"
    "58832e4dd5880339a5831abaf3a4711b9c7fb4da84a264f37d9d6f";

// ==========================================================================

// ---------- GLOBALS ----------
WiFiClient client;
WiFiUDP udp;
unsigned long lastSample = 0;
unsigned long epochAtSync = 0;
unsigned long millisAtSync = 0;

const struct uECC_Curve_t *curve = uECC_secp256r1();

// ---------- RNG (required for ECDSA signing) ----------
static int rng_function(uint8_t *dest, unsigned size) {
  // Seed from analog noise — adequate for class project, not crypto-grade
  for (unsigned i = 0; i < size; i++) {
    uint32_t noise = 0;
    for (int b = 0; b < 8; b++) {
      noise = (noise << 1) | (analogRead(A5) & 1);
      delayMicroseconds(17);
    }
    dest[i] = (uint8_t)(noise ^ (micros() & 0xFF));
  }
  return 1;
}

// ---------- HELPERS ----------
void bytesToHex(const uint8_t *b, size_t len, char *out) {
  const char *h = "0123456789abcdef";
  for (size_t i = 0; i < len; i++) {
    out[i * 2] = h[(b[i] >> 4) & 0xF];
    out[i * 2 + 1] = h[b[i] & 0xF];
  }
  out[len * 2] = '\0';
}

// ---------- WIFI ----------
void connectWiFi() {
  Serial.print(F("WiFi"));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }
  Serial.print(F(" OK "));
  Serial.println(WiFi.localIP());
}

// ---------- NTP ----------
unsigned long fetchNTP() {
  uint8_t pkt[48] = {0};
  pkt[0] = 0b11100011;
  udp.begin(2390);
  udp.beginPacket(NTP_HOST, NTP_PORT);
  udp.write(pkt, 48);
  udp.endPacket();
  unsigned long t0 = millis();
  while (millis() - t0 < 2000) {
    if (udp.parsePacket() >= 48) {
      udp.read(pkt, 48);
      unsigned long secs =
          ((unsigned long)pkt[40] << 24) | ((unsigned long)pkt[41] << 16) |
          ((unsigned long)pkt[42] << 8) | ((unsigned long)pkt[43]);
      udp.stop();
      return secs - NTP_UNIX_OFFSET;
    }
  }
  udp.stop();
  return 0;
}

void syncTime() {
  Serial.print(F("NTP... "));
  for (int i = 0; i < 5; i++) {
    unsigned long t = fetchNTP();
    if (t > 1700000000UL) {
      epochAtSync = t;
      millisAtSync = millis();
      Serial.print(F("OK epoch="));
      Serial.println(epochAtSync);
      return;
    }
    delay(1000);
  }
  Serial.println(F("FAILED — timestamps will be 0"));
}

unsigned long currentEpoch() {
  if (epochAtSync == 0)
    return 0;
  return epochAtSync + (millis() - millisAtSync) / 1000UL;
}

// ---------- SENSOR ----------
float readVoltage() {
  int raw = analogRead(VOLTAGE_PIN);
  float v = (raw / (float)ADC_RESOLUTION) * ADC_REF_VOLTAGE;

  return v * VOLTAGE_DIVIDER_RATIO;
}
float readCurrent() {
  int raw_v1 = analogRead(VOLTAGE_PIN);
  int raw_v2 = analogRead(CURRENT_PIN);
  float v1 = (raw_v1 / (float)ADC_RESOLUTION) * ADC_REF_VOLTAGE;
  float v2 = (raw_v2 / (float)ADC_RESOLUTION) * ADC_REF_VOLTAGE;
}

float readPowerKW() { return readVoltage() * readCurrent(); }

// ---------- SIGN + SEND ----------
void sampleAndSend() {

  // ── 1. Read sensor (per-tick kWh, not accumulated) ───────────────────
  float powerKW = readPowerKW();
  float kwh = powerKW * (SAMPLE_INTERVAL_MS / 1000.0f) / 3600.0f;
  unsigned long ts = currentEpoch();

  // ── 2. Convert to integer-scaled units ───────────────────────────────
  // kwh_milli   = kwh × 1000        (e.g. 0.1042 kWh -> 104)
  // price_micro = price × 1,000,000 (e.g. 0.20 $/kWh -> 200000)
  unsigned long kwh_milli =
      (unsigned long)(kwh * 1000.0f + 0.5f); // round to nearest
  unsigned long price_micro =
      (unsigned long)(PRICE_PER_KWH * 1000000.0f + 0.5f);

  // ── 3. Build canonical JSON to sign ──────────────────────────────────
  // Must exactly match Python's json.dumps(payload, sort_keys=True) — uses
  // ", " and ": " separators (with spaces). Keys sorted alphabetically:
  //   kwh_milli, price_micro, timeStamp, walletKey
  //
  char canonical[512];
  int canonLen = snprintf(canonical, sizeof(canonical),
                          "{\"kwh_milli\": %lu, \"price_micro\": %lu, "
                          "\"timeStamp\": %lu, \"walletKey\": \"%s\"}",
                          kwh_milli, price_micro, ts, WALLET_KEY_HEX);

  Serial.print(F("Canonical: "));
  Serial.println(canonical);

  // ── 4. SHA-256 hash of canonical string ──────────────────────────────
  uint8_t hash[32];
  SHA256 sha;
  sha.reset();
  sha.update((const void *)canonical, (size_t)canonLen);
  sha.finalize(hash, sizeof(hash));

  // ── 5. ECDSA P-256 sign — produces 64-byte signature (r || s) ────────
  uint8_t signature[64];
  if (!uECC_sign(PRIVATE_KEY, hash, sizeof(hash), signature, curve)) {
    Serial.println(F("!! ECDSA sign failed"));
    return;
  }

  char sigHex[129];
  bytesToHex(signature, 64, sigHex);

  // ── 6. Build POST body ────────────────────────────────────────────────
  // Use ArduinoJson — field order in the body doesn't matter since the
  // validator reconstructs canonical form via json.dumps(sort_keys=True).
  JsonDocument doc;
  doc["walletKey"] = WALLET_KEY_HEX;
  doc["timeStamp"] = ts;
  doc["kwh_milli"] = kwh_milli;
  doc["price_micro"] = price_micro;
  doc["signature"] = sigHex;

  char body[1024];
  size_t bodyLen = serializeJson(doc, body, sizeof(body));
  Serial.print(F("Body len: "));
  Serial.println(bodyLen);

  // ── 7. POST to validator ──────────────────────────────────────────────
  if (!client.connect(VALIDATOR_HOST, VALIDATOR_PORT)) {
    Serial.println(F("!! connect failed"));
    return;
  }
  client.print(F("POST "));
  client.print(VALIDATOR_PATH);
  client.println(F(" HTTP/1.1"));
  client.print(F("Host: "));
  client.println(VALIDATOR_HOST);
  client.println(F("Content-Type: application/json"));
  client.print(F("Content-Length: "));
  client.println(bodyLen);
  client.println(F("Connection: close"));
  client.println();
  client.write((const uint8_t *)body, bodyLen);

  unsigned long t0 = millis();
  while (client.connected() && millis() - t0 < 5000) {
    if (client.available()) {
      String line = client.readStringUntil('\n');
      Serial.print(F("RX: "));
      Serial.println(line);
      if (line == "\r")
        break;
    }
  }
  while (client.available())
    Serial.write(client.read());
  client.stop();
}

// ---------- SETUP / LOOP ----------
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
  }
  analogReadResolution(14);

  uECC_set_rng(&rng_function);

  Serial.print(F("Wallet key: "));
  Serial.println(WALLET_KEY_HEX);
  connectWiFi();
  syncTime();
  Serial.println(F("Sensor ready."));
}

void loop() {
  if (WiFi.status() != WL_CONNECTED)
    connectWiFi();
  if (epochAtSync == 0)
    syncTime();

  unsigned long now = millis();
  if (now - lastSample >= SAMPLE_INTERVAL_MS) {
    lastSample = now;
    sampleAndSend();
  }
}
