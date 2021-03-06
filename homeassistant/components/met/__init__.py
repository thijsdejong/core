"""The met component."""
from datetime import timedelta
import logging
from random import randrange

import metno

from homeassistant.const import (
    CONF_ELEVATION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    EVENT_CORE_CONFIG_UPDATE,
    LENGTH_FEET,
    LENGTH_METERS,
)
from homeassistant.core import Config, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.distance import convert as convert_distance
import homeassistant.util.dt as dt_util

from .const import (
    CONF_TRACK_HOME,
    DEFAULT_HOME_LATITUDE,
    DEFAULT_HOME_LONGITUDE,
    DOMAIN,
)

URL = "https://aa015h6buqvih86i1.api.met.no/weatherapi/locationforecast/2.0/complete"


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: Config) -> bool:
    """Set up configured Met."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass, config_entry):
    """Set up Met as config entry."""
    # Don't setup if tracking home location and latitude or longitude isn't set.
    # Also, filters out our onboarding default location.
    if config_entry.data.get(CONF_TRACK_HOME, False) and (
        (not hass.config.latitude and not hass.config.longitude)
        or (
            hass.config.latitude == DEFAULT_HOME_LATITUDE
            and hass.config.longitude == DEFAULT_HOME_LONGITUDE
        )
    ):
        _LOGGER.warning(
            "Skip setting up met.no integration; No Home location has been set"
        )
        return False

    coordinator = MetDataUpdateCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()

    if config_entry.data.get(CONF_TRACK_HOME, False):
        coordinator.track_home()

    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(config_entry, "weather")
    )

    return True


async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    await hass.config_entries.async_forward_entry_unload(config_entry, "weather")
    hass.data[DOMAIN][config_entry.entry_id].untrack_home()
    hass.data[DOMAIN].pop(config_entry.entry_id)

    return True


class MetDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Met data."""

    def __init__(self, hass, config_entry):
        """Initialize global Met data updater."""
        self._unsub_track_home = None
        self.weather = MetWeatherData(
            hass, config_entry.data, hass.config.units.is_metric
        )
        self.weather.set_coordinates()

        update_interval = timedelta(minutes=randrange(55, 65))

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)

    async def _async_update_data(self):
        """Fetch data from Met."""
        try:
            return await self.weather.fetch_data()
        except Exception as err:
            raise UpdateFailed(f"Update failed: {err}") from err

    def track_home(self):
        """Start tracking changes to HA home setting."""
        if self._unsub_track_home:
            return

        async def _async_update_weather_data(_event=None):
            """Update weather data."""
            if self.weather.set_coordinates():
                await self.async_refresh()

        self._unsub_track_home = self.hass.bus.async_listen(
            EVENT_CORE_CONFIG_UPDATE, _async_update_weather_data
        )

    def untrack_home(self):
        """Stop tracking changes to HA home setting."""
        if self._unsub_track_home:
            self._unsub_track_home()
            self._unsub_track_home = None


class MetWeatherData:
    """Keep data for Met.no weather entities."""

    def __init__(self, hass, config, is_metric):
        """Initialise the weather entity data."""
        self.hass = hass
        self._config = config
        self._is_metric = is_metric
        self._weather_data = None
        self.current_weather_data = {}
        self.daily_forecast = None
        self.hourly_forecast = None
        self._coordinates = None

    def set_coordinates(self):
        """Weather data inialization - set the coordinates."""
        if self._config.get(CONF_TRACK_HOME, False):
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            elevation = self.hass.config.elevation
        else:
            latitude = self._config[CONF_LATITUDE]
            longitude = self._config[CONF_LONGITUDE]
            elevation = self._config[CONF_ELEVATION]

        if not self._is_metric:
            elevation = int(
                round(convert_distance(elevation, LENGTH_FEET, LENGTH_METERS))
            )

        coordinates = {
            "lat": str(latitude),
            "lon": str(longitude),
            "msl": str(elevation),
        }
        if coordinates == self._coordinates:
            return False
        self._coordinates = coordinates

        self._weather_data = metno.MetWeatherData(
            coordinates, async_get_clientsession(self.hass), api_url=URL
        )
        return True

    async def fetch_data(self):
        """Fetch data from API - (current weather and forecast)."""
        await self._weather_data.fetching_data()
        self.current_weather_data = self._weather_data.get_current_weather()
        time_zone = dt_util.DEFAULT_TIME_ZONE
        self.daily_forecast = self._weather_data.get_forecast(time_zone, False)
        self.hourly_forecast = self._weather_data.get_forecast(time_zone, True)
        return self
