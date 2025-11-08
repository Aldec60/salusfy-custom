# Salus IT500 – Custom Fork (by Aldec60)

Custom component for Home Assistant, compatible with **Salus IT500 thermostats** (cloud mode).  

The Salus IT500 has 2 zones, the first one on the device itself, the second is a remote device (an IT300TX module).

Both control relays into the IT500RX modules.

Here is the working adaptation to connect the 2 zones Z1 and Z2 to HA 

This fork adds **multi-zone (Z1/Z2) support**, better temperature synchronization, and modern async handling.

---

## ✨ Features
- Two independent zones (Living / Bathroom, etc.)
- Real-time target & ambient temperature updates
- HVAC mode reporting (`heating`, `idle`, `off`)
- Token refresh & error recovery
- Clean domain name: `salusfy_custom`

---

## ⚙️ Installation

Copy this repository to: /config/custom_components/salusfy_custom/

Then add to your Home Assistant `configuration.yaml`:
```yaml
climate:
  - platform: salusfy_custom
    name: "Salus IT500 - Living"
    username: "your_email"
    password: "your_password"
    id: "your_device_id"
    zone: 1

  - platform: salusfy_custom
    name: "Salus IT500 - Bathroom"
    username: "your_email"
    password: "your_password"
    id: "your_device_id"m
    zone: 2
```
Restart Home Assistant, and both zones should appear automatically.

⸻

🧩 Credits

Original integration by floringhimie, modified by Aldec60 for dual-zone.

⸻⸻⸻⸻⸻⸻⸻⸻⸻⸻⸻⸻⸻⸻⸻

HA Dashboard example :

<p align="center">
  <img src="IMG_2133.png" alt="Salus IT500 Dashboard" width="700">
</p>
