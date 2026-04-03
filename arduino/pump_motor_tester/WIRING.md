# Seaflow 12V gear pump — Arduino → speed controller → pump

This matches the chain: **PC → USB → Arduino → motor driver → pump**.

## Before you power anything

1. **Confirm the pump is a 2-wire brushed DC motor** (usually red/black). If your pump has three wires and is brushless, this sketch and an L298N-style driver are the wrong approach—use the vendor ESC instead.
2. **Fuse the 12V line** close to the battery/supply positive (e.g. fuse rated for your pump’s max current; start conservative).
3. **Never power the 12V pump from the Arduino 5V pin.** The Arduino only drives logic and PWM; motor current flows through the driver from the 12V supply.

## Power and grounds

- **12V supply (+)** → motor driver **VIN / +12V / VS** (name depends on module).
- **12V supply (−)** → **common ground bus**.
- Connect **Arduino GND** to that same ground bus (required so PWM/direction signals are referenced correctly).
- Connect motor driver **logic GND** to the same bus if the module has a separate logic ground.

## BTS7960 / IBT-2 style module (R_EN + L_EN + RPWM + LPWM)

For your `HW-039`-marked controller with pins:
`VCC, GND, R_IS, L_IS, R_EN, L_EN, RPWM, LPWM`

Use this mapping (matches current firmware):

| Driver pin | Connect to |
|------------|------------|
| VCC        | Arduino **5V** |
| GND        | Arduino **GND** and 12V supply **−** |
| R_EN       | Arduino **D7** (or tie high to 5V) |
| L_EN       | Arduino **D8** (or tie high to 5V) |
| RPWM       | Arduino **D5** (PWM) |
| LPWM       | Arduino **D6** (PWM) |
| R_IS / L_IS| leave open for now (optional current-sense) |

Power stage:
- Driver `B+`/`Vmotor+` -> 12V supply **+**
- Driver `B-`/`Vmotor-` -> 12V supply **−**
- Driver `M+`/`M-` -> pump motor leads

If direction is opposite of expectation, swap pump leads at `M+`/`M-`.

## USB

- PC/laptop USB → Arduino for serial and to upload `pump_motor_tester.ino`.

## Smoke-test order

1. Upload firmware; open Serial Monitor at **115200**; you should see `pump_motor_tester ready`.
2. Send `S 0` then `D F` then `S 15` — pump should run slowly forward.
3. Send `S 0`, then `D R`, then `S 15` — reverse.
4. Use the Python **Pump motor test** GUI for the same commands.

Always start at **low speed** and with **liquid or safe load** so dry-running does not damage the gear pump.
