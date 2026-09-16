# inkbird-ha-bridge

A lightweight, robust Bluetooth Low Energy (BLE) bridge that passively listens for **Inkbird IBS-TH2**, **IBS-TH1**, and **IBS-P01B** thermometer and hygrometer broadcast advertisements and publishes live temperature, humidity, and battery readings directly to **Home Assistant** via the REST API.

---

## Why this exists

Running Home Assistant in a virtual machine (e.g. VirtualBox, VMware, Proxmox, Hyper-V) frequently suffers from unstable or completely broken USB Bluetooth controller passthrough due to host-guest driver conflicts, motherboard USB4/Thunderbolt reset issues, or hypervisor kernel driver bugs.

`inkbird-ha-bridge` solves this cleanly by running as a native background service directly on the host OS (Windows, Linux, or macOS). It accesses the host's native Bluetooth adapter directly (e.g. Realtek, Intel, MediaTek) using `bleak` and pushes parsed measurements over the local network to Home Assistant's REST API.

### Key Features

- **Zero USB Passthrough Issues:** Bypasses virtual machine USB controllers entirely.
- **Passive BLE Listening:** Listens to broadcast advertisements without establishing a connection, preserving sensor battery life.
- **Support for Multiple Models:**
  - Inkbird IBS-TH2 (Temperature, Humidity, Battery)
  - Inkbird IBS-TH1 / IBS-TH1 Mini / Plus
  - Inkbird IBS-P01B pool sensor
- **Smart Rate Limiting & Deduplication:** Only sends updates when values change beyond a configurable threshold (e.g. 0.1°C or 0.5% humidity) or after a maximum interval.
- **Automatic Fallback:** Supports primary and fallback Home Assistant URLs (e.g. local LAN IP with Tailscale fallback).
- **Auto-Discovery or Explicit Mapping:** Pre-configure friendly names and entity prefixes or let unmapped sensors automatically generate sensor entities.
- **Zero Dependencies Beyond Python:** Installs in seconds with `pip install -r requirements.txt`.

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/maximusmaximus/inkbird-ha-bridge.git
cd inkbird-ha-bridge
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure
Copy `config.example.yaml` to `config.yaml`:
```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your Home Assistant details:
```yaml
home_assistant:
  url: "http://192.168.1.100:8123"
  fallback_url: "http://100.x.y.z:8123" # optional Tailscale or remote IP
  token: "YOUR_LONG_LIVED_ACCESS_TOKEN"

bridge:
  min_update_interval_sec: 30
  force_update_interval_sec: 120
  temp_delta_threshold: 0.1
  humidity_delta_threshold: 0.5
  temperature_unit: "F" # "F" for Fahrenheit, "C" for Celsius

devices:
  "49:26:04:25:02:13":
    name: "ParkingLot"
    entity_prefix: "sensor.ibs_th_0213"
  "49:26:04:25:06:54":
    name: "McFridge"
    entity_prefix: "sensor.ibs_th_0654"
```

### 4. Run
```bash
python bridge.py
```

---

## Running as a 24/7 Background Service on Windows

To have the bridge automatically start when your computer boots or logs in, you can register it as a Windows Scheduled Task.

Run `install_task.ps1` in PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File install_task.ps1
```

Or manually create the task using `schtasks`:
```cmd
schtasks /create /tn "InkbirdHABridge" /tr "C:\Users\worki\AppData\Local\Programs\Python\Python312\pythonw.exe C:\path\to\inkbird-ha-bridge\bridge.py" /sc onlogon /rl highest
```

---

## Protocol Decoding Reference

Inkbird broadcast advertisement payloads pack sensor data into the manufacturer data field:
- **9-byte format (IBS-TH2 / IBS-TH1 Mini):**
  - Bytes 0–1: Temperature in hundredths of a degree Celsius (signed 16-bit little-endian `int16`).
  - Bytes 2–3: Relative humidity in hundredths of a percent (unsigned 16-bit little-endian `uint16`).
  - Byte 7: Battery percentage (`uint8`).
- **18-byte format (IBS-TH1 Plus / ITH):**
  - Bytes 6–7: Temperature in hundredths of a degree Celsius.
  - Bytes 8–9: Relative humidity in hundredths of a percent.
  - Byte 10: Battery percentage.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
