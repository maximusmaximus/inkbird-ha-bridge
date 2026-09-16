"""
Inkbird to Home Assistant BLE Bridge
Listens passively for Inkbird IBS-TH2 and IBS-TH1 BLE advertisements
and streams temperature, humidity, and battery readings directly to Home Assistant REST API.
"""

import asyncio
import logging
import os
import signal
import struct
import sys
import time
from typing import Any, Dict, Optional, Tuple

import requests
import yaml
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("inkbird-bridge")

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

# Inkbird UUIDs & Plausibility Constants
INKBIRD_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
HUMIDITY_NOT_FITTED = frozenset((0, 0xFFFF))


class InkbirdBridge:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config = self._load_config(config_path)
        self.ha_config = self.config.get("home_assistant", {})
        self.bridge_config = self.config.get("bridge", {})
        self.device_map = self.config.get("devices", {})

        # Normalize MAC addresses in device map to lowercase
        self.device_map = {k.lower(): v for k, v in self.device_map.items()}

        self.ha_url = self.ha_config.get("url", "http://localhost:8123").rstrip("/")
        self.ha_fallback_url = self.ha_config.get("fallback_url", "").rstrip("/")
        self.ha_token = self.ha_config.get("token", "")

        self.min_update_sec = float(self.bridge_config.get("min_update_interval_sec", 30))
        self.force_update_sec = float(self.bridge_config.get("force_update_interval_sec", 120))
        self.temp_delta_thresh = float(self.bridge_config.get("temp_delta_threshold", 0.1))
        self.hum_delta_thresh = float(self.bridge_config.get("humidity_delta_threshold", 0.5))
        self.temp_unit = self.bridge_config.get("temperature_unit", "F").upper()

        # Cache of last sent values per MAC: { "temp_c": float, "humidity": float, "battery": int, "time": float }
        self._last_sent: Dict[str, Dict[str, Any]] = {}
        self._running = True

        self._active_url = self.ha_url
        logger.info(f"Initialized Inkbird Bridge. Primary HA URL: {self.ha_url}")
        logger.info(f"Monitoring {len(self.device_map)} pre-configured devices: {list(self.device_map.keys())}")

    def _load_config(self, path: str) -> dict:
        if not os.path.exists(path):
            example_path = os.path.join(os.path.dirname(path), "config.example.yaml")
            if os.path.exists(example_path):
                logger.warning(f"Config '{path}' not found. Falling back to '{example_path}'")
                path = example_path
            else:
                logger.error(f"Config file not found at '{path}'")
                return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def parse_advertisement(adv: AdvertisementData) -> Optional[Tuple[float, Optional[float], Optional[int]]]:
        """
        Parse raw BLE manufacturer data for Inkbird IBS-TH2 / IBS-TH1 / IBS-P01B.
        Returns (temperature_celsius, humidity_percent, battery_percent) or None.
        """
        mfg_data = adv.manufacturer_data
        if not mfg_data:
            return None

        for mfg_id, payload in mfg_data.items():
            # In Inkbird protocol, the 2-byte manufacturer company ID (little-endian)
            # is part of the telemetry payload stream.
            raw = int(mfg_id).to_bytes(2, byteorder="little") + bytes(payload)
            msg_len = len(raw)

            # 9-byte packet (IBS-TH2, IBS-TH1 Mini, IBS-P01B)
            if msg_len == 9:
                temp_raw, hum_raw = struct.unpack("<hH", raw[0:4])
                temp_c = temp_raw / 100.0

                # Plausibility check on humidity
                if hum_raw in HUMIDITY_NOT_FITTED:
                    hum = None
                else:
                    hum = hum_raw / 100.0
                    if hum > 100.0:
                        hum = None

                bat = int(raw[7]) if len(raw) > 7 else None
                if bat is not None and bat > 100:
                    bat = None

                return temp_c, hum, bat

            # 18-byte packet (IBS-TH1 Plus, ITH-11-B, ITH-13-B, IBS-P02B)
            elif msg_len == 18:
                temp_raw, hum_raw = struct.unpack("<hH", raw[6:10])
                temp_c = temp_raw / 100.0
                hum = hum_raw / 100.0 if hum_raw <= 10000 else None
                bat = int(raw[10]) if len(raw) > 10 else None
                if bat is not None and bat > 100:
                    bat = None

                return temp_c, hum, bat

        return None

    def _should_update(self, mac: str, temp_c: float, hum: Optional[float], bat: Optional[int]) -> bool:
        now = time.time()
        last = self._last_sent.get(mac)
        if not last:
            return True

        elapsed = now - last["time"]

        # If force interval elapsed, update regardless
        if elapsed >= self.force_update_sec:
            return True

        # If min update interval has not elapsed, suppress
        if elapsed < self.min_update_sec:
            return False

        # Check deltas
        temp_diff = abs(temp_c - last.get("temp_c", temp_c))
        if temp_diff >= self.temp_delta_thresh:
            return True

        if hum is not None and last.get("humidity") is not None:
            hum_diff = abs(hum - last["humidity"])
            if hum_diff >= self.hum_delta_thresh:
                return True

        if bat is not None and bat != last.get("battery"):
            return True

        return False

    def _post_ha_state(self, entity_id: str, state_val: Any, attributes: dict) -> bool:
        """Publish entity state and attributes to Home Assistant REST API."""
        headers = {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "state": str(state_val),
            "attributes": attributes,
        }

        urls_to_try = [self._active_url]
        if self.ha_fallback_url and self.ha_fallback_url != self._active_url:
            urls_to_try.append(self.ha_fallback_url)

        for url in urls_to_try:
            endpoint = f"{url}/api/states/{entity_id}"
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=5)
                if resp.status_code in (200, 201):
                    self._active_url = url
                    return True
                else:
                    logger.warning(f"Failed to post to {endpoint}: HTTP {resp.status_code} - {resp.text}")
            except requests.RequestException as e:
                logger.debug(f"Network error contacting {endpoint}: {e}")

        logger.error(f"Could not reach Home Assistant for {entity_id} via any configured URLs")
        return False

    def handle_device_data(self, mac: str, local_name: str, temp_c: float, hum: Optional[float], bat: Optional[int], rssi: int):
        if not self._should_update(mac, temp_c, hum, bat):
            return

        # Lookup device info from config or generate default prefix
        dev_info = self.device_map.get(mac)
        if dev_info:
            friendly_name = dev_info.get("name", mac)
            prefix = dev_info.get("entity_prefix", f"sensor.ibs_th_{mac.replace(':', '')[-4:]}")
        else:
            suffix = mac.replace(":", "")[-4:].lower()
            friendly_name = f"Inkbird {suffix.upper()}"
            prefix = f"sensor.ibs_th_{suffix}"

        # Determine temperature value according to configured unit
        if self.temp_unit == "F":
            temp_pub = round(temp_c * 9.0 / 5.0 + 32.0, 1)
            temp_unit_str = "°F"
        else:
            temp_pub = round(temp_c, 1)
            temp_unit_str = "°C"

        logger.info(
            f"[{friendly_name} / {mac}] Temp: {temp_pub}{temp_unit_str} | "
            f"Humidity: {f'{hum:.1f}%' if hum is not None else 'N/A'} | "
            f"Battery: {f'{bat}%' if bat is not None else 'N/A'} | RSSI: {rssi} dBm"
        )

        # 1. Temperature entity
        temp_attrs = {
            "unit_of_measurement": temp_unit_str,
            "device_class": "temperature",
            "state_class": "measurement",
            "friendly_name": f"{friendly_name} Temperature",
            "raw_celsius": round(temp_c, 2),
            "mac_address": mac,
            "rssi": rssi,
        }
        self._post_ha_state(f"{prefix}_temperature", temp_pub, temp_attrs)

        # 2. Humidity entity (if available)
        if hum is not None:
            hum_attrs = {
                "unit_of_measurement": "%",
                "device_class": "humidity",
                "state_class": "measurement",
                "friendly_name": f"{friendly_name} Humidity",
                "mac_address": mac,
            }
            self._post_ha_state(f"{prefix}_humidity", round(hum, 1), hum_attrs)

        # 3. Battery entity (if available)
        if bat is not None:
            bat_attrs = {
                "unit_of_measurement": "%",
                "device_class": "battery",
                "state_class": "measurement",
                "friendly_name": f"{friendly_name} Battery",
                "mac_address": mac,
            }
            self._post_ha_state(f"{prefix}_battery", bat, bat_attrs)

        # Update cache
        self._last_sent[mac] = {
            "temp_c": temp_c,
            "humidity": hum,
            "battery": bat,
            "time": time.time(),
        }

    def detection_callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        mac = device.address.lower()
        name = (device.name or advertisement_data.local_name or "").lower()

        # Match known devices or recognized Inkbird identifiers
        is_known = mac in self.device_map
        is_inkbird = (
            "sps" in name
            or "tps" in name
            or "ink" in name
            or INKBIRD_SERVICE_UUID in [str(u).lower() for u in advertisement_data.service_uuids]
        )

        if not (is_known or is_inkbird):
            return

        parsed = self.parse_advertisement(advertisement_data)
        if parsed is None:
            return

        temp_c, hum, bat = parsed
        self.handle_device_data(mac, name, temp_c, hum, bat, advertisement_data.rssi)

    async def run(self):
        logger.info("Starting passive BLE scanner...")
        scanner = BleakScanner(detection_callback=self.detection_callback)
        await scanner.start()
        logger.info("BLE Scanner is active and listening for Inkbird advertisements.")

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Stopping BLE scanner...")
            await scanner.stop()
            logger.info("BLE Scanner stopped cleanly.")

    def stop(self):
        self._running = False


def main():
    bridge = InkbirdBridge()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal(*_):
        logger.info("Shutdown signal received.")
        bridge.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        loop.run_until_complete(bridge.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
