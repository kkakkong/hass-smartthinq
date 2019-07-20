import logging
import voluptuous as vol
from homeassistant.components import climate
import homeassistant.helpers.config_validation as cv
from homeassistant import const
import time
from homeassistant.components.climate import const as c_const

REQUIREMENTS = ['wideq']

LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = climate.PLATFORM_SCHEMA.extend({
    vol.Required('refresh_token'): cv.string,
    'country': cv.string,
    'language': cv.string,
})

MODES = {
    'HEAT': c_const.HVAC_MODE_HEAT,
    'COOL': c_const.HVAC_MODE_COOL,
    'ACO': c_const.HVAC_MODE_HEAT_COOL,
}
MAX_RETRIES = 5
TRANSIENT_EXP = 5.0  # Report set temperature for 5 seconds.
TEMP_MIN_F = 60  # Guessed from actual behavior: API reports are unreliable.
TEMP_MAX_F = 89


def setup_platform(hass, config, add_devices, discovery_info=None):
    import wideq

    refresh_token = config.get('refresh_token')
    country = config.get('country')
    language = config.get('language')

    client = wideq.Client.from_token(refresh_token, country, language)
    add_devices(_ac_devices(hass, client), True)


def _ac_devices(hass, client):
    """Generate all the AC (climate) devices associated with the user's
    LG account.

    Log errors for devices that can't be used for whatever reason.
    """
    import wideq

    persistent_notification = hass.components.persistent_notification

    for device in client.devices:
        if device.type == wideq.DeviceType.AC:
            try:
                d = LGDevice(client, device)
            except wideq.NotConnectedError:
                LOGGER.error(
                    'SmartThinQ device not available: %s', device.name
                )
                persistent_notification.async_create(
                    'SmartThinQ device not available: %s' % device.name,
                    title='SmartThinQ Error',
                )
            else:
                yield d


class LGDevice(climate.ClimateDevice):
    def __init__(self, client, device, fahrenheit=True):
        self._client = client
        self._device = device
        self._fahrenheit = fahrenheit

        import wideq
        self._ac = wideq.ACDevice(client, device)
        self._ac.monitor_start()

        # The response from the monitoring query.
        self._state = None

        # Store a transient temperature when we've just set it. We also
        # store the timestamp for when we set this value.
        self._transient_temp = None
        self._transient_time = None

    @property
    def temperature_unit(self):
        if self._fahrenheit:
            return const.TEMP_FAHRENHEIT
        else:
            return const.TEMP_CELSIUS

    @property
    def name(self):
        return self._device.name

    @property
    def available(self):
        return True

    @property
    def supported_features(self):
        return (
            c_const.SUPPORT_TARGET_TEMPERATURE
        )

    @property
    def min_temp(self):
        if self._fahrenheit:
            return TEMP_MIN_F
        return climate.ClimateDevice.min_temp.fget(self)

    @property
    def max_temp(self):
        if self._fahrenheit:
            return TEMP_MAX_F
        return climate.ClimateDevice.max_temp.fget(self)

    @property
    def current_temperature(self):
        if self._state:
            if self._fahrenheit:
                return self._state.temp_cur_f
            else:
                return self._state.temp_cur_c

    @property
    def target_temperature(self):
        # Use the recently-set target temperature if it was set recently
        # (within TRANSIENT_EXP seconds ago).
        if self._transient_temp:
            interval = time.time() - self._transient_time
            if interval < TRANSIENT_EXP:
                return self._transient_temp
            else:
                self._transient_temp = None

        # Otherwise, actually use the device's state.
        if self._state:
            if self._fahrenheit:
                return self._state.temp_cfg_f
            else:
                return self._state.temp_cfg_c

    @property
    def hvac_modes(self):
        return list(MODES.values()) + [c_const.HVAC_MODE_OFF]

    @property
    def hvac_mode(self):
        if self._state:
            if not self._state.is_on:
                return c_const.HVAC_MODE_OFF
            mode = self._state.mode
            return MODES[mode.name]

    def set_hvac_mode(self, hvac_mode):
        if hvac_mode == c_const.HVAC_MODE_OFF:
            self._ac.set_on(False)
            return

        import wideq

        # Invert the modes mapping.
        modes_inv = {v: k for k, v in MODES.items()}

        mode = wideq.ACMode[modes_inv[hvac_mode]]
        LOGGER.info('Setting mode to %s...', mode)
        self._ac.set_mode(mode)
        LOGGER.info('Mode set.')

    def set_temperature(self, **kwargs):
        temperature = kwargs['temperature']
        self._transient_temp = temperature
        self._transient_time = time.time()

        LOGGER.info('Setting temperature to %s...', temperature)
        if self._fahrenheit:
            self._ac.set_fahrenheit(temperature)
        else:
            self._ac.set_celsius(temperature)
        LOGGER.info('Temperature set.')

    def update(self):
        """Poll for updated device status.

        Set the `_state` field to a new data mapping.
        """

        import wideq

        LOGGER.info('Updating %s.', self.name)
        for iteration in range(MAX_RETRIES):
            LOGGER.info('Polling...')

            try:
                state = self._ac.poll()
            except wideq.NotLoggedInError:
                LOGGER.info('Session expired. Refreshing.')
                self._client.refresh()
                self._ac.monitor_start()
                continue

            if state:
                LOGGER.info('Status updated.')
                self._state = state
                return

            LOGGER.info('No status available yet.')
            time.sleep(2 ** iteration)  # Exponential backoff.

        # We tried several times but got no result. This might happen
        # when the monitoring request gets into a bad state, so we
        # restart the task.
        LOGGER.warn('Status update failed.')
        self._ac.monitor_start()