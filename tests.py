import unittest
from binascii import unhexlify

import pygatt
import serial
from pygatt import BLEAddressType
from pygatt.backends.bgapi.bgapi import MAX_CONNECTION_ATTEMPTS
from pygatt.backends.bgapi.device import BGAPIBLEDevice

from pylgbst.comms import Connection
from pylgbst.comms_bluegiga import BlueGigaConnection
from pylgbst.movehub import MoveHub
from pylgbst.peripherals import *

HANDLE = MOVE_HUB_HARDWARE_HANDLE

logging.basicConfig(level=logging.DEBUG)

log = logging.getLogger('test')


class ConnectionMock(Connection):
    """
    For unit testing purposes
    """

    def __init__(self):
        super(ConnectionMock, self).__init__()
        self.writes = []
        self.notifications = []
        self.notification_handler = None
        self.running = True
        self.finished = False

    def set_notify_handler(self, handler):
        self.notification_handler = handler
        thr = Thread(target=self.notifier)
        thr.setDaemon(True)
        thr.start()

    def notifier(self):
        while self.running:
            if self.notification_handler:
                while self.notifications:
                    handle, data = self.notifications.pop(0)
                    self.notification_handler(handle, unhexlify(data.replace(' ', '')))
            time.sleep(0.1)

        self.finished = True

    def write(self, handle, data):
        log.debug("Writing to %s: %s", handle, str2hex(data))
        self.writes.append((handle, str2hex(data)))


class HubMock(MoveHub):
    # noinspection PyUnresolvedReferences
    def __init__(self, connection=None):
        """
        :type connection: ConnectionMock
        """
        super(HubMock, self).__init__(connection if connection else ConnectionMock())
        self.notify_mock = self.connection.notifications
        self.writes = self.connection.writes

    def _wait_for_devices(self):
        pass

    def _report_status(self):
        pass


class GeneralTest(unittest.TestCase):
    def _wait_notifications_handled(self, hub):
        hub.connection.running = False
        for _ in range(1, 180):
            time.sleep(1)
            log.debug("Waiting for notifications to process...")
            if hub.connection.finished:
                log.debug("Done waiting")
                break

    def test_led(self):
        hub = HubMock()
        led = LED(hub, PORT_LED)
        led.set_color(COLOR_RED)
        self.assertEqual("0801813201510009", hub.writes[0][1])

    def test_tilt_sensor(self):
        hub = HubMock()
        hub.notify_mock.append((HANDLE, '1b0e00 0f00 04 3a 0128000000000100000001'))
        time.sleep(1)

        def callback(param1, param2=None, param3=None):
            if param2 is None:
                log.debug("Tilt: %s", TiltSensor.DUO_STATES[param1])
            else:
                log.debug("Tilt: %s %s %s", param1, param2, param3)

        self._inject_notification(hub, '1b0e000a00 47 3a 090100000001', 1)
        hub.tilt_sensor.subscribe(callback)
        hub.notify_mock.append((HANDLE, "1b0e000500453a05"))
        hub.notify_mock.append((HANDLE, "1b0e000a00473a010100000001"))
        time.sleep(1)
        self._inject_notification(hub, '1b0e000a00 47 3a 090100000001', 1)
        hub.tilt_sensor.subscribe(callback, TiltSensor.MODE_2AXIS_SIMPLE)

        hub.notify_mock.append((HANDLE, "1b0e000500453a09"))
        time.sleep(1)

        self._inject_notification(hub, '1b0e000a00 47 3a 090100000001', 1)
        hub.tilt_sensor.subscribe(callback, TiltSensor.MODE_2AXIS_FULL)
        hub.notify_mock.append((HANDLE, "1b0e000600453a04fe"))
        time.sleep(1)

        self._wait_notifications_handled(hub)
        hub.tilt_sensor.unsubscribe(callback)
        # TODO: assert

    def test_motor(self):
        conn = ConnectionMock()
        conn.notifications.append((14, '1b0e00 0900 04 39 0227003738'))
        hub = HubMock(conn)
        time.sleep(0.1)

        conn.notifications.append((14, '1b0e00050082390a'))
        hub.motor_AB.timed(1.5)
        self.assertEqual("0d018139110adc056464647f03", conn.writes[0][1])

        conn.notifications.append((14, '1b0e00050082390a'))
        hub.motor_AB.angled(90)
        self.assertEqual("0f018139110c5a0000006464647f03", conn.writes[1][1])

    def test_capabilities(self):
        conn = ConnectionMock()
        conn.notifications.append((14, '1b0e00 0f00 04 01 0125000000001000000010'))
        conn.notifications.append((14, '1b0e00 0f00 04 02 0126000000001000000010'))
        conn.notifications.append((14, '1b0e00 0f00 04 37 0127000100000001000000'))
        conn.notifications.append((14, '1b0e00 0f00 04 38 0127000100000001000000'))
        conn.notifications.append((14, '1b0e00 0900 04 39 0227003738'))
        conn.notifications.append((14, '1b0e00 0f00 04 32 0117000100000001000000'))
        conn.notifications.append((14, '1b0e00 0f00 04 3a 0128000000000100000001'))
        conn.notifications.append((14, '1b0e00 0f00 04 3b 0115000200000002000000'))
        conn.notifications.append((14, '1b0e00 0f00 04 3c 0114000200000002000000'))
        conn.notifications.append((14, '1b0e00 0f00 8202 01'))
        conn.notifications.append((14, '1b0e00 0f00 8202 0a'))

        self._inject_notification(conn, '1b0e00 1200 0101 06 4c45474f204d6f766520487562', 1)
        self._inject_notification(conn, '1b0e00 1200 0108 06 4c45474f204d6f766520487562', 2)
        self._inject_notification(conn, '1b0e00 0900 47 3c 0227003738', 3)
        self._inject_notification(conn, '1b0e00 0600 45 3c 020d', 4)
        hub = MoveHub(conn)
        # demo_all(hub)
        self._wait_notifications_handled(hub)

    def test_color_sensor(self):
        #
        hub = HubMock()
        hub.notify_mock.append((HANDLE, '1b0e000f00 04010125000000001000000010'))
        time.sleep(1)

        def callback(color, unk1, unk2=None):
            name = COLORS[color] if color is not None else 'NONE'
            log.info("Color: %s %s %s", name, unk1, unk2)

        self._inject_notification(hub, '1b0e000a00 4701090100000001', 1)
        hub.color_distance_sensor.subscribe(callback)

        hub.notify_mock.append((HANDLE, "1b0e0008004501ff0aff00"))
        time.sleep(1)
        # TODO: assert
        self._wait_notifications_handled(hub)
        hub.color_distance_sensor.unsubscribe(callback)

    def test_button(self):
        hub = HubMock()
        time.sleep(1)

        def callback(pressed):
            log.info("Pressed: %s", pressed)

        hub.notify_mock.append((HANDLE, "1b0e00060001020600"))
        hub.button.subscribe(callback)

        hub.notify_mock.append((HANDLE, "1b0e00060001020601"))
        hub.notify_mock.append((HANDLE, "1b0e00060001020600"))
        time.sleep(1)
        # TODO: assert
        self._wait_notifications_handled(hub)
        hub.button.unsubscribe(callback)

    def _inject_notification(self, hub, notify, pause):
        def inject():
            time.sleep(pause)
            if isinstance(hub, ConnectionMock):
                hub.notifications.append((HANDLE, notify))
            else:
                hub.notify_mock.append((HANDLE, notify))

        Thread(target=inject).start()


class SerialMock(serial.Serial):
    def write(self, data):
        self.is_open = True
        logging.debug("Write data to serial: %s", data)
        return len(data)

    def flush(self, *args, **kwargs):
        pass

    def close(self):
        pass

    def read(self, size=1):
        return bytes("")


class BGAPIBLEDeviceMock(BGAPIBLEDevice):
    def subscribe(self, uuid, callback=None, indication=False):
        log.debug("Mock subscribing")

    def char_write_handle(self, char_handle, value, wait_for_response=False):
        log.debug("Mock write: %s", value)


class BlueGigaBackendMock(pygatt.BGAPIBackend):
    def _open_serial_port(self, max_connection_attempts=MAX_CONNECTION_ATTEMPTS):
        log.debug("Mock open serial port")
        self._ser = SerialMock()

    def expect(self, expected, *args, **kargs):
        log.debug("Mock expect")
        data = {
            "packet_type": 0x04,
            "sender": "abcdef",
            "data": [1, 2, 3],
            "rssi": 1
        }
        self._ble_evt_gap_scan_response(data)

    def connect(self, address, timeout=5, address_type=BLEAddressType.public, interval_min=60, interval_max=76,
                supervision_timeout=100, latency=0):
        log.debug("Mock connect")
        device = BGAPIBLEDeviceMock("address", 0, self)
        return device


class BlueGigaTests(unittest.TestCase):
    def test_1(self):
        obj = BlueGigaConnection()
        obj.backend = BlueGigaBackendMock
        obj.connect(u'66:65:64:63:62:61')
        obj.write(0, "test")
        obj.set_notify_handler(lambda x: None)
