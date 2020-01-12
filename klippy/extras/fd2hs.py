# Support for DIY filament width sensor
#
# Copyright (C) 2020  Damir Khakimov <damir.hakimov@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Based on
# https://3dtoday.ru/blogs/test3210/the-sensor-diameter-of-the-filament-from-simple-inexpensive-parts-avai/
#
# Sensor accuracy no worse than 0.01mm
# WARNING: Only one sensor and one extruder at this time!
#
# To make this sensor You need:
# 1.Ironing skills
# 2a. Linear Hall Effect Sensor SS49E - 2 pcs
#       Documentation (https://www.sunrom.com/get/324700)
# 2b. Neodymium magnet 5x5x2mm - 1 pc
# 2c. Radial bearing F623ZZ 3x10x4mm - 2 pcs
# 3. Two free ADC inputs on Your mcu board
#       (Or You can add another mcu board)
# 4. 3d printed case case

ADC_REPORT_TIME = 0.500
ADC_SAMPLE_TIME = 0.001
ADC_SAMPLE_COUNT = 8
MEASUREMENT_INTERVAL_MM = 5

class FD2HS:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        pin1 = config.get("hall_sensor_1")
        pin2 = config.get("hall_sensor_2")
        if pin1==pin2:
            raise config.error("Can't use the same pin for hall_sensor_1 and hall_sensor_2")

        self.hall_1 = printer.lookup_object("pins").setup_pin("adc", pin1)
        self.hall_2 = printer.lookup_object("pins").setup_pin("adc", pin2)

        self.nominal_filament_dia = config.getfloat(
            'nominal_filament_diameter', above=1.0)
        self.measurement_delay = config.getfloat('measurement_delay', above=0.)
        self.max_diameter = config.getfloat(
            'max_filament_diameter', above=self.nominal_filament_dia)
        self.min_diameter = config.getfloat(
            'min_filament_diameter', above=1.0)
        self.is_active = True
        # filament array [position, filamentWidth]
        self.filament_array = []
        self.lastFilamentWidthReading = 0
        # printer objects
        self.toolhead = self.ppins = self.mcu_adc = None
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        # Start adc
        self.ppins = self.printer.lookup_object('pins')
        self.mcu_adc = self.ppins.setup_pin('adc', self.pin)
        self.mcu_adc.setup_minmax(ADC_SAMPLE_TIME, ADC_SAMPLE_COUNT)
        self.mcu_adc.setup_adc_callback(ADC_REPORT_TIME, self.adc_callback)
        # extrude factor updating
        self.extrude_factor_update_timer = self.reactor.register_timer(
            self.extrude_factor_update_event)
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('QUERY_FILAMENT_WIDTH', self.cmd_M407)
        self.gcode.register_command('RESET_FILAMENT_WIDTH_SENSOR',
                                    self.cmd_ClearFilamentArray)
        self.gcode.register_command('DISABLE_FILAMENT_WIDTH_SENSOR',
                                    self.cmd_M406)
        self.gcode.register_command('ENABLE_FILAMENT_WIDTH_SENSOR',
                                    self.cmd_M405)

    # Initialization
    def handle_ready(self):
        # Load printer objects
        self.toolhead = self.printer.lookup_object('toolhead')

        # Start extrude factor update timer
        self.reactor.update_timer(self.extrude_factor_update_timer,
                                  self.reactor.NOW)

    def adc_callback(self, read_time, read_value):
        # read sensor value
        self.lastFilamentWidthReading = round(read_value * 5, 2)

    def update_filament_array(self, last_epos):
        # Fill array
        if len(self.filament_array) > 0:
            # Get last reading position in array & calculate next
            # reading position
            next_reading_position = (self.filament_array[-1][0]
                                     + MEASUREMENT_INTERVAL_MM)
            if next_reading_position <= (last_epos + self.measurement_delay):
                self.filament_array.append([last_epos + self.measurement_delay,
                                            self.lastFilamentWidthReading])
        else:
            # add first item to array
            self.filament_array.append([self.measurement_delay + last_epos,
                                        self.lastFilamentWidthReading])

    def extrude_factor_update_event(self, eventtime):
        # Update extrude factor
        pos = self.toolhead.get_position()
        last_epos = pos[3]
        # Update filament array for lastFilamentWidthReading
        self.update_filament_array(last_epos)
        # Does filament exists
        if self.lastFilamentWidthReading > 0.5:
            if len(self.filament_array) > 0:
                # Get first position in filament array
                pending_position = self.filament_array[0][0]
                if pending_position <= last_epos:
                    # Get first item in filament_array queue
                    item = self.filament_array.pop(0)
                    filament_width = item[1]
                    if ((filament_width <= self.max_diameter)
                        and (filament_width >= self.min_diameter)):
                        percentage = round(self.nominal_filament_dia**2
                                           / filament_width**2 * 100)
                        self.gcode.run_script("M221 S" + str(percentage))
                    else:
                        self.gcode.run_script("M221 S100")
        else:
            self.gcode.run_script("M221 S100")
            self.filament_array = []
        return eventtime + 1

    def cmd_M407(self, params):
        response = ""
        if self.lastFilamentWidthReading > 0:
            response += ("Filament dia (measured mm): "
                         + str(self.lastFilamentWidthReading))
        else:
            response += "Filament NOT present"
        self.gcode.respond(response)

    def cmd_ClearFilamentArray(self, params):
        self.filament_array = []
        self.gcode.respond("Filament width measurements cleared!")
        # Set extrude multiplier to 100%
        self.gcode.run_script_from_command("M221 S100")

    def cmd_M405(self, params):
        response = "Filament width sensor Turned On"
        if self.is_active:
            response = "Filament width sensor is already On"
        else:
            self.is_active = True
            # Start extrude factor update timer
            self.reactor.update_timer(self.extrude_factor_update_timer,
                                      self.reactor.NOW)
        self.gcode.respond(response)

    def cmd_M406(self, params):
        response = "Filament width sensor Turned Off"
        if not self.is_active:
            response = "Filament width sensor is already Off"
        else:
            self.is_active = False
            # Stop extrude factor update timer
            self.reactor.update_timer(self.extrude_factor_update_timer,
                                      self.reactor.NEVER)
            # Clear filament array
            self.filament_array = []
            # Set extrude multiplier to 100%
            self.gcode.run_script_from_command("M221 S100")
        self.gcode.respond(response)

def load_config(config):
    return FD2HS(config)
