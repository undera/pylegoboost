import logging
import time
import unittest
from threading import Thread

from pylgbst import MoveHub, COLOR_RED, LED, EncodedMotor, PORT_AB
from pylgbst.comms import Connection, str2hex
from pylgbst.constants import PORT_LED

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
                    self.notification_handler(handle, hex2str(data.replace(' ', '')))
            time.sleep(0.1)

        self.finished = True

    def write(self, handle, data):
        log.debug("Writing to %s: %s", handle, str2hex(data))
        self.writes.append((handle, str2hex(data)))

    def read(self, handle):
        log.debug("Reading from: %s", handle)
        return None  # TODO


class GeneralTest(unittest.TestCase):
    def test_led(self):
        conn = ConnectionMock()
        conn.notifications.append((14, '1b0e00 0900 04 39 0227003738'))
        hub = MoveHub(conn)
        led = LED(hub, PORT_LED)
        led.set_color(COLOR_RED)
        self.assertEquals("0701813211510009", conn.writes[1][1])

    def test_motor(self):
        conn = ConnectionMock()
        conn.notifications.append((14, '1b0e00 0900 04 39 0227003738'))
        hub = MoveHub(conn)
        motor = EncodedMotor(hub, PORT_AB)
        motor.timed(1.5)
        self.assertEquals("0c018139110adc056464647f03", conn.writes[1][1])
        motor.angled(90)
        self.assertEquals("0e018139110c5a0000006464647f03", conn.writes[2][1])

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

        hub = MoveHub(conn)
        # demo_all(hub)
        conn.running = False

        while not conn.finished:
            time.sleep(0.1)
