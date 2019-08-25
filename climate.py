import logging
import time
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_REGION, CONF_TOKEN
from homeassistant.components import climate
from homeassistant import const
from homeassistant.components.climate import const as c_const
from custom_components.smartthinq import (
    CONF_LANGUAGE, KEY_SMARTTHINQ_DEVICES, LGDevice)

import wideq
from wideq import dehum

LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = climate.PLATFORM_SCHEMA.extend({
    vol.Required(KEY_DEPRECATED_REFRESH_TOKEN): cv.string,
    KEY_DEPRECATED_COUNTRY: cv.string,
    KEY_DEPRECATED_LANGUAGE: cv.string,
})

MODES = {
    'SMART': '스마트제습',
    'SPEED': '쾌속제습',
    'SILENT': '저소음제습',
    'FOCUS': '집중건조',
    'CLOTHES': '의류건조',
}
FAN_MODES = {
    'LOW': c_const.FAN_LOW,
    'HIGH': c_const.FAN_HIGH,
}

MAX_RETRIES = 5
TRANSIENT_EXP = 5.0  # Report set temperature for 5 seconds.
HUM_MIN = 30
HUM_MAX = 70
HUM_STEP = 5


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the LG entities"""

    refresh_token = hass.data[CONF_TOKEN]
    region = hass.data[CONF_REGION]
    language = hass.data[CONF_LANGUAGE]

    client = wideq.Client.from_token(refresh_token, region, language)
    dehumifiers = []

    for device_id in hass.data[KEY_SMARTTHINQ_DEVICES]:
        device = client.get_device(device_id)
        model = client.model_info(device)
        LOGGER.debug("Device: %s" % device.type)

        if device.type == wideq.DeviceType.DEHUMIDIFIER:
            base_name = "lg_dehumifier_" + device.name
            LOGGER.debug("Creating new LG Dehumifier: %s" % base_name)
            try:
                dehumifiers.append(LGDehumDevice(client, device, base_name, device.typ))
            except wideq.NotConnectedError:
                # Dehumifiers are only connected when in use. Ignore
                # NotConnectedError on platform setup.
                pass

    if dehumifiers:
        add_entities(dehumifiers, True)
    return True

class LGDehumDevice(climate.ClimateDevice):
    def __init__(self, client, device, name, type):
        """Initialize an LG Dehumifier Device."""

        super().__init__(client, device)

        # This constructor is called during platform creation. It must not
        # involve any API calls that actually need the dehumifier to be
        # connected, otherwise the device construction will fail and the entity
        # will not get created. Specifically, calls that depend on dehumifier
        # interaction should only happen in update(...), including the start of
        # the monitor task.
        self._dehumifier = dehumifier.DehumDevice(client, device)
        self._name = name
        self._status = None
        self._type = type
        self._transient_humi = None
        self._transient_time = None
        self._failed_request_count = 0

    @property
    def temperature_unit(self):
        return '%'

    @property
    def name(self):
        return self._name

    @property
    def device_type(self):
        return self._type

    @property
    def available(self):
        return True

    @property
    def supported_features(self):
        return (
            c_const.SUPPORT_TARGET_TEMPERATURE |
            c_const.SUPPORT_PRESET_MODE |
            c_const.SUPPORT_FAN_MODE |
            c_const.SUPPORT_ON_OFF
        )

    @property
    def min_temp(self):
        return HUM_MIN

    @property
    def max_temp(self):
        return HUM_MAX

    @property
    def current_temperature(self):
        if self._state:
            return self._state.current_humidity

    @property
    def target_temperature(self):
        # Use the recently-set target temperature if it was set recently
        # (within TRANSIENT_EXP seconds ago).
        if self._transient_humi:
            interval = time.time() - self._transient_time
            if interval < TRANSIENT_EXP:
                return self._transient_humi
            else:
                self._transient_humi = None

        # Otherwise, actually use the device's state.
        if self._state:
            return self._state.target_humidity

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return HUM_STEP

    @property
    def preset_modes(self):
        return list(MODES.values())

    @property
    def fan_modes(self):
        return list(FAN_MODES.values())

    @property
    def preset_mode(self):
        if self._state:
            if not self._state.is_on:
                return c_const.HVAC_MODE_OFF
            return self._state.mode

    @property
    def fan_mode(self):
        if self._state:
            if not self._state.is_on:
                return c_const.HVAC_MODE_OFF
            return self._state.windstrength_state

    def set_preset_mode(self, preset_mode):
        if preset_mode == c_const.HVAC_MODE_OFF:
            self._dehumidifier.set_on(False)
            return

        # Some AC units must be powered on before setting the mode.
        if not self._state.is_on:
            self._dehumidifier.set_on(True)

        LOGGER.info('Setting mode to %s...', preset_mode)
        self._dehumidifier.set_mode(mode)
        LOGGER.info('Mode set.')

    def set_fan_mode(self, fan_mode):
        if preset_mode == c_const.HVAC_MODE_OFF:
            self._dehumidifier.set_on(False)
            return

        # Some AC units must be powered on before setting the mode.
        if not self._state.is_on:
            self._dehumidifier.set_on(True)

        LOGGER.info('Setting fan mode to %s', fan_mode)
        self._dehumidifier.set_windstrength(fan_mode)
        LOGGER.info('Fan mode set.')

    @property
    def is_airremoval_mode(self):
        if self._state:
            return self._state.airremoval_state

    def airremoval_mode(self, airremoval_mode):
        if airremoval_mode == '켜짐':
            self._dehum.set_airremoval(True)
        elif airremoval_mode == '꺼짐':
            self._dehum.set_airremoval(False)

    def set_humidity(self, **kwargs):
        humidity = kwargs['humidity']
        self._transient_humi = humidity
        self._transient_time = time.time()

        LOGGER.info('Setting humidity to %s...', humidity)
        self._dehumifier.set_humidity(humidity)
        LOGGER.info('Humidity set.')

    def _restart_monitor(self):
        try:
            self._dehumidifier.monitor_start()
        except wideq.NotConnectedError:
            self._status = None
        except wideq.NotLoggedInError:
            LOGGER.info('Session expired. Refreshing.')
            self._client.refresh()

    def update(self):
        """Poll for dehumidifier state updates."""

        # This method is polled, so try to avoid sleeping in here. If an error
        # occurs, it will naturally be retried on the next poll.

        LOGGER.debug('Updating %s.', self.name)

        # On initial construction, the dehumidifier monitor task
        # will not have been created. If so, start monitoring here.
        if getattr(self._dehumidifier, 'mon', None) is None:
            self._restart_monitor()

        try:
            status = self._dehumidifier.poll()
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
