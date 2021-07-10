import logging
import math
import traceback
from struct import pack, unpack
from threading import Thread

from pylgbst.messages import MsgHubProperties, MsgPortOutput, MsgPortInputFmtSetupSingle, MsgPortInfoRequest, \
    MsgPortModeInfoRequest, MsgPortInfo, MsgPortModeInfo, MsgPortInputFmtSingle
from pylgbst.utilities import queue, str2hex, usbyte, ushort, usint

log = logging.getLogger('peripherals')

# COLORS
COLOR_BLACK = 0x00
COLOR_PINK = 0x01
COLOR_PURPLE = 0x02
COLOR_BLUE = 0x03
COLOR_LIGHTBLUE = 0x04
COLOR_CYAN = 0x05
COLOR_GREEN = 0x06
COLOR_YELLOW = 0x07
COLOR_ORANGE = 0x08
COLOR_RED = 0x09
COLOR_WHITE = 0x0a
COLOR_NONE = 0xFF
COLORS = {
    COLOR_BLACK: "BLACK",
    COLOR_PINK: "PINK",
    COLOR_PURPLE: "PURPLE",
    COLOR_BLUE: "BLUE",
    COLOR_LIGHTBLUE: "LIGHTBLUE",
    COLOR_CYAN: "CYAN",
    COLOR_GREEN: "GREEN",
    COLOR_YELLOW: "YELLOW",
    COLOR_ORANGE: "ORANGE",
    COLOR_RED: "RED",
    COLOR_WHITE: "WHITE",
    COLOR_NONE: "NONE"
}


# TODO: support more types of peripherals from
# https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#io-type-id

class Peripheral(object):
    """
    :type parent: pylgbst.hub.Hub
    :type _incoming_port_data: queue.Queue
    :type _port_mode: MsgPortInputFmtSingle
    """

    def __init__(self, parent, port):
        """
        :type parent: pylgbst.hub.Hub
        :type port: int
        """
        super(Peripheral, self).__init__()
        self.virtual_ports = ()
        self.hub = parent
        self.port = port

        self.is_buffered = False

        self._subscribers = set()
        self._port_mode = MsgPortInputFmtSingle(self.port, None, False, 1)

        self._incoming_port_data = queue.Queue(1)  # limit 1 means we drop data if we can't handle it fast enough
        thr = Thread(target=self._queue_reader)
        thr.setDaemon(True)
        thr.setName("Port data queue: %s" % self)
        thr.start()

    def __repr__(self):
        msg = "%s on port 0x%x" % (self.__class__.__name__, self.port)
        if self.virtual_ports:
            msg += " (ports 0x%x and 0x%x combined)" % (self.virtual_ports[0], self.virtual_ports[1])
        return msg

    def set_port_mode(self, mode, send_updates=None, update_delta=None):
        assert not self.virtual_ports, "TODO: support combined mode for sensors"

        if send_updates is None:
            send_updates = self._port_mode.upd_enabled
            log.debug("Implied update is enabled=%s", send_updates)

        if update_delta is None:
            update_delta = self._port_mode.upd_delta
            log.debug("Implied update delta=%s", update_delta)

        if self._port_mode.mode == mode \
                and self._port_mode.upd_enabled == send_updates \
                and self._port_mode.upd_delta == update_delta:
            log.debug("Already in target mode, no need to switch")
            return
        else:
            msg = MsgPortInputFmtSetupSingle(self.port, mode, update_delta, send_updates)
            resp = self.hub.send(msg)
            assert isinstance(resp, MsgPortInputFmtSingle)
            self._port_mode = resp

    def _send_output(self, msg):
        assert isinstance(msg, MsgPortOutput)
        msg.is_buffered = self.is_buffered  # TODO: support buffering
        return self.hub.send(msg)

    def get_sensor_data(self, mode):
        self.set_port_mode(mode)
        msg = MsgPortInfoRequest(self.port, MsgPortInfoRequest.INFO_PORT_VALUE)
        resp = self.hub.send(msg)
        return self._decode_port_data(resp)

    def subscribe(self, callback, mode=0x00, granularity=1):
        if self._port_mode.mode != mode and self._subscribers:
            raise ValueError("Port is in active mode %r, unsubscribe all subscribers first" % self._port_mode)
        self.set_port_mode(mode, True, granularity)
        if callback:
            self._subscribers.add(callback)

    def unsubscribe(self, callback=None):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

        if not self._port_mode.upd_enabled:
            log.warning("Attempt to unsubscribe while port value updates are off: %s", self)
        elif not self._subscribers:
            self.set_port_mode(self._port_mode.mode, False)

    def _notify_subscribers(self, *args, **kwargs):
        for subscriber in self._subscribers.copy():
            subscriber(*args, **kwargs)
        return args

    def queue_port_data(self, msg):
        try:
            self._incoming_port_data.put_nowait(msg)
        except queue.Full:
            log.debug("Dropped port data: %r", msg)

    def _decode_port_data(self, msg):
        """
        :rtype: tuple
        """
        log.warning("Unhandled port data: %r", msg)
        return ()

    def _handle_port_data(self, msg):
        """
        :type msg: pylgbst.messages.MsgPortValueSingle
        """
        decoded = self._decode_port_data(msg)
        assert isinstance(decoded, (tuple, list)), "Unexpected data type: %s" % type(decoded)
        self._notify_subscribers(*decoded)

    def _queue_reader(self):
        while True:
            msg = self._incoming_port_data.get()
            try:
                self._handle_port_data(msg)
            except BaseException:
                log.warning("%s", traceback.format_exc())
                log.warning("Failed to handle port data by %s: %r", self, msg)

    def describe_possible_modes(self):
        mode_info = self.hub.send(MsgPortInfoRequest(self.port, MsgPortInfoRequest.INFO_MODE_INFO))
        assert isinstance(mode_info, MsgPortInfo)
        info = {
            "mode_count": mode_info.total_modes,
            "input_modes": [],
            "output_modes": [],
            "capabilities": {
                "logically_combinable": mode_info.is_combinable(),
                "synchronizable": mode_info.is_synchronizable(),
                "can_output": mode_info.is_output(),
                "can_input": mode_info.is_input(),
            }
        }

        if mode_info.is_combinable():
            mode_combinations = self.hub.send(MsgPortInfoRequest(self.port, MsgPortInfoRequest.INFO_MODE_COMBINATIONS))
            assert isinstance(mode_combinations, MsgPortInfo)
            info['possible_mode_combinations'] = mode_combinations.possible_mode_combinations

        info['modes'] = []
        for mode in range(256):
            info['modes'].append(self._describe_mode(mode))

        for mode in mode_info.output_modes:
            info['output_modes'].append(self._describe_mode(mode))

        for mode in mode_info.input_modes:
            info['input_modes'].append(self._describe_mode(mode))

        log.debug("Port info for 0x%x: %s", self.port, info)
        return info

    def _describe_mode(self, mode):
        descr = {"Mode": mode}
        for info in MsgPortModeInfoRequest.INFO_TYPES:
            try:
                resp = self.hub.send(MsgPortModeInfoRequest(self.port, mode, info))
                assert isinstance(resp, MsgPortModeInfo)
                descr[MsgPortModeInfoRequest.INFO_TYPES[info]] = resp.value
            except RuntimeError:
                log.debug("Got error while requesting info 0x%x: %s", info, traceback.format_exc())
                if info == MsgPortModeInfoRequest.INFO_NAME:
                    break
        return descr


class LEDRGB(Peripheral):
    MODE_INDEX = 0x00
    MODE_RGB = 0x01

    def __init__(self, parent, port):
        super(LEDRGB, self).__init__(parent, port)

    def set_color(self, color):
        if isinstance(color, (list, tuple)):
            assert len(color) == 3, "RGB color has to have 3 values"
            self.set_port_mode(self.MODE_RGB)
            payload = pack("<B", self.MODE_RGB) + pack("<B", color[0]) + pack("<B", color[1]) + pack("<B", color[2])
        else:
            if color == COLOR_NONE:
                color = COLOR_BLACK

            if color not in COLORS:
                raise ValueError("Color %s is not in list of available colors" % color)

            self.set_port_mode(self.MODE_INDEX)
            payload = pack("<B", self.MODE_INDEX) + pack("<B", color)

        msg = MsgPortOutput(self.port, MsgPortOutput.WRITE_DIRECT_MODE_DATA, payload)
        self._send_output(msg)

    def _decode_port_data(self, msg):
        if len(msg.payload) == 3:
            return usbyte(msg.payload, 0), usbyte(msg.payload, 1), usbyte(msg.payload, 2),
        else:
            return usbyte(msg.payload, 0),


class Motor(Peripheral):
    SUBCMD_START_POWER = 0x01
    SUBCMD_START_POWER_GROUPED = 0x02
    SUBCMD_SET_ACC_TIME = 0x05
    SUBCMD_SET_DEC_TIME = 0x06
    SUBCMD_START_SPEED = 0x07
    # SUBCMD_START_SPEED = 0x08
    SUBCMD_START_SPEED_FOR_TIME = 0x09
    # SUBCMD_START_SPEED_FOR_TIME = 0x0A

    END_STATE_BRAKE = 127
    END_STATE_HOLD = 126
    END_STATE_FLOAT = 0

    def _speed_abs(self, relative):
        if relative == Motor.END_STATE_BRAKE or relative == Motor.END_STATE_HOLD:
            # special value for BRAKE
            # https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-startpower-power
            return relative

        if relative < -1:
            log.warning("Speed cannot be less than -1")
            relative = -1

        if relative > 1:
            log.warning("Speed cannot be more than 1")
            relative = 1

        absolute = math.ceil(relative * 100)  # scale of 100 is proven by experiments
        return int(absolute)

    def _write_direct_mode(self, subcmd, params):
        params = pack("<B", subcmd) + params
        msg = MsgPortOutput(self.port, MsgPortOutput.WRITE_DIRECT_MODE_DATA, params)
        self._send_output(msg)

    def _is_successful(self, msg):
        return not msg.is_discarded()

    def _send_cmd(self, subcmd, params):
        if self.virtual_ports:
            subcmd += 1  # de-facto rule

        msg = MsgPortOutput(self.port, subcmd, params)
        return self._send_output(msg)

    def start_power(self, power_primary=1.0, power_secondary=None):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-startpower-power
        """
        if power_secondary is None:
            power_secondary = power_primary

        if self.virtual_ports:
            cmd = self.SUBCMD_START_POWER_GROUPED - 1  # because _send_cmd will do +1
        else:
            cmd = self.SUBCMD_START_POWER

        params = b""
        params += pack("<b", self._speed_abs(power_primary))
        if self.virtual_ports:
            params += pack("<b", self._speed_abs(power_secondary))

        resp = self._send_cmd(cmd, params)

        return self._is_successful(resp)

    def stop(self):
        return self.timed(0)

    def set_acc_profile(self, seconds, profile_no=0x00):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-setacctime-time-profileno-0x05
        """
        params = b""
        params += pack("<H", int(seconds * 1000))
        params += pack("<B", profile_no)

        resp = self._send_cmd(self.SUBCMD_SET_ACC_TIME, params)

        return self._is_successful(resp)

    def set_dec_profile(self, seconds, profile_no=0x00):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-setdectime-time-profileno-0x06
        """
        params = b""
        params += pack("<H", int(seconds * 1000))
        params += pack("<B", profile_no)

        resp = self._send_cmd(self.SUBCMD_SET_DEC_TIME, params)

        return self._is_successful(resp)

    def start_speed(self, speed_primary=1.0, speed_secondary=None, max_power=1.0, use_profile=0b11):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-startspeed-speed-maxpower-useprofile-0x07
        """
        if speed_secondary is None:
            speed_secondary = speed_primary

        params = b""
        params += pack("<b", self._speed_abs(speed_primary))
        if self.virtual_ports:
            params += pack("<b", self._speed_abs(speed_secondary))

        params += pack("<B", int(100 * max_power))
        params += pack("<B", use_profile)

        resp = self._send_cmd(self.SUBCMD_START_SPEED, params)

        return self._is_successful(resp)

    def timed(self, seconds, speed_primary=1.0, speed_secondary=None, max_power=1.0, end_state=END_STATE_BRAKE,
              use_profile=0b11):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-startspeedfortime-time-speed-maxpower-endstate-useprofile-0x09
        """
        if speed_secondary is None:
            speed_secondary = speed_primary

        params = b""
        params += pack("<H", int(seconds * 1000))
        params += pack("<b", self._speed_abs(speed_primary))
        if self.virtual_ports:
            params += pack("<b", self._speed_abs(speed_secondary))

        params += pack("<B", int(100 * max_power))
        params += pack("<B", end_state)
        params += pack("<B", use_profile)

        resp = self._send_cmd(self.SUBCMD_START_SPEED_FOR_TIME, params)

        return self._is_successful(resp)


class EncodedMotor(Motor):
    SUBCMD_START_SPEED_FOR_DEGREES = 0x0B
    # SUBCMD_START_SPEED_FOR_DEGREES = 0x0C
    SUBCMD_GOTO_ABSOLUTE_POSITION = 0x0D
    # SUBCMD_GOTO_ABSOLUTE_POSITIONC = 0x0E
    SUBCMD_PRESET_ENCODER = 0x14

    SENSOR_POWER = 0x00  # it's not input mode, hovewer returns some numbers
    SENSOR_SPEED = 0x01
    SENSOR_ANGLE = 0x02
    SENSOR_TEST = 0x03  # exists, but neither input nor output mode

    def angled(self, degrees, speed_primary=1.0, speed_secondary=None, max_power=1.0, end_state=Motor.END_STATE_BRAKE,
               use_profile=0b11):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-startspeedfordegrees-degrees-speed-maxpower-endstate-useprofile-0x0b
        :type degrees: int
        :type speed_primary: float
        """
        if speed_secondary is None:
            speed_secondary = speed_primary

        degrees = int(round(degrees))
        if degrees < 0:
            degrees = -degrees
            speed_primary = -speed_primary
            speed_secondary = -speed_secondary

        params = b""
        params += pack("<I", degrees)
        params += pack("<b", self._speed_abs(speed_primary))
        if self.virtual_ports:
            params += pack("<b", self._speed_abs(speed_secondary))

        params += pack("<B", int(100 * max_power))
        params += pack("<B", end_state)
        params += pack("<B", use_profile)

        resp = self._send_cmd(self.SUBCMD_START_SPEED_FOR_DEGREES, params)

        return self._is_successful(resp)

    def goto_position(self, degrees_primary, degrees_secondary=None, speed=1.0, max_power=1.0,
                      end_state=Motor.END_STATE_BRAKE, use_profile=0b11):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-gotoabsoluteposition-abspos-speed-maxpower-endstate-useprofile-0x0d
        """
        if degrees_secondary is None:
            degrees_secondary = degrees_primary

        params = b""
        params += pack("<i", degrees_primary)
        if self.virtual_ports:
            params += pack("<i", degrees_secondary)

        params += pack("<b", self._speed_abs(speed))

        params += pack("<B", int(100 * max_power))
        params += pack("<B", end_state)
        params += pack("<B", use_profile)

        resp = self._send_cmd(self.SUBCMD_GOTO_ABSOLUTE_POSITION, params)

        return self._is_successful(resp)

    def _decode_port_data(self, msg):
        data = msg.payload
        if self._port_mode.mode == self.SENSOR_ANGLE:
            angle = unpack("<l", data[0:4])[0]
            return (angle,)
        elif self._port_mode.mode == self.SENSOR_SPEED:
            speed = unpack("<b", data[0:1])[0]
            return (speed,)
        else:
            log.debug("Got motor sensor data while in unexpected mode: %r", self._port_mode)
            return ()

    def subscribe(self, callback, mode=SENSOR_ANGLE, granularity=1):
        super(EncodedMotor, self).subscribe(callback, mode, granularity)

    def preset_encoder(self, degrees=0, degrees_secondary=None, only_combined=False):
        """
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-presetencoder-position-n-a
        https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-presetencoder-leftposition-rightposition-0x14
        """
        if degrees_secondary is None:
            degrees_secondary = degrees

        if self.virtual_ports and not only_combined:
            self._send_cmd(self.SUBCMD_PRESET_ENCODER, pack("<i", degrees) + pack("<i", degrees_secondary))
        else:
            params = pack("<i", degrees)
            self._write_direct_mode(self.SENSOR_ANGLE, params)


class TiltSensor(Peripheral):
    MODE_2AXIS_ANGLE = 0x00
    MODE_2AXIS_SIMPLE = 0x01
    MODE_3AXIS_SIMPLE = 0x02
    MODE_IMPACT_COUNT = 0x03
    MODE_3AXIS_ACCEL = 0x04
    MODE_ORIENT_CF = 0x05
    MODE_IMPACT_CF = 0x06
    MODE_CALIBRATION = 0x07

    TRI_BACK = 0x00
    TRI_UP = 0x01
    TRI_DOWN = 0x02
    TRI_LEFT = 0x03
    TRI_RIGHT = 0x04
    TRI_FRONT = 0x05

    DUO_HORIZ = 0x00
    DUO_DOWN = 0x03
    DUO_LEFT = 0x05
    DUO_RIGHT = 0x07
    DUO_UP = 0x09

    DUO_STATES = {
        DUO_HORIZ: "HORIZONTAL",
        DUO_DOWN: "DOWN",
        DUO_LEFT: "LEFT",
        DUO_RIGHT: "RIGHT",
        DUO_UP: "UP",
    }

    TRI_STATES = {
        TRI_BACK: "BACK",
        TRI_UP: "UP",
        TRI_DOWN: "DOWN",
        TRI_LEFT: "LEFT",
        TRI_RIGHT: "RIGHT",
        TRI_FRONT: "FRONT",
    }

    def subscribe(self, callback, mode=MODE_3AXIS_SIMPLE, granularity=1):
        super(TiltSensor, self).subscribe(callback, mode, granularity)

    def _decode_port_data(self, msg):
        data = msg.payload
        if self._port_mode.mode == self.MODE_2AXIS_ANGLE:
            roll = unpack('<b', data[0:1])[0]
            pitch = unpack('<b', data[1:2])[0]
            return (roll, pitch)
        elif self._port_mode.mode == self.MODE_3AXIS_SIMPLE:
            state = usbyte(data, 0)
            return (state,)
        elif self._port_mode.mode == self.MODE_2AXIS_SIMPLE:
            state = usbyte(data, 0)
            return (state,)
        elif self._port_mode.mode == self.MODE_IMPACT_COUNT:
            bump_count = usint(data, 0)
            return (bump_count,)
        elif self._port_mode.mode == self.MODE_3AXIS_ACCEL:
            roll = unpack('<b', data[0:1])[0]
            pitch = unpack('<b', data[1:2])[0]
            yaw = unpack('<b', data[2:3])[0]  # did I get the order right?
            return (roll, pitch, yaw)
        elif self._port_mode.mode == self.MODE_ORIENT_CF:
            state = usbyte(data, 0)
            return (state,)
        elif self._port_mode.mode == self.MODE_IMPACT_CF:
            state = usbyte(data, 0)
            return (state,)
        elif self._port_mode.mode == self.MODE_CALIBRATION:
            return (usbyte(data, 0), usbyte(data, 1), usbyte(data, 2))
        else:
            log.debug("Got tilt sensor data while in unexpected mode: %r", self._port_mode)
            return ()

    # TODO: add some methods from official doc, like
    # https://lego.github.io/lego-ble-wireless-protocol-docs/index.html#output-sub-command-tiltconfigimpact-impactthreshold-bumpholdoff-n-a


class VisionSensor(Peripheral):
    COLOR_INDEX = 0x00
    DISTANCE_INCHES = 0x01
    COUNT_2INCH = 0x02
    DISTANCE_REFLECTED = 0x03
    AMBIENT_LIGHT = 0x04
    SET_COLOR = 0x05
    COLOR_RGB = 0x06
    SET_IR_TX = 0x07
    COLOR_DISTANCE_FLOAT = 0x08  # it's not declared by dev's mode info

    DEBUG = 0x09  # first val is by fact ambient light, second is zero
    CALIBRATE = 0x0a  # gives constant values

    def __init__(self, parent, port):
        super(VisionSensor, self).__init__(parent, port)

    def subscribe(self, callback, mode=COLOR_DISTANCE_FLOAT, granularity=1):
        super(VisionSensor, self).subscribe(callback, mode, granularity)

    def _decode_port_data(self, msg):
        data = msg.payload
        if self._port_mode.mode == self.COLOR_INDEX:
            color = usbyte(data, 0)
            return (color,)
        elif self._port_mode.mode == self.COLOR_DISTANCE_FLOAT:
            color = usbyte(data, 0)
            val = usbyte(data, 1)
            partial = usbyte(data, 3)
            if partial:
                val += 1.0 / partial
            return (color, float(val))
        elif self._port_mode.mode == self.DISTANCE_INCHES:
            val = usbyte(data, 0)
            return (val,)
        elif self._port_mode.mode == self.DISTANCE_REFLECTED:
            val = usbyte(data, 0) / 100.0
            return (val,)
        elif self._port_mode.mode == self.AMBIENT_LIGHT:
            val = usbyte(data, 0) / 100.0
            return (val,)
        elif self._port_mode.mode == self.COUNT_2INCH:
            count = usint(data, 0)
            return (count,)
        elif self._port_mode.mode == self.COLOR_RGB:
            val1 = int(255 * ushort(data, 0) / 1023.0)
            val2 = int(255 * ushort(data, 2) / 1023.0)
            val3 = int(255 * ushort(data, 4) / 1023.0)
            return (val1, val2, val3)
        elif self._port_mode.mode == self.DEBUG:
            val1 = 10 * ushort(data, 0) / 1023.0
            val2 = 10 * ushort(data, 2) / 1023.0
            return (val1, val2)
        elif self._port_mode.mode == self.CALIBRATE:
            return [ushort(data, x * 2) for x in range(8)]
        else:
            log.debug("Unhandled VisionSensor data in mode %s: %s", self._port_mode.mode, str2hex(data))
            return ()

    def set_color(self, color):
        if color == COLOR_NONE:
            color = COLOR_BLACK

        if color not in COLORS:
            raise ValueError("Color %s is not in list of available colors" % color)

        self.set_port_mode(self.SET_COLOR)
        payload = pack("<B", self.SET_COLOR) + pack("<B", color)

        msg = MsgPortOutput(self.port, MsgPortOutput.WRITE_DIRECT_MODE_DATA, payload)
        self._send_output(msg)

    def set_ir_tx(self, level=1.0):
        assert 0 <= level <= 1.0
        self.set_port_mode(self.SET_IR_TX)
        payload = pack("<B", self.SET_IR_TX) + pack("<H", int(level * 65535))

        msg = MsgPortOutput(self.port, MsgPortOutput.WRITE_DIRECT_MODE_DATA, payload)
        self._send_output(msg)


class Voltage(Peripheral):
    # sensor says there are "L" and "S" values, but what are they?
    VOLTAGE_L = 0x00
    VOLTAGE_S = 0x01

    def __init__(self, parent, port):
        super(Voltage, self).__init__(parent, port)

    def _decode_port_data(self, msg):
        data = msg.payload
        val = ushort(data, 0)
        volts = 9600.0 * val / 3893.0 / 1000.0
        return (volts,)


class Current(Peripheral):
    CURRENT_L = 0x00
    CURRENT_S = 0x01

    def __init__(self, parent, port):
        super(Current, self).__init__(parent, port)

    def _decode_port_data(self, msg):
        val = ushort(msg.payload, 0)
        milliampers = 2444 * val / 4095.0
        return (milliampers,)


class Button(Peripheral):
    """
    It's not really a peripheral, we use MSG_DEVICE_INFO commands to interact with it
    """

    def __init__(self, parent):
        super(Button, self).__init__(parent, 0)  # fake port 0
        self.hub.add_message_handler(MsgHubProperties, self._props_msg)

    def subscribe(self, callback, mode=None, granularity=1):
        self.hub.send(MsgHubProperties(MsgHubProperties.BUTTON, MsgHubProperties.UPD_ENABLE))

        if callback:
            self._subscribers.add(callback)

    def unsubscribe(self, callback=None):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

        if not self._subscribers:
            self.hub.send(MsgHubProperties(MsgHubProperties.BUTTON, MsgHubProperties.UPD_DISABLE))

    def _props_msg(self, msg):
        """
        :type msg: MsgHubProperties
        """
        if msg.property == MsgHubProperties.BUTTON and msg.operation == MsgHubProperties.UPSTREAM_UPDATE:
            self._notify_subscribers(usbyte(msg.parameters, 0))
