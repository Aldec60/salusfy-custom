"""Salus IT500 Custom Fork integration."""
DOMAIN = "salusfy_custom"

async def async_setup(hass, config):
    """Set up the Salus IT500 Custom integration."""
    hass.data[DOMAIN] = {}
    return True
