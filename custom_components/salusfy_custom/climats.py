"""
Adds support for the Salus Thermostat (IT500) units – multi-zone (Z1/Z2).
"""

import datetime
import time
import logging
import re
import json
import requests
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_ID,
    CONF_NAME,
    CONF_ZONE,
    UnitOfTemperature,
)

_LOGGER = logging.getLogger(__name__)
__version__ = "0.1.0"

# Endpoints connus (peuvent varier selon Salus, mais ceux-ci couvrent l’IT500)
URL_LOGIN = "https://salus-it500.com/public/login.php"
URL_GET_TOKEN = "https://salus-it500.com/public/control.php"
URL_GET_DATA = "https://salus-it500.com/public/ajax_device_values.php"
URL_SET_DATA = "https://salus-it500.com/includes/set.php"

DEFAULT_NAME = "Salus IT500"
MIN_TEMP = 5
MAX_TEMP = 30

# Fonctionnalités de base : réglage de la consigne
SUPPORT_FLAGS = ClimateEntityFeature.TARGET_TEMPERATURE

# (Option) cadence de polling raisonnable pour éviter l’anti-bot
SCAN_INTERVAL = datetime.timedelta(seconds=120)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_ID): cv.string,
        vol.Optional(CONF_ZONE, default=1): cv.positive_int,  # 1 = Z1, 2 = Z2
    }
)


# ────────────────────────────────────────────────────────────────────────────
#  SETUP SYNCHRONE  (requests est synchrone → pas d’async ici)
# ────────────────────────────────────────────────────────────────────────────
def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Salus IT500 platform (sync)."""
    name = config.get(CONF_NAME, DEFAULT_NAME)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    device_id = config.get(CONF_ID)
    zone = int(config.get(CONF_ZONE, 1))

    add_entities([SalusThermostat(name, username, password, device_id, zone)], True)


# ────────────────────────────────────────────────────────────────────────────
#  ENTITÉ CLIMATE
# ────────────────────────────────────────────────────────────────────────────
class SalusThermostat(ClimateEntity):
    """Representation of a Salus IT500 thermostat (single zone view)."""

    def __init__(self, name, username, password, device_id, zone=1):
        """Initialize the thermostat."""
        self._name = name
        self._username = username
        self._password = password
        self._id = device_id
        self._zone = int(zone)              # 1 ou 2
        self._z = f"Z{self._zone}"          # "Z1" ou "Z2"

        self._current_temperature = None
        self._target_temperature = None
        self._frost = None
        self._status = "OFF"                # "ON"/"OFF" chauffe en cours (proxy)
        self._current_operation_mode = "OFF"  # "ON"/"OFF" (auto/manu proxy)
        self._token = None

        # Session HTTP avec en-têtes "navigateur" pour contourner l’anti-bot
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": URL_LOGIN,
        })

        # Premier fetch
        self.update()

    # ── Helpers ────────────────────────────────────────────────────────────
    def _first_key(self, data: dict, *keys, cast=float, default=None):
        """Retourne la 1ère clé existante dans 'data' parmi 'keys' (avec conversion)."""
        for k in keys:
            if not k:
                continue
            if k in data and data[k] not in (None, "", "null"):
                try:
                    return cast(data[k])
                except Exception:
                    return data[k]
        return default

    # ── Propriétés HA ─────────────────────────────────────────────────────
    @property
    def supported_features(self):
        return SUPPORT_FLAGS

    @property
    def name(self):
        # On peut suffixer la zone si on veut, mais le YAML fournit déjà un nom explicite
        return self._name

    @property
    def unique_id(self) -> str:
        # Stable et unique par device_id + zone
        return f"salus_it500_{self._id}_Z{self._zone}"

    @property
    def should_poll(self):
        return True

    @property
    def min_temp(self):
        return MIN_TEMP

    @property
    def max_temp(self):
        return MAX_TEMP

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        return self._current_temperature

    @property
    def target_temperature(self):
        return self._target_temperature

    @property
    def hvac_mode(self):
        """Retourne le mode HA (HEAT/OFF) à partir du champ texte 'ON'/'OFF'."""
        try:
            return HVACMode.HEAT if self._current_operation_mode == "ON" else HVACMode.OFF
        except Exception:
            return HVACMode.OFF

    @property
    def hvac_modes(self):
        return [HVACMode.HEAT, HVACMode.OFF]

    @property
    def hvac_action(self):
        """Retourne l'action courante (HEATING/IDLE/OFF)."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING if self._status == "ON" else HVACAction.IDLE

    # ── Commandes ─────────────────────────────────────────────────────────
    def set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._set_temperature(float(temperature))

    def _set_temperature(self, temperature: float):
        """Set new target temperature, via URL commands (zone-aware)."""
        z = self._z
        payload = {
            "token": self._token,
            "devId": self._id,
            "tempUnit": "0",
            f"current_temp{z}_set": "1",
            f"current_temp{z}": temperature,
        }
        headers = {"content-type": "application/x-www-form-urlencoded"}

        try:
            resp = self._session.post(URL_SET_DATA, data=payload, headers=headers)
            if resp.status_code == 200:
                self._target_temperature = temperature
                # Mise à jour locale immédiate (pas de force_refresh pour éviter le "yo-yo")
                self.schedule_update_ha_state()
                _LOGGER.info("Salus set_temperature OK (zone=%s, T=%.1f°C)", z, temperature)
            else:
                _LOGGER.warning("Salus set_temperature: HTTP %s (zone=%s)", resp.status_code, z)
        except Exception as e:
            _LOGGER.error("Error Setting the temperature (zone=%s): %s", z, e)

    def set_hvac_mode(self, hvac_mode):
        """Set HVAC mode, via URL commands (zone-aware)."""
        headers = {"content-type": "application/x-www-form-urlencoded"}
        z = self._z

        if hvac_mode == HVACMode.OFF:
            # auto=1 + auto_setZx=1 → OFF (selon l’implémentation d’origine du repo)
            payload = {"token": self._token, "devId": self._id, "auto": "1", f"auto_set{z}": "1"}
            try:
                resp = self._session.post(URL_SET_DATA, data=payload, headers=headers)
                if resp.status_code == 200:
                    self._current_operation_mode = "OFF"
                    self.schedule_update_ha_state()
            except Exception as e:
                _LOGGER.error("Error Setting HVAC mode OFF (zone=%s): %s", z, e)

        elif hvac_mode == HVACMode.HEAT:
            # auto=0 + auto_setZx=1 → HEAT/manuel (selon l’implémentation d’origine)
            payload = {"token": self._token, "devId": self._id, "auto": "0", f"auto_set{z}": "1"}
            try:
                resp = self._session.post(URL_SET_DATA, data=payload, headers=headers)
                if resp.status_code == 200:
                    self._current_operation_mode = "ON"
                    self.schedule_update_ha_state()
            except Exception as e:
                _LOGGER.error("Error Setting HVAC mode HEAT (zone=%s): %s", z, e)

        _LOGGER.info("Setting the HVAC mode (zone=%s).", z)

    # ── Auth / Token ───────────────────────────────────────────────────────
    def get_token(self):
        """Get the Session Token of the Thermostat."""
        payload = {
            "IDemail": self._username,
            "password": self._password,
            "login": "Login",
            "keep_logged_in": "1",
        }
        headers = {"content-type": "application/x-www-form-urlencoded"}
        try:
            # Login (prime cookies)
            self._session.post(URL_LOGIN, data=payload, headers=headers)
            # Page de contrôle (contient le token dans un champ hidden)
            params = {"devId": self._id}
            page = self._session.get(URL_GET_TOKEN, params=params)
            # Regex tolérante (simple/double quotes, ordre d’attributs indifférent)
            m = re.search(r'id=[\'"]token[\'"][^>]*value=[\'"]([^\'"]+)[\'"]', page.text, re.IGNORECASE)
            if not m:
                _LOGGER.error("Error Getting the Session Token (token introuvable).")
                return
            self._token = m.group(1)
            _LOGGER.info("Salus get_token OK")
        except Exception as e:
            _LOGGER.error("Error Getting the Session Token: %s", e)

    # ── Lecture données ────────────────────────────────────────────────────
    def _get_data(self):
        if self._token is None:
            self.get_token()

        # IMPORTANT : la clé anti-cache est "_" (pas "&_")
        params = {
            "devId": self._id,
            "token": self._token,
            "_": str(int(round(time.time() * 1000))),
        }
        try:
            r = self._session.get(url=URL_GET_DATA, params=params)
            if not r:
                _LOGGER.error("Could not get data from Salus.")
                return

            try:
                data = json.loads(r.text)
            except Exception as e:
                _LOGGER.warning("Error decoding Salus data, refreshing token: %s", e)
                # Token expiré ? on tente une fois de le renouveler
                self.get_token()
                r = self._session.get(url=URL_GET_DATA, params=params)
                data = json.loads(r.text)

            _LOGGER.info("Salus get_data output OK (zone=%s)", self._z)

            # —— Zone-aware parsing ——
            z = self._z
            zone_num = self._zone

            # Consigne
            self._target_temperature = self._first_key(
                data,
                f"current_temp{z}_sp",
                f"setTemp{z}",
                f"setPoint{zone_num}",
                f"CH{zone_num}currentSetPoint",  # format d’origine du repo
                default=self._target_temperature,
            )

            # Température ambiante
            self._current_temperature = self._first_key(
                data,
                f"current_temp{z}",
                f"roomTemp{zone_num}",
                f"CH{zone_num}currentRoomTemp",   # format d’origine du repo
                default=self._current_temperature,
            )

            # Antigel
            self._frost = self._first_key(data, "frost", default=self._frost)

            # Statut de chauffe (1/0 → ON/OFF)
            status = self._first_key(
                data,
                f"CH{zone_num}heatOnOffStatus",
                f"heatOnOffStatus{z}",
                cast=str,
                default="0",
            )
            self._status = "ON" if status == "1" else "OFF"

            # Mode de fonctionnement (1/0 → OFF/ON suivant implémentation d’origine)
            mode = self._first_key(
                data,
                f"CH{zone_num}heatOnOff",
                f"mode{z}",
                cast=str,
                default="0",
            )
            self._current_operation_mode = "OFF" if mode == "1" else "ON"

        except Exception as e:
            _LOGGER.error(
                "Error Getting the data from Web. Please check the connection to salus-it500.com manually: %s",
                e,
            )

    # Poll standard HA
    def update(self):
        """Get the latest data."""
        self._get_data()
