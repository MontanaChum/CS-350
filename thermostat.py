"""CS-350 smart thermostat prototype. Hardware test required before submission.

Dependencies: gpiozero, adafruit-circuitpython-ahtx0, RPLCD, pyserial, smbus2.
Pin numbers are BCM GPIO numbers; edit CONFIG below to match your wiring.
"""
import logging
import time
from datetime import datetime
from enum import Enum

import board
import adafruit_ahtx0
import serial
from gpiozero import Button, PWMLED
from RPLCD.i2c import CharLCD

# BCM GPIO assignments; verify against the lab guide before wiring.
MODE_BUTTON_GPIO = 17
UP_BUTTON_GPIO = 27
DOWN_BUTTON_GPIO = 22
HEAT_LED_GPIO = 18
COOL_LED_GPIO = 13
LCD_I2C_ADDRESS = 0x27  # Some backpacks use 0x3F.
LCD_EXPANDER = 'PCF8574'
UART_DEVICE = '/dev/serial0'
UART_BAUD = 9600  # Verify baud rate and data format with your lab guide.
INITIAL_SETPOINT_F = 72.0
SETPOINT_STEP_F = 1.0
MIN_SETPOINT_F = 50.0
MAX_SETPOINT_F = 90.0
TEMPERATURE_TOLERANCE_F = 0.5
LOOP_INTERVAL_SECONDS = 0.5

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

class Mode(Enum):
    OFF = 'OFF'
    HEAT = 'HEAT'
    COOL = 'COOL'

class Thermostat:
    def __init__(self):
        self.mode = Mode.OFF
        self.setpoint_f = INITIAL_SETPOINT_F
        self.sensor = adafruit_ahtx0.AHTx0(board.I2C())
        self.heat_led = PWMLED(HEAT_LED_GPIO)
        self.cool_led = PWMLED(COOL_LED_GPIO)
        self.mode_button = Button(MODE_BUTTON_GPIO, pull_up=True, bounce_time=0.15)
        self.up_button = Button(UP_BUTTON_GPIO, pull_up=True, bounce_time=0.15)
        self.down_button = Button(DOWN_BUTTON_GPIO, pull_up=True, bounce_time=0.15)
        self.mode_button.when_pressed = self.next_mode
        self.up_button.when_pressed = self.increase_setpoint
        self.down_button.when_pressed = self.decrease_setpoint
        self.lcd = None
        self.uart = None
        try:
            self.lcd = CharLCD(i2c_expander=LCD_EXPANDER, address=LCD_I2C_ADDRESS,
                               port=1, cols=16, rows=2, charmap='A00')
            self.lcd.clear()
        except Exception as exc:
            logging.warning('LCD unavailable: %s', exc)
        try:
            self.uart = serial.Serial(UART_DEVICE, UART_BAUD, bytesize=serial.EIGHTBITS,
                                      parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                      timeout=1)
        except Exception as exc:
            logging.warning('UART unavailable: %s', exc)

    def next_mode(self):
        self.mode = {Mode.OFF: Mode.HEAT, Mode.HEAT: Mode.COOL, Mode.COOL: Mode.OFF}[self.mode]
        logging.info('Mode changed: %s', self.mode.value)

    def increase_setpoint(self):
        self.setpoint_f = min(MAX_SETPOINT_F, self.setpoint_f + SETPOINT_STEP_F)
        logging.info('Setpoint: %.1f F', self.setpoint_f)

    def decrease_setpoint(self):
        self.setpoint_f = max(MIN_SETPOINT_F, self.setpoint_f - SETPOINT_STEP_F)
        logging.info('Setpoint: %.1f F', self.setpoint_f)

    def update_leds(self, current_f, phase):
        """Pulse the active LED only when heating/cooling is needed; solid at target."""
        self.heat_led.value = 0
        self.cool_led.value = 0
        if self.mode is Mode.OFF:
            return
        active = self.heat_led if self.mode is Mode.HEAT else self.cool_led
        delta = self.setpoint_f - current_f
        demand = delta > TEMPERATURE_TOLERANCE_F if self.mode is Mode.HEAT else delta < -TEMPERATURE_TOLERANCE_F
        at_target = abs(delta) <= TEMPERATURE_TOLERANCE_F
        if demand:
            active.value = phase
        elif at_target:
            active.value = 1
        # If temperature has passed setpoint, output is off.

    def display(self, current_f):
        if not self.lcd:
            return
        now = datetime.now()
        first = now.strftime('%m/%d %H:%M')[:16]
        second = f'{self.mode.value[:4]:<4} {current_f:4.1f}/{self.setpoint_f:4.1f}'[:16]
        try:
            self.lcd.cursor_pos = (0, 0)
            self.lcd.write_string(first.ljust(16))
            self.lcd.cursor_pos = (1, 0)
            self.lcd.write_string(second.ljust(16))
        except Exception as exc:
            logging.warning('LCD write failed: %s', exc)

    def send_uart(self, current_f):
        payload = f'{datetime.now().isoformat(timespec="seconds")},mode={self.mode.value},temp_f={current_f:.1f},set_f={self.setpoint_f:.1f}\r\n'
        if self.uart:
            try:
                self.uart.write(payload.encode('ascii'))
            except Exception as exc:
                logging.warning('UART send failed: %s', exc)
        logging.info(payload.strip())

    def run(self):
        start = time.monotonic()
        last_send = 0.0
        while True:
            try:
                current_f = self.sensor.temperature * 9 / 5 + 32
                # Smooth triangular fade: 0 -> 1 -> 0 over 2 seconds.
                phase = 1 - abs((time.monotonic() - start) % 2 - 1)
                self.update_leds(current_f, phase)
                self.display(current_f)
                if time.monotonic() - last_send >= 2.0:
                    self.send_uart(current_f)
                    last_send = time.monotonic()
            except (OSError, RuntimeError, ValueError) as exc:
                logging.error('Sensor or peripheral read failed: %s', exc)
                self.heat_led.off()
                self.cool_led.off()
            time.sleep(LOOP_INTERVAL_SECONDS)

    def close(self):
        self.heat_led.off()
        self.cool_led.off()
        for device in (self.mode_button, self.up_button, self.down_button,
                       self.heat_led, self.cool_led):
            device.close()
        if self.lcd:
            self.lcd.clear()
        if self.uart:
            self.uart.close()

if __name__ == '__main__':
    thermostat = None
    try:
        thermostat = Thermostat()
        thermostat.run()
    except KeyboardInterrupt:
        print('\nStopping thermostat safely.')
    finally:
        if thermostat:
            thermostat.close()
