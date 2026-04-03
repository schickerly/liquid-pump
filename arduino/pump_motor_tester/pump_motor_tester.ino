/*
 * Pump motor tester — serial control for PC GUI.
 *
 * Wiring target: Arduino Nano Every -> BTS7960 / IBT-2 style driver
 * (R_EN, L_EN, RPWM, LPWM) -> 12V pump.
 * Baud: 115200, line-based commands ending in \n
 *
 * Commands (case-insensitive):
 *   S <0-100>  — speed percent (PWM duty)
 *   D F        — forward (LPWM active, RPWM 0) — swapped vs raw H-bridge labels to match plumbing
 *   D R        — reverse (RPWM active, LPWM 0)
 *   STOP       — same as S 0 (both PWM 0)
 *
 * Pins (change if needed):
 */
const int PIN_RPWM = 5;  // PWM pin
const int PIN_LPWM = 6;  // PWM pin
const int PIN_REN = 7;
const int PIN_LEN = 8;

bool forward_dir = true;
int speed_pct = 0;

void motor_apply() {
  int pwm = map(constrain(speed_pct, 0, 100), 0, 100, 0, 255);
  if (speed_pct <= 0) {
    analogWrite(PIN_RPWM, 0);
    analogWrite(PIN_LPWM, 0);
    return;
  }
  if (forward_dir) {
    analogWrite(PIN_RPWM, 0);
    analogWrite(PIN_LPWM, pwm);
  } else {
    analogWrite(PIN_RPWM, pwm);
    analogWrite(PIN_LPWM, 0);
  }
}

void process_line(String line) {
  line.trim();
  if (line.length() == 0) {
    return;
  }
  line.toUpperCase();

  if (line == "STOP") {
    speed_pct = 0;
    motor_apply();
    Serial.println(F("OK STOP"));
    return;
  }
  if (line.startsWith("S ")) {
    int v = line.substring(2).toInt();
    speed_pct = constrain(v, 0, 100);
    motor_apply();
    Serial.print(F("OK S "));
    Serial.println(speed_pct);
    return;
  }
  if (line.startsWith("D ")) {
    if (line.length() < 3) {
      Serial.println(F("ERR D ?"));
      return;
    }
    char c = line.charAt(2);
    if (c == 'F') {
      forward_dir = true;
    } else if (c == 'R') {
      forward_dir = false;
    } else {
      Serial.println(F("ERR D F|R"));
      return;
    }
    motor_apply();
    Serial.println(forward_dir ? F("OK D F") : F("OK D R"));
    return;
  }
  Serial.println(F("ERR unknown"));
}

void setup() {
  pinMode(PIN_RPWM, OUTPUT);
  pinMode(PIN_LPWM, OUTPUT);
  pinMode(PIN_REN, OUTPUT);
  pinMode(PIN_LEN, OUTPUT);
  digitalWrite(PIN_REN, HIGH);
  digitalWrite(PIN_LEN, HIGH);
  motor_apply();
  Serial.begin(115200);
  Serial.println(F("pump_motor_tester ready"));
}

void loop() {
  if (!Serial.available()) {
    return;
  }
  String line = Serial.readStringUntil('\n');
  process_line(line);
}
