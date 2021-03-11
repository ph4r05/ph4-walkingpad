#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import binascii
import logging

from bleak import discover
from bleak import BleakClient

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self):
        self.devices_dict = {}
        self.devices_list = []
        self.receive_data = []

    async def scan(self):
        logger.info("Scanning for peripherals...")
        dev = await discover()
        for i in range(len(dev)):
            # Print the devices discovered
            info_str = ', '.join(["[%2d]" % i, str(dev[i].address), str(dev[i].name), str(dev[i].metadata["uuids"])])
            logger.info("Device: %s" % info_str)

            # Put devices information into list
            self.devices_dict[dev[i].address] = []
            self.devices_dict[dev[i].address].append(dev[i].name)
            self.devices_dict[dev[i].address].append(dev[i].metadata["uuids"])
            self.devices_list.append(dev[i].address)


class WalkingPad:
    MODE_STANDBY = 2
    MODE_MANUAL = 1
    MODE_AUTOMAT = 0

    PREFS_MAX_SPEED = 3
    PREFS_START_SPEED = 4
    PREFS_START_INTEL = 5
    PREFS_SENSITIVITY = 6
    PREFS_DISPLAY = 7
    PREFS_CHILD_LOCK = 9
    PREFS_UNITS = 8
    PREFS_TARGET = 1

    TARGET_NONE = 0
    TARGET_DIST = 1
    TARGET_CAL = 2
    TARGET_TIME = 3

    BUTTON_None = 0
    BUTTON_Down = 4
    BUTTON_Stop = 3
    BUTTON_Up = 2
    BUTTON_long_mode = -6
    BUTTON_up = -4
    BUTTON_mode = -6

    PAYLOADS_255 = [
        [247, 165, 96, 74, 77, 147, 113, 41, 201, 253],
        [247, 165, 96, 74, 58, 60, 113, 41, 95, 253],
        [247, 165, 96, 74, 15, 165, 113, 41, 157, 253],
        [247, 165, 96, 74, 21, 129, 113, 41, 127, 253],
        [247, 165, 96, 74, 45, 189, 115, 171, 87, 253],
        [247, 165, 96, 74, 49, 42, 113, 41, 68, 253],
        [247, 165, 96, 74, 58, 60, 113, 41, 95, 253],
        [247, 165, 96, 74, 77, 147, 113, 41, 201, 253],
    ]

    @staticmethod
    def int2byte(val, width=3):
        return [(val >> (8 * (width - 1 - i)) & 0xff) for i in range(width)]

    @staticmethod
    def byte2int(val, width=3):
        return sum([(val[i] << (8 * (width - 1 - i))) for i in range(width)])

    @staticmethod
    def fix_crc(cmd):
        cmd[-2] = sum(cmd[1:-2]) % 256
        return cmd


class WalkingPadCurStatus:
    def __init__(self):
        self.raw = None
        self.dist = 0
        self.time = 0
        self.steps = 0
        self.speed = 0
        self.controller_button = 0
        self.app_speed = 0
        self.belt_state = 0
        self.manual_mode = 0

    def load_from(self, cmd):
        self.raw = bytearray(cmd)
        self.belt_state = cmd[2]
        self.speed = cmd[3]
        self.manual_mode = cmd[4]
        self.time = WalkingPad.byte2int(cmd[5:])
        self.dist = WalkingPad.byte2int(cmd[8:])
        self.steps = WalkingPad.byte2int(cmd[11:])
        self.app_speed = cmd[14]  # / 30
        self.controller_button = cmd[16]

    @staticmethod
    def check_type(cmd):
        return bytes(cmd[0:2]) == bytes([248, 162])

    @staticmethod
    def from_data(cmd):
        if not WalkingPadCurStatus.check_type(cmd):
            raise ValueError('Incorrect message type, could not parse')
        m = WalkingPadCurStatus()
        m.load_from(cmd)
        return m

    def __str__(self):
        return 'WalkingPadCurStatus(dist=%s, time=%s, steps=%s, speed=%s, state=%s, ' \
               'mode=%s, app_speed=%s, button=%s, rest=%s)' \
               % (self.dist / 100, self.time, self.steps, self.speed / 10, self.belt_state,
                  self.manual_mode, self.app_speed / 30 if self.app_speed > 0 else 0, self.manual_mode,
                  binascii.hexlify(bytearray([self.raw[15], self.raw[17]])).decode('utf8'))


class WalkingPadLastStatus:
    def __init__(self):
        self.raw = None
        self.dist = 0
        self.time = 0
        self.steps = 0

    def load_from(self, cmd):
        self.raw = bytearray(cmd)
        self.time = WalkingPad.byte2int(cmd[8:])
        self.dist = WalkingPad.byte2int(cmd[11:])
        self.steps = WalkingPad.byte2int(cmd[14:])

    @staticmethod
    def check_type(cmd):
        return bytes(cmd[0:2]) == bytes([248, 167])

    @staticmethod
    def from_data(cmd):
        if not WalkingPadLastStatus.check_type(cmd):
            raise ValueError('Incorrect message type, could not parse')
        m = WalkingPadLastStatus()
        m.load_from(cmd)
        return m

    def __str__(self):
        return 'WalkingPadLastStatus(dist=%s, time=%s, steps=%s, rest=%s)' \
               % (self.dist / 100, self.time, self.steps, binascii.hexlify(self.raw[2:8]).decode('utf8'))


class Controller:
    def __init__(self):
        self.char_fe01 = None
        self.char_fe02 = None
        self.client = None
        self.last_raw_cmd = None
        self.last_status = None
        self.last_record = None

    def notif_handler(self, sender, data):
        logger.info('Msg: %s' % (', '.join('{:02x}'.format(x) for x in data)))
        if WalkingPadCurStatus.check_type(data):
            m = WalkingPadCurStatus.from_data(data)
            self.last_status = m
            logger.info('Status: %s' % (m,))

        elif WalkingPadLastStatus.check_type(data):
            m = WalkingPadLastStatus.from_data(data)
            self.last_record = None
            logger.info('Record: %s' % (m,))

    def fix_crc(self, cmd):
        return WalkingPad.fix_crc(cmd)

    async def disconnect(self):
        await self.client.disconnect()

    async def connect(self, address):
        self.client = BleakClient(address)
        return await self.client.connect()

    async def send_cmd(self, cmd):
        self.fix_crc(cmd)
        return await self.send_cmd_raw(cmd)

    async def send_cmd_raw(self, cmd):
        r = await self.client.write_gatt_char(self.char_fe02, cmd)
        return r

    async def switch_mode(self, mode: int):
        cmd = bytearray([247, 162, 2, mode, 0xff, 253])
        return await self.send_cmd(cmd)

    async def change_speed(self, speed: int):
        cmd = bytearray([247, 162, 1, speed, 0xff, 253])
        return await self.send_cmd(cmd)

    async def stop_belt(self):
        return await self.change_speed(0)

    async def start_belt(self):
        cmd = bytearray([247, 162, 4, 1, 0xff, 253])
        return await self.send_cmd(cmd)

    async def ask_profile(self, profile_idx=0):
        cmd = bytearray(WalkingPad.PAYLOADS_255[profile_idx])
        return await self.send_cmd(cmd)

    async def ask_stats(self):
        cmd = bytearray([247, 162, 0, 0, 162, 253])
        return await self.send_cmd(cmd)

    async def ask_hist(self, mode=0):
        cmd = bytearray([247, 167, 170, 255, 80, 253] if mode == 0 else [247, 167, 170, 0, 81, 253])
        return await self.send_cmd(cmd)

    async def cmd_162_3_7(self, mode=0):
        cmd = bytearray([247, 162, 3, 7, 172, 253])
        return await self.send_cmd(cmd)

    async def set_pref_arr(self, key: int, arr):
        cmd = bytearray([247, 166, key, *arr, 172, 253])
        return await self.send_cmd(cmd)

    async def set_pref_int(self, key: int, val: int, stype: int = 0):
        arr = [stype, *WalkingPad.int2byte(val)]
        return await self.set_pref_arr(key, arr)

    async def set_pref_max_speed(self, speed):
        return await self.set_pref_int(WalkingPad.PREFS_MAX_SPEED, speed)

    async def set_pref_start_speed(self, speed):
        return await self.set_pref_int(WalkingPad.PREFS_START_SPEED, speed)

    async def set_pref_inteli(self, enabled=False):
        return await self.set_pref_int(WalkingPad.PREFS_START_INTEL, int(enabled))

    async def set_pref_sensitivity(self, sensitivity=3):  # 1 = high, 2 = medium, 3 = low
        return await self.set_pref_int(WalkingPad.PREFS_SENSITIVITY, sensitivity)

    async def set_pref_display(self, bit_mask: int):  # 7bits
        return await self.set_pref_int(WalkingPad.PREFS_DISPLAY, bit_mask)

    async def set_pref_child_lock(self, enabled=False):
        return await self.set_pref_int(WalkingPad.PREFS_CHILD_LOCK, int(enabled))

    async def set_pref_units_miles(self, enabled=False):
        return await self.set_pref_int(WalkingPad.PREFS_UNITS, int(enabled))

    async def set_pref_target(self, target_type: int = 0, value: int = 0):
        return await self.set_pref_int(WalkingPad.PREFS_TARGET, value, target_type)

    async def run(self, address):
        await self.connect(address)
        client = self.client

        x = await client.is_connected()
        logger.info("Connected: {0}".format(x))

        self.char_fe01 = None
        self.char_fe02 = None

        for service in client.services:
            logger.info("[Service] {0}: {1}".format(service.uuid, service.description))
            for char in service.characteristics:
                if "read" in char.properties:
                    try:
                        value = None  # bytes(await client.read_gatt_char(char.uuid))
                    except Exception as e:
                        logger.debug('read failed for %s' % (char.uuid,))
                        value = str(e).encode()
                else:
                    value = None
                logger.info(
                    "\t[Characteristic] {0}: (Handle: {1}) ({2}) | Name: {3}, Value: {4} ".format(
                        char.uuid,
                        char.handle,
                        ",".join(char.properties),
                        char.description,
                        value,
                    )
                )

                if char.uuid.startswith('0000fe01'):
                    self.char_fe01 = char

                if char.uuid.startswith('0000fe02'):
                    self.char_fe02 = char

                for descriptor in char.descriptors:
                    value = await client.read_gatt_descriptor(descriptor.handle)
                    logger.info(
                        "\t\t[Descriptor] {0}: (Handle: {1}) | Value: {2} ".format(
                            descriptor.uuid, descriptor.handle, bytes(value)
                        )
                    )

        try:
            CHARACTERISTIC_UUID = self.char_fe01.uuid
            logger.info('Enabling notification for %s' % (CHARACTERISTIC_UUID,))
            await client.start_notify(CHARACTERISTIC_UUID, self.notif_handler)
        except Exception as e:
            logger.warning("Notify failed: %s" % (e,))

        logger.info('Service enumeration done')



