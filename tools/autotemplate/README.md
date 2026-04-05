# autotemplate

Automates template generation for the MKW tracker by spoofing a Pro Controller
and driving the Switch 2 through every menu screen while the capture card records.

Not shipped to end users — developer tool only.

---

## How it works

```
Windows PC
├── WSL2 (Ubuntu) ──── nxbt ──────────────────────────────┐
│   └── runner.py                                         │  Bluetooth
│       ├── reads YAML script                             ↓
│       ├── sends button presses via nxbt         Nintendo Switch 2
│       └── reads capture card frame via OpenCV   (paired as Pro Controller)
│                        ↑
│                        │ USB forwarded via usbipd-win
├── Capture card (USB) ──┘
└── images/ directory (written directly by runner)
```

The runner:
1. Pairs with the Switch 2 as a Pro Controller over Bluetooth
2. Executes a user-defined YAML button script
3. At each `capture` point, reads a frame from the capture card, crops the configured ROI, and saves the processed PNG to `images/{category}/`

Templates are saved in the exact format the tracker expects — binary-thresholded for characters/karts/courses/mushrooms, raw BGR for costumes (edge-processed at load time).

---

## Prerequisites

### Windows side
- `usbipd-win` installed: https://github.com/dorssel/usbipd-win/releases
- WSL2 with Ubuntu 22.04+ (`wsl --install`)
- Your Bluetooth adapter and capture card USB devices

### WSL2 side
- Python 3.10+
- BlueZ + D-Bus (see setup below)

---

## Setup (WSL2)

### 1. Enable systemd in WSL2 (required for BlueZ)

```ini
# /etc/wsl.conf  (create if absent)
[boot]
systemd=true
```

Restart WSL: `wsl --shutdown`, then reopen.

### 2. Install BlueZ and D-Bus

```bash
sudo apt update
sudo apt install -y bluetooth bluez dbus python3-dbus python3-pip
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
```

### 3. Forward Bluetooth adapter to WSL2 (Windows PowerShell, as Admin)

```powershell
# List USB devices
usbipd list

# Find your Bluetooth adapter (e.g. busid 1-5) and share + attach it
usbipd bind   --busid 1-5
usbipd attach --wsl --busid 1-5
```

Verify in WSL2:
```bash
hciconfig   # should show hci0
```

### 4. Forward capture card to WSL2

```powershell
# Find capture card busid (e.g. 2-3)
usbipd bind   --busid 2-3
usbipd attach --wsl --busid 2-3
```

Verify in WSL2:
```bash
ls /dev/video*   # should show /dev/video0
```

### 5. Install Python dependencies

```bash
cd /path/to/mkw-split-rewrite/tools/autotemplate
pip install -r requirements.txt
```

### 6. Give your user Bluetooth permissions

```bash
sudo usermod -aG bluetooth $USER
# Log out and back in, or run: newgrp bluetooth
```

---

## Alternative: Linux VM (VirtualBox / VMware)

If WSL2 Bluetooth is too unreliable, a standard Ubuntu VM is simpler:

1. Install VirtualBox with Extension Pack (for USB 3.0 passthrough)
2. In VM settings → USB, add your Bluetooth adapter and capture card
3. Start VM → `sudo systemctl start bluetooth` → proceed with steps 4–6 above

---

## Quick demo (verify controller pairing works)

```bash
python3 - <<'EOF'
from controller import ProController
import time

ctrl = ProController()
# On Switch 2: go to HOME → Controllers → Change Grip/Order
ctrl.connect()
print("Connected! Pressing A in 2 seconds…")
time.sleep(2)
ctrl.press("A")
time.sleep(0.5)
ctrl.press("B")
ctrl.disconnect()
print("Done.")
EOF
```

If the Switch 2 registers button presses, the setup is working.

---

## Running a script

### Dry run first (no controller, no capture card needed)

```bash
python3 runner.py scripts/characters_en.yaml --dry-run
```

This prints every step and capture point without touching any hardware.

### Preview ROI (verify ROI position before a real run)

```bash
python3 runner.py scripts/characters_en.yaml \
    --preview-roi characters \
    --db /mnt/c/development/mkw-split-rewrite/mkw_tracker.db
```

Opens a live OpenCV window showing the capture feed with the character name ROI
highlighted. Press Q to close. **mkw_tracker must NOT be running** (it holds the
capture card).

### Full run

```bash
python3 runner.py scripts/characters_en.yaml \
    --db /mnt/c/development/mkw-split-rewrite/mkw_tracker.db \
    --device 0
```

The script will:
- Prompt you to put the Switch in pairing mode
- Execute the preamble to reach the right screen
- Step through each character, capturing a template per item

### Resume after interruption

```bash
python3 runner.py scripts/characters_en.yaml \
    --db /mnt/c/.../mkw_tracker.db \
    --start-from koopa_troopa
```

Skips all items before `koopa_troopa` and resumes from there.

### Reconnect to already-paired Switch (no pairing mode needed)

```bash
python3 runner.py scripts/characters_en.yaml \
    --reconnect "AA:BB:CC:DD:EE:FF" \
    --db /mnt/c/.../mkw_tracker.db
```

Replace the MAC with your Switch 2's Bluetooth address (visible in Switch
system settings under Bluetooth).

---

## Writing a script

See `scripts/characters_en.yaml` for a full example.

```yaml
name:     "Karts (French)"
category: karts          # characters | karts | courses | costumes | mushrooms
language: fr

# Run once at start — navigate from whatever state to the first item screen
preamble:
  - { A: 0.1 }
  - { wait: 2.0 }

items:
  - name: "B Dasher"    # display name (informational)
    file: b_dasher      # saved as images/karts/b_dasher.png
    before:             # button presses from previous item to this one
      - { DPAD_RIGHT: 0.05 }
      - { wait: 0.3 }
    capture_wait: 0.4   # seconds between last button and frame capture
```

### Step syntax

| Step dict             | Meaning                          |
|-----------------------|----------------------------------|
| `{ A: 0.1 }`         | Press A for 0.1 s                |
| `{ DPAD_RIGHT: 0.05 }` | Press D-pad right for 0.05 s  |
| `{ wait: 1.5 }`      | Sleep 1.5 s                      |
| `{ macro: "A 0.1s\n0.2s\nB 0.1s" }` | Raw nxbt macro  |

Valid button names: `A B X Y L R ZL ZR PLUS MINUS HOME CAPTURE DPAD_UP DPAD_DOWN DPAD_LEFT DPAD_RIGHT L_STICK R_STICK`

### Tips

- Start with `--dry-run` to verify your script before touching the Switch
- Use `--preview-roi` to confirm the ROI captures what you expect for each category
- `capture_wait` should be long enough for any selection animation to finish (0.3–0.8 s is typical)
- The preamble for a given category only needs to get you to the **first item** — subsequent items navigate relative to the previous one

---

## Processing per category

| Category   | How templates are saved          | Why                                      |
|------------|----------------------------------|------------------------------------------|
| characters | Binary-thresholded grayscale PNG | Matched against binarized live crops     |
| karts      | Binary-thresholded grayscale PNG | Same                                     |
| courses    | Binary-thresholded grayscale PNG | Same                                     |
| costumes   | Raw BGR PNG                      | `load_template_dir(white_text=True)` applies Canny edges at load time |
| mushrooms  | Binary-thresholded grayscale PNG | Same as characters                       |

---

## Adding language support

1. Duplicate an existing script: `cp scripts/characters_en.yaml scripts/characters_fr.yaml`
2. Set `language: fr`
3. Update the `preamble` if the menu navigation differs in the French version
4. Update `before` steps if character order differs
5. Run the script — templates are saved to the same `images/` directories, **overwriting** the previous language's templates

To ship multi-language support: maintain separate `images_en/`, `images_fr/` etc. directories and have the tracker select the right one based on a config key.
