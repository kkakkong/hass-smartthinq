import logging
import time
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant import const
from homeassistant.const import CONF_REGION, CONF_TOKEN
from homeassistant.components import climate
from homeassistant.components.climate import ClimateDevice
from homeassistant.components.climate import const as c_const
from custom_components.smartthinq import (
    CONF_LANGUAGE, KEY_SMARTTHINQ_DEVICES, LGDevice)

import wideq
from wideq import dehum
REQUIREMENTS = ['wideq']

LOGGER = logging.getLogger(__name__)

ATTR_DH_STATE = 'state'
ATTR_DH_AIRREMOVAL_MODE = 'airremoval_mode'
ATTR_DH_FAN_MODE = 'fan_mode'
ATTR_DH_FAN_MODES = 'fan_modes'
ATTR_DH_PRESET_MODE = 'preset_mode'
ATTR_DH_PRESET_MODES = 'preset_modes'
ATTR_DH_HVAC_MODE = 'hvac_mode'
ATTR_DH_HVAC_MODES = 'hvac_modes'
ATTR_DH_HUMIDITY = 'humidity'
ATTR_DH_TARGET_HUMIDITY = 'target_humidity'
ATTR_DH_MIN_HUMIDITY = 'min_humidity'
ATTR_DH_MAX_HUMIDITY = 'max_humidity'

MODES = {
    'SMART': '스마트제습',
    'SPEED': '쾌속제습',
    'SILENT': '저소음제습',
    'FOCUS': '집중건조',
    'CLOTHES': '의류건조',
}
FAN_MODES = {
    'LOW': '약',
    'HIGH': '강',
}

MAX_RETRIES = 5
TRANSIENT_EXP = 5.0  # Report set temperature / humidity for 5 seconds.
HUM_MIN = 30
HUM_MAX = 70
HUM_STEP = 5

async def async_setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the LG entities"""

    refresh_token = hass.data[CONF_TOKEN]
    region = hass.data[CONF_REGION]
    language = hass.data[CONF_LANGUAGE]

    client = wideq.Client.from_token(refresh_token, region, language)
    dehumidifiers = []

    for device_id in hass.data[KEY_SMARTTHINQ_DEVICES]:
        device = client.get_device(device_id)
        model = client.model_info(device)
        LOGGER.debug("Device: %s" % device.type)

        if device.type == wideq.DeviceType.DEHUMIDIFIER:
            base_name = "lg_dehumidifier_" + device.name
            LOGGER.debug("Creating new LG Dehumidifier: %s" % base_name)
            try:
                dehumidifiers.append(LGDehumDevice(client, device, base_name))
            except wideq.NotConnectedError:
                # Dehumidifier are only connected when in use. Ignore
                # NotConnectedError on platform setup.
                pass

    if dehumidifiers:
        add_devices(dehumidifiers, True)

class LGDehumDevice(LGDevice, ClimateDevice):
    def __init__(self, client, device, name):
        """Initialize an LG Dehumidifier Device."""

        super().__init__(client, device)

        # This constructor is called during platform creation. It must not
        # involve any API calls that actually need the dehumidifier to be
        # connected, otherwise the device construction will fail and the entity
        # will not get created. Specifically, calls that depend on dehumidifier
        # interaction should only happen in update(...), including the start of
        # the monitor task.
        self._dehumidifier = dehum.DehumDevice(client, device)
        self._name = name
        self._status = None
        self._transient_humi = None
        self._transient_time = None
        self._failed_request_count = 0

    @property
    def name(self):
        return self._name

    @property
    def device_type(self):
        return self._type

    @property
    def available(self):
        return True

    async def async_turn_on(self):
        if self._status:
            if not self._status.is_on:
                self._dehumidifier.set_on(True)
            LOGGER.info('Turn On %s', self.name)
            await self.async_update_ha_state()

    async def async_turn_off(self) :
        if self._status:
            if self._status.is_on:
                self._dehumidifier.set_on(False)
            LOGGER.info('Turn Off %s', self.name)
            await self.async_update_ha_state()

        # Fake turn off
        if HVAC_MODE_OFF in self.hvac_modes:
            await self.async_set_hvac_mode(HVAC_MODE_OFF)

    @property
    def supported_features(self):
        return (
            c_const.SUPPORT_TARGET_HUMIDITY |
            c_const.SUPPORT_PRESET_MODE |
            c_const.SUPPORT_FAN_MODE
        )

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        data = {}
        data[ATTR_DH_STATE] = self.state
        data[ATTR_DH_AIRREMOVAL_MODE] = self.is_airremoval_mode
        data[ATTR_DH_FAN_MODE] = self.fan_mode
        data[ATTR_DH_FAN_MODES] = self.fan_modes
        data[ATTR_DH_PRESET_MODE] = self.preset_mode
        data[ATTR_DH_PRESET_MODES] = self.preset_modes
        data[ATTR_DH_HVAC_MODE] = self.hvac_mode
        data[ATTR_DH_HVAC_MODES] = self.hvac_modes
        data[ATTR_DH_HUMIDITY] = self.current_humidity
        data[ATTR_DH_TARGET_HUMIDITY] = self.target_humidity
        data[ATTR_DH_MIN_HUMIDITY] = self.min_humidity
        data[ATTR_DH_MAX_HUMIDITY] = self.max_humidity

        return data

    @property
    def state(self):
        if self._status:
            return self._status.state
        return 'Off'

    @property
    def min_humidity(self):
        return HUM_MIN

    @property
    def max_humidity(self):
        return HUM_MAX

    @property
    def current_humidity(self):
        return self._status.current_humidity if self._status else 0

    @property
    def target_humidity(self):
        # Use the recently-set target temperature if it was set recently
        # (within TRANSIENT_EXP seconds ago).
        if self._transient_humi:
            interval = time.time() - self._transient_time
            if interval < TRANSIENT_EXP:
                return self._transient_humi
            else:
                self._transient_humi = None

        # Otherwise, actually use the device's state.
        return self._status.target_humidity if self._status else 0

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return HUM_STEP

    @property
    def preset_mode(self):
        if self._status:
            if not self._status.is_on:
                return c_const.HVAC_MODE_OFF
            return self._status.mode
        return c_const.HVAC_MODE_OFF

    @property
    def preset_modes(self):
        return list(MODES.values())

    @property
    def hvac_mode(self):
        if self._status:
            if not self._status.is_on:
                return c_const.HVAC_MODE_OFF
            return c_const.HVAC_MODE_DRY
        return c_const.HVAC_MODE_OFF

    @property
    def hvac_modes(self):
        return [c_const.HVAC_MODE_DRY] + [c_const.HVAC_MODE_OFF]

    @property
    def fan_mode(self):
        if self._status:
            if not self._status.is_on:
                return c_const.HVAC_MODE_OFF
            return self._status.windstrength_state
        return c_const.HVAC_MODE_OFF

    @property
    def fan_modes(self):
        return list(FAN_MODES.values())

    async def async_set_preset_mode(self, preset_mode):
        if preset_mode == c_const.HVAC_MODE_OFF:
            self._dehumidifier.set_on(False)
            return

        # Some AC units must be powered on before setting the mode.
        if self._status:
            if not self._status.is_on:
                self._dehumidifier.set_on(True)
            LOGGER.info('Setting mode to %s...', preset_mode)
            self._dehumidifier.set_mode(preset_mode)
            LOGGER.info('Mode set.')
            await self.async_update_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == c_const.HVAC_MODE_OFF:
            self._dehumidifier.set_on(False)
            return

        # Some AC units must be powered on before setting the mode.
        if self._status:
            if not self._status.is_on:
                self._dehumidifier.set_on(True)
            LOGGER.info('Setting mode to %s...', hvac_mode)
            self._dehumidifier.set_mode(hvac_mode)
            LOGGER.info('Mode set.')
            await self.async_update_ha_state()

    async def async_set_fan_mode(self, fan_mode):
        if fan_mode == c_const.HVAC_MODE_OFF:
            self._dehumidifier.set_on(False)
            return

        # Some AC units must be powered on before setting the mode.
        if self._status:
            if not self._status.is_on:
                self._dehumidifier.set_on(True)
            LOGGER.info('Setting fan mode to %s', fan_mode)
            self._dehumidifier.set_windstrength(fan_mode)
            LOGGER.info('Fan mode set.')
            await self.async_update_ha_state()

    @property
    def is_airremoval_mode(self):
        if self._status:
            return self._status.airremoval_state
        return c_const.HVAC_MODE_OFF

    def airremoval_mode(self, airremoval_mode):
        if airremoval_mode == '켜짐':
            self._dehum.set_airremoval(True)
        elif airremoval_mode == '꺼짐':
            self._dehum.set_airremoval(False)

    async def async_set_humidity(self, **kwargs):
        humidity = kwargs['humidity']
        self._transient_humi = humidity
        self._transient_time = time.time()

        if self._status:
            if not self._status.is_on:
                self._dehumidifier.set_on(True)
            LOGGER.info('Setting humidity to %s...', humidity)
            self._dehumidifier.set_humidity(humidity)
            LOGGER.info('Humidity set.')
            await self.async_update_ha_state()

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
