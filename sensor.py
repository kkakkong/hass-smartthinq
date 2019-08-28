import datetime
import logging
import time
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from custom_components.smartthinq import (
    CONF_LANGUAGE, KEY_SMARTTHINQ_DEVICES, LGDevice)
from homeassistant.const import CONF_REGION, CONF_TOKEN

import wideq
from wideq import dryer
from wideq import washer
REQUIREMENTS = ['wideq']

ATTR_WW_STATE = 'state'
ATTR_WW_DEVICETYPE = 'type'
ATTR_WW_REMAINING_TIME = 'remaining_time'
ATTR_WW_REMAINING_TIME_IN_MINUTES = 'remaining_time_in_minutes'
ATTR_WW_INITIAL_TIME = 'initial_time'
ATTR_WW_INITIAL_TIME_IN_MINUTES = 'initial_time_in_minutes'
ATTR_WW_RESERVE_TIME = 'reserve_time'
ATTR_WW_RESERVE_TIME_IN_MINUTES = 'reserve_time_in_minutes'
ATTR_WW_COURSE = 'course'
ATTR_WW_ERROR = 'error'
ATTR_WW_DRYLEVEL = 'dry_level'
ATTR_WW_ECOHYBRID = 'eco_hybrid'
ATTR_WW_ANTICREASE = 'anti_crease'
ATTR_WW_CHILDLOCK = 'child_lock'
ATTR_WW_SELFCLEANING = 'self_cleaning'
ATTR_WW_DAMPDRYBEEP = 'damp_dry_beep'
ATTR_WW_HANDIRON = 'hand_iron'
ATTR_WW_SOILLEVEL = 'soil_level'
ATTR_WW_WATERTEMP = 'water_temp'
ATTR_WW_SPINSPEED = 'spin_speed'
ATTR_WW_RINSECOUNT = 'rinse_count'
ATTR_WW_WATERLEVEL = 'water_level'
ATTR_WW_WATERFLOW = 'water_flow'
ATTR_WW_SOAK = 'soak'
ATTR_WW_FRESHCARE = 'fresh_care'
ATTR_WW_DOORLOCK = 'door_lock'
ATTR_WW_STEAM = 'steam'
ATTR_WW_TURBOSHOT = 'turbo_shot'
ATTR_WW_BUZZER = 'buzzer'
ATTR_WW_STERILIZE = 'sterilize'
ATTR_WW_HEATER = 'heater'
ATTR_WW_TUBCLEANCOUNT = 'tubclean_count'
ATTR_WW_LOADLEVEL = 'load_level'

MAX_RETRIES = 5

KEY_WW_OFF = '꺼짐'
KEY_WW_DISCONNECTED = '연결해제'

LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the LG entities"""

    refresh_token = hass.data[CONF_TOKEN]
    region = hass.data[CONF_REGION]
    language = hass.data[CONF_LANGUAGE]

    client = wideq.Client.from_token(refresh_token, region, language)
    dryers = []
    washers = []

    for device_id in hass.data[KEY_SMARTTHINQ_DEVICES]:
        device = client.get_device(device_id)
        model = client.model_info(device)
        LOGGER.debug("Device: %s" % device.type)

        if device.type == wideq.DeviceType.DRYER:
            base_name = "lg_dryer_" + device.name
            LOGGER.debug("Creating new LG Dryer: %s" % base_name)
            try:
                dryers.append(LGDryerDevice(client, device, base_name))
            except wideq.NotConnectedError:
                # Dryers are only connected when in use. Ignore
                # NotConnectedError on platform setup.
                pass
        if device.type == wideq.DeviceType.WASHER:
            base_name = "lg_washer_" + device.name
            LOGGER.debug("Creating new LG Washer: %s" % base_name)
            try:
                washers.append(LGWasherDevice(client, device, base_name))
            except wideq.NotConnectedError:
                # Washers are only connected when in use. Ignore
                # NotConnectedError on platform setup.
                pass

    if dryers:
        add_devices(dryers, True)
    if washers:
        add_devices(washers, True)
    return True


class LGDryerDevice(LGDevice):
    def __init__(self, client, device, name):
        """Initialize an LG Dryer Device."""

        super().__init__(client, device)

        # This constructor is called during platform creation. It must not
        # involve any API calls that actually need the dryer to be
        # connected, otherwise the device construction will fail and the entity
        # will not get created. Specifically, calls that depend on dryer
        # interaction should only happen in update(...), including the start of
        # the monitor task.
        self._dryer = dryer.DryerDevice(client, device)
        self._name = name
        self._status = None
        self._failed_request_count = 0

    @property
    def state_attributes(self):
        """Return the optional state attributes for the dryer."""
        data = {}
        data[ATTR_WW_REMAINING_TIME] = self.remaining_time
        data[ATTR_WW_REMAINING_TIME_IN_MINUTES] = self.remaining_time_in_minutes
        data[ATTR_WW_INITIAL_TIME] = self.initial_time
        data[ATTR_WW_INITIAL_TIME_IN_MINUTES] = self.initial_time_in_minutes
        data[ATTR_WW_RESERVE_TIME] = self.reserve_time
        data[ATTR_WW_RESERVE_TIME_IN_MINUTES] = self.reserve_time_in_minutes
        data[ATTR_WW_COURSE] = self.course
        data[ATTR_WW_ERROR] = self.error
        data[ATTR_WW_DRYLEVEL] = self.dry_level
        data[ATTR_WW_ECOHYBRID] = self.eco_hybrid
        data[ATTR_WW_ANTICREASE] = self.anti_crease
        data[ATTR_WW_CHILDLOCK] = self.child_lock
        data[ATTR_WW_SELFCLEANING] = self.self_cleaning
        data[ATTR_WW_DAMPDRYBEEP] = self.damp_dry_beep
        data[ATTR_WW_HANDIRON] = self.hand_iron

        # For convenience, include the state as an attribute.
        data[ATTR_WW_STATE] = self.state
        return data

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        if self._status:
            return self._status.state
        return KEY_WW_OFF

    @property
    def remaining_time(self):
        minutes = self.remaining_time_in_minutes if self._status else 0
        return str(datetime.timedelta(minutes=minutes))[:-3]

    @property
    def remaining_time_in_minutes(self):
        # The API (indefinitely) returns 1 minute remaining when a cycle is
        # either in state off or complete, or process night-drying. Return 0
        # minutes remaining in these instances, which is more reflective of
        # reality.
        return self._status.remaining_time if self._status else 0

    @property
    def initial_time(self):
        minutes = self.initial_time_in_minutes if self._status else 0
        return str(datetime.timedelta(minutes=minutes))[:-3]

    @property
    def initial_time_in_minutes(self):
        # When in state OFF, the dryer still returns the initial program
        # length of the previously ran cycle. Instead, return 0 which is more
        # reflective of the dryer being off.
        return self._status.initial_time if self._status else 0

    @property
    def reserve_time(self):
        minutes = self.reserve_time_in_minutes if self._status else 0
        return str(datetime.timedelta(minutes=minutes))[:-3]

    @property
    def reserve_time_in_minutes(self):
        return self._status.reserve_time if self._status else 0

    @property
    def course(self):
        if self._status:
            if self._status.smart_course != KEY_WW_OFF:
                return self._status.smart_course
            else:
                return self._status.course
        return KEY_WW_OFF

    @property
    def error(self):
        if self._status:
            return self._status.error
        return KEY_WW_DISCONNECTED

    def _restart_monitor(self):
        try:
            self._dryer.monitor_start()
        except wideq.NotConnectedError:
            self._status = None
        except wideq.NotLoggedInError:
            LOGGER.info('Session expired. Refreshing.')
            self._client.refresh()

    @property
    def dry_level(self):
        if self._status:
            if self._status.dry_level != KEY_WW_OFF:
                return self._status.dry_level
        return KEY_WW_OFF

    @property
    def eco_hybrid(self):
        if self._status:
            if self._status.eco_hybrid != KEY_WW_OFF:
                return self._status.eco_hybrid
        return KEY_WW_OFF

    @property
    def anti_crease(self):
        if self._status:
            if self._status.anti_crease != KEY_WW_OFF:
                return self._status.anti_crease
        return KEY_WW_OFF

    @property
    def child_lock(self):
        if self._status:
            if self._status.child_lock != KEY_WW_OFF:
                return self._status.child_lock
        return KEY_WW_OFF

    @property
    def self_cleaning(self):
        if self._status:
            if self._status.self_cleaning != KEY_WW_OFF:
                return self._status.self_cleaning
        return KEY_WW_OFF

    @property
    def damp_dry_beep(self):
        if self._status:
            if self._status.damp_dry_beep != KEY_WW_OFF:
                return self._status.damp_dry_beep
        return KEY_WW_OFF

    @property
    def hand_iron(self):
        if self._status:
            if self._status.hand_iron != KEY_WW_OFF:
                return self._status.hand_iron
        return KEY_WW_OFF

    def update(self):
        """Poll for dryer state updates."""

        # This method is polled, so try to avoid sleeping in here. If an error
        # occurs, it will naturally be retried on the next poll.

        LOGGER.debug('Updating %s.', self.name)

        # On initial construction, the dryer monitor task
        # will not have been created. If so, start monitoring here.
        if getattr(self._dryer, 'mon', None) is None:
            self._restart_monitor()

        try:
            status = self._dryer.poll()
        except wideq.NotConnectedError:
            self._status = None
            return
        except wideq.NotLoggedInError:
            LOGGER.info('Session expired. Refreshing.')
            self._client.refresh()
            self._restart_monitor()
            return

        if status:
            LOGGER.debug('Status updated.')
            self._status = status
            self._failed_request_count = 0
            return

        LOGGER.debug('No status available yet.')
        self._failed_request_count += 1

        if self._failed_request_count >= MAX_RETRIES:
            # We tried several times but got no result. This might happen
            # when the monitoring request gets into a bad state, so we
            # restart the task.
            self._restart_monitor()
            self._failed_request_count = 0


class LGWasherDevice(LGDevice):
    def __init__(self, client, device, name):
        """Initialize an LG Washer Device."""

        super().__init__(client, device)

        # This constructor is called during platform creation. It must not
        # involve any API calls that actually need the washer to be
        # connected, otherwise the device construction will fail and the entity
        # will not get created. Specifically, calls that depend on washer
        # interaction should only happen in update(...), including the start of
        # the monitor task.
        self._washer = washer.WasherDevice(client, device)
        self._name = name
        self._status = None
        self._failed_request_count = 0

    @property
    def state_attributes(self):
        """Return the optional state attributes for the washer."""
        data = {}
        data[ATTR_WW_DEVICETYPE] = self.device_type
        data[ATTR_WW_REMAINING_TIME] = self.remaining_time
        data[ATTR_WW_REMAINING_TIME_IN_MINUTES] = self.remaining_time_in_minutes
        data[ATTR_WW_INITIAL_TIME] = self.initial_time
        data[ATTR_WW_INITIAL_TIME_IN_MINUTES] = self.initial_time_in_minutes
        data[ATTR_WW_RESERVE_TIME] = self.reserve_time
        data[ATTR_WW_RESERVE_TIME_IN_MINUTES] = self.reserve_time_in_minutes
        data[ATTR_WW_COURSE] = self.course
        data[ATTR_WW_ERROR] = self.error
        data[ATTR_WW_SOILLEVEL] = self.soil_level
        data[ATTR_WW_WATERTEMP] = self.water_temp
        data[ATTR_WW_SPINSPEED] = self.spin_speed
        data[ATTR_WW_RINSECOUNT] = self.rinse_count
        data[ATTR_WW_CHILDLOCK] = self.child_lock
        data[ATTR_WW_STEAM] = self.steam
        data[ATTR_WW_TURBOSHOT] = self.turbo_shot

        if self.device_type == 'FL':
            data[ATTR_WW_DRYLEVEL] = self.dry_level
            data[ATTR_WW_FRESHCARE] = self.fresh_care
            data[ATTR_WW_TUBCLEANCOUNT] = self.tubclean_count
            data[ATTR_WW_LOADLEVEL] = self.load_level
        elif self.device_type == 'TL':
            data[ATTR_WW_WATERLEVEL] = self.water_level
            data[ATTR_WW_WATERFLOW] = self.water_flow
            data[ATTR_WW_SOAK] = self.soak
            data[ATTR_WW_DOORLOCK] = self.door_lock
            data[ATTR_WW_BUZZER] = self.buzzer
            data[ATTR_WW_STERILIZE] = self.sterilize
            data[ATTR_WW_HEATER] = self.heater

        # For convenience, include the state as an attribute.
        data[ATTR_WW_STATE] = self.state
        return data

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        if self._status:
            return self._status.state
        return KEY_WW_OFF

    @property
    def device_type(self):
        if self._status:
            return self._status.device_type
        return KEY_WW_OFF

    @property
    def remaining_time(self):
        minutes = self.remaining_time_in_minutes if self._status else 0
        return str(datetime.timedelta(minutes=minutes))[:-3]

    @property
    def remaining_time_in_minutes(self):
        # The API (indefinitely) returns 1 minute remaining when a cycle is
        # either in state off or complete, or process night-drying. Return 0
        # minutes remaining in these instances, which is more reflective of
        # reality.
        return self._status.remaining_time if self._status else 0

    @property
    def initial_time(self):
        minutes = self.initial_time_in_minutes if self._status else 0
        return str(datetime.timedelta(minutes=minutes))[:-3]

    @property
    def initial_time_in_minutes(self):
        # When in state OFF, the washer still returns the initial program
        # length of the previously ran cycle. Instead, return 0 which is more
        # reflective of the washer being off.
        return self._status.initial_time if self._status else 0

    @property
    def reserve_time(self):
        minutes = self.reserve_time_in_minutes if self._status else 0
        return str(datetime.timedelta(minutes=minutes))[:-3]

    @property
    def reserve_time_in_minutes(self):
        return self._status.reserve_time if self._status else 0

    @property
    def course(self):
        if self._status:
            return self._status.course
        return KEY_WW_OFF

    @property
    def smart_course(self):
        if self._status:
            return self._status.smart_course
        return KEY_WW_OFF

    @property
    def error(self):
        if self._status:
            return self._status.error
        return KEY_WW_DISCONNECTED

    @property
    def soil_level(self):
        if self._status:
            if self._status.soil_level != KEY_WW_OFF:
                return self._status.soil_level
        return KEY_WW_OFF

    @property
    def water_temp(self):
        if self._status:
            if self._status.water_temp != KEY_WW_OFF:
                return self._status.water_temp
        return KEY_WW_OFF

    @property
    def spin_speed(self):
        if self._status:
            if self._status.spin_speed != KEY_WW_OFF:
                return self._status.spin_speed
        return KEY_WW_OFF

    @property
    def rinse_count(self):
        if self._status:
            if self._status.rinse_count != KEY_WW_OFF:
                return self._status.rinse_count
        return KEY_WW_OFF

    @property
    def dry_level(self):
        if self._status:
            if self._status.dry_level != KEY_WW_OFF:
                return self._status.dry_level
        return KEY_WW_OFF

    @property
    def water_level(self):
        if self._status:
            if self._status.water_level != KEY_WW_OFF:
                return self._status.water_level
        return KEY_WW_OFF

    @property
    def water_flow(self):
        if self._status:
            if self._status.water_flow != KEY_WW_OFF:
                return self._status.water_flow
        return KEY_WW_OFF

    @property
    def soak(self):
        if self._status:
            if self._status.soak != KEY_WW_OFF:
                return self._status.soak
        return KEY_WW_OFF

    @property
    def fresh_care(self):
        if self._status:
            if self._status.fresh_care != KEY_WW_OFF:
                return self._status.fresh_care
        return KEY_WW_OFF

    @property
    def child_lock(self):
        if self._status:
            if self._status.child_lock != KEY_WW_OFF:
                return self._status.child_lock
        return KEY_WW_OFF

    @property
    def door_lock(self):
        if self._status:
            if self._status.door_lock != KEY_WW_OFF:
                return self._status.door_lock
        return KEY_WW_OFF

    @property
    def steam(self):
        if self._status:
            if self._status.steam != KEY_WW_OFF:
                return self._status.steam
        return KEY_WW_OFF

    @property
    def turbo_shot(self):
        if self._status:
            if self._status.turbo_shot != KEY_WW_OFF:
                return self._status.turbo_shot
        return KEY_WW_OFF

    @property
    def buzzer(self):
        if self._status:
            if self._status.buzzer != KEY_WW_OFF:
                return self._status.buzzer
        return KEY_WW_OFF

    @property
    def sterilize(self):
        if self._status:
            if self._status.sterilize != KEY_WW_OFF:
                return self._status.sterilize
        return KEY_WW_OFF

    @property
    def heater(self):
        if self._status:
            if self._status.heater != KEY_WW_OFF:
                return self._status.heater
        return KEY_WW_OFF

    @property
    def tubclean_count(self):
        if self._status:
            if self._status.tubclean_count != KEY_WW_OFF:
                return self._status.tubclean_count
        return KEY_WW_OFF

    @property
    def load_level(self):
        if self._status:
            if self._status.load_level != KEY_WW_OFF:
                return self._status.load_level
        return KEY_WW_OFF

    def _restart_monitor(self):
        try:
            self._washer.monitor_start()
        except wideq.NotConnectedError:
            self._status = None
        except wideq.NotLoggedInError:
            LOGGER.info('Session expired. Refreshing.')
            self._client.refresh()

    def update(self):
        """Poll for washer state updates."""

        # This method is polled, so try to avoid sleeping in here. If an error
        # occurs, it will naturally be retried on the next poll.

        LOGGER.debug('Updating %s.', self.name)

        # On initial construction, the washer monitor task
        # will not have been created. If so, start monitoring here.
        if getattr(self._washer, 'mon', None) is None:
            self._restart_monitor()

        try:
            status = self._washer.poll()
        except wideq.NotConnectedError:
            self._status = None
            return
        except wideq.NotLoggedInError:
            LOGGER.info('Session expired. Refreshing.')
            self._client.refresh()
            self._restart_monitor()
            return

        if status:
            LOGGER.debug('Status updated.')
            self._status = status
            self._failed_request_count = 0
            return

        LOGGER.debug('No status available yet.')
        self._failed_request_count += 1

        if self._failed_request_count >= MAX_RETRIES:
            # We tried several times but got no result. This might happen
            # when the monitoring request gets into a bad state, so we
            # restart the task.
            self._restart_monitor()
            self._failed_request_count = 0
