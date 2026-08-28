# Laser Director

Laser Director is a Python/PySide6 MVP for aiming a mini moving head laser from a Vectorworks DXF or PDF stage plan and sending pan/tilt/laser values over Art-Net.

## Install and Run

```bash
cd laser_director
python -m pip install -r requirements.txt
python main.py
```

Python 3.12+ is recommended. The app is intended for Windows and macOS.

## Art-Net Connection

1. Connect the computer and Art-Net node/controller to the same network.
2. Set the controller IP in the right panel. The default broadcast address is `2.255.255.255`.
3. Keep the Art-Net UDP port at `6454` unless your controller requires another port.
4. Set the DMX universe. Universe `0` is the default.
5. Click `Laser OFF` first, then test pan/tilt with the jog buttons.

ArtDMX packets are sent on every changed value. The app always sends a 512-channel DMX frame.

## Create a Project

1. Start the app and choose `New`.
2. Load a plan with `Load DXF` or `Load PDF`.
3. Configure the fixture:
   - DMX address
   - pan channel
   - tilt channel
   - laser/dimmer channel
   - 8-bit or 16-bit pan/tilt
   - pan and tilt min/max limits
4. Save with `Save Project`. The project is stored as JSON.

All stage coordinates are stored in millimeters. The convention is:

- `X=0 Y=0` at stage center
- positive `Y` goes into stage depth
- `X` is left/right from center

## Calibrate the Moving Head

The intended workflow is a compact calibration cross near stage zero. You do not need to mark the whole stage.

1. Click `CALIBRATION WIZARD`.
2. The first point is selected and highlighted on the plan.
3. Enter the pan and tilt ranges from the fixture manual, for example `Pan 540 deg`, `Tilt 270 deg`.
4. Turn `Laser ON`.
5. Use the large `Pan -`, `Pan +`, `Tilt -`, `Tilt +` buttons and the jog step field until the beam hits the real calibration point.
6. Check the `Pan rotation` status. For a 540-degree 8-bit fixture, Pan values separated by 170 DMX are equivalent full turns. Use `Use recommended Pan turn` when offered, verify that the beam stayed on the point, then save.
7. Click `Save & Next` and repeat for the center-cross points. The branch lock compares each new point with the already saved neighboring points.
8. Use `Verify saved point` to return to the selected calibration value and flash it.
9. Click `Finish`. The wizard validates the points and fits the head automatically, then save the project.

The default calibration is a compact cross around stage zero, so you do not need to walk the whole stage:

- `center`: `X=0 Y=0`
- `left_1m`: `X=-1000 Y=0`
- `right_1m`: `X=1000 Y=0`
- `front_1m`: `X=0 Y=-1000`
- `back_1m`: `X=0 Y=1000`

In the wizard, use `Radius, mm` and `Reset to cross around zero` if you want another size, for example 500 mm or 2000 mm.

Pan and tilt are stored internally as `0-65535` in 16-bit mode. In 8-bit mode they are stored and sent as `0-255`.

The wizard rejects two different stage coordinates saved with the same Pan/Tilt values. It also warns about a likely 180-degree head-flip branch. Saved calibration points remain exact anchors after geometric fitting.

After fitting, clicks on the plan use the geometric head model. If the model is not fitted yet, the app falls back to interpolation from the saved points.

## Use with Vectorworks via DXF

1. Export the stage plan from Vectorworks as DXF in millimeters.
2. Keep the drawing origin aligned with stage center when possible.
3. Load the DXF with `Load DXF`.
4. The app reads common geometry: `LINE`, `LWPOLYLINE`, `POLYLINE`, `CIRCLE`, `INSERT`, `TEXT`, and `MTEXT`.
5. Click a point on the canvas to aim the laser there.
6. Select an object from the list or canvas, then use:
   - `Show selected center`
   - `Show selected corners`
   - `Next point`
   - `Previous point`

## PDF Plans

1. Load a PDF with `Load PDF`.
2. Hold `Shift` and click two known points on the PDF.
3. Enter the real distance between those points in millimeters.
4. After scaling, clicks on the PDF are converted to millimeters.

PDF support is intended as a practical raster underlay. DXF is preferred when exact object centers and corners are needed.

## DMX Channel Setup

The fixture channel fields are relative to the fixture start address.

Example for a fixture at DMX address `101`:

- pan channel `1` writes DMX channel `101`
- tilt channel `3` writes DMX channel `103`
- laser channel `5` writes DMX channel `105`

For 16-bit pan/tilt, the app writes high byte to the configured channel and low byte to the next channel.

## Safety

Use `EMERGENCY OFF` or press `Esc` to immediately send the laser channel off value. The current MVP does not replace a hardware laser safety interlock.
