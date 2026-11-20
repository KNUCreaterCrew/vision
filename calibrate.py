"""Click seat polygons on a RAM-only camera snapshot; save coordinates only."""

import argparse
import os
from pathlib import Path
import re
import sys

from zeroframe.camera import camera_session, read_frame, require_opencv
from zeroframe.roi import ROIConfig, SeatROI, load_config, save_config, validate_ids


WINDOW = "ZeroFrame - local calibration"
DEMO_SEATS = ("A-01", "A-02", "B-01", "B-02")


def _next_seat_id(room_id, requested, seats):
    if len(seats) < len(requested):
        return requested[len(seats)]
    used = {seat.seat_id for seat in seats}
    if room_id == "IT4-101" and used.issubset(DEMO_SEATS):
        for seat_id in DEMO_SEATS:
            if seat_id not in used:
                return seat_id
    last = seats[-1].seat_id
    suffix = re.search(r"[0-9]+$", last)
    if suffix:
        digits = suffix.group()
        prefix, number, width = last[:suffix.start()], int(digits) + 1, len(digits)
    else:
        prefix, number, width = last + "-", 2, 2
    while True:
        candidate = f"{prefix}{number:0{width}d}"
        if candidate not in used:
            return candidate
        number += 1


def _draw_polygon(cv2, image, points, color, *, closed):
    for point in points:
        cv2.circle(image, point, 3, color, -1)
    for a, b in zip(points, points[1:]):
        cv2.line(image, a, b, color, 2)
    if closed and len(points) > 2:
        cv2.line(image, points[-1], points[0], color, 2)


def calibrate(room_id, seat_ids=None, *, max_seats=None, camera=0, output="roi_config.json", overwrite=False):
    output = Path(output)
    if max_seats is not None:
        if type(max_seats) is not int or max_seats < 1 or seat_ids is not None:
            raise ValueError("Specify a positive maximum seat count or an explicit seat list.")
        seat_ids = ["A-01"]
        if overwrite and output.exists():
            try:
                previous = load_config(output)
            except ValueError:
                pass  # The operator can replace an invalid coordinate file.
            else:
                if previous.room_id == room_id:
                    seat_ids = [seat.seat_id for seat in previous.seats[:max_seats]]
    validate_ids(room_id, seat_ids)
    minimum = 1 if max_seats is not None else len(seat_ids)
    if output.exists() and not overwrite:
        raise FileExistsError(f"{output} already exists; choose --output or explicitly use --overwrite")
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        raise RuntimeError(
            "Calibration needs a local Linux desktop display and mouse. "
            "Use the Raspberry Pi desktop, not a headless SSH session. See README.md."
        )
    cv2 = require_opencv()
    snapshot = None
    display = None
    window_open = False
    try:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        window_open = True
        print("Preview: SPACE freezes a frame; Q/ESC cancels without saving.")
        with camera_session(camera) as capture:
            while True:
                snapshot = read_frame(capture)
                cv2.imshow(WINDOW, snapshot)
                key = cv2.waitKey(20) & 0xFF
                if key in (27, ord("q")) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    return None
                if key == ord(" "):
                    break

        height, width = snapshot.shape[:2]
        points = []
        seats = []
        current_id = seat_ids[0]
        message = ""

        def on_mouse(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN and current_id is not None:
                if 0 <= x < width and 0 <= y < height:
                    points.append((x, y))

        cv2.setMouseCallback(WINDOW, on_mouse)
        print("Click corners in order; N/ENTER confirms a seat; U undoes; S saves; Q/ESC cancels.")
        print("Include the seated head/shoulders/upper-body area, not just the desk surface.")
        print(f"Requested seats: {', '.join(seat_ids)}. Start with {seat_ids[0]}.")
        while True:
            display = snapshot.copy()
            for seat in seats:
                _draw_polygon(cv2, display, seat.polygon, (0, 220, 0), closed=True)
                cv2.putText(display, seat.seat_id, seat.polygon[0], cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1)
            _draw_polygon(cv2, display, points, (0, 220, 255), closed=False)
            label = f"Seat: {current_id} | Points: {len(points)} | Confirmed: {len(seats)}"
            if max_seats is not None:
                label += f"/{max_seats}"
                if current_id is None:
                    label = f"Confirmed: {len(seats)}/{max_seats} | S: save | U: edit last seat"
            cv2.putText(display, label, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1)
            cv2.putText(display, "Click corners | N: next | U: undo | S: save | Q: cancel", (10, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            cv2.putText(display, "ROI: include seated head/shoulders, not just desk", (10, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            if message:
                cv2.putText(display, "Invalid or unfinished ROI; see terminal", (10, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            cv2.imshow(WINDOW, display)
            key = cv2.waitKey(20) & 0xFF
            if key in (27, ord("q")) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                return None
            if key == ord("u"):
                message = ""
                if points:
                    points.pop()
                elif seats:
                    reopened = seats.pop()
                    current_id = reopened.seat_id
                    points.extend(reopened.polygon)
            elif key in (10, 13, ord("n")) and current_id is not None:
                try:
                    seat = SeatROI(current_id, points)
                    ROIConfig(room_id, width, height, tuple(seats + [seat]))
                except ValueError as error:
                    message = f"{current_id}: {len(points)} points; {error}. Click corners, then N/ENTER."
                    print(message)
                else:
                    seats.append(seat)
                    points.clear()
                    message = ""
                    if len(seats) == max_seats:
                        current_id = None
                        print("Maximum reached. Press S to save.")
                    else:
                        current_id = _next_seat_id(room_id, seat_ids, seats)
                        print(f"Confirmed {seat.seat_id}. Next seat: {current_id}. Click corners, then N/ENTER.")
                        if len(seats) >= minimum:
                            print("Press S to save, or click to add another seat.")
            elif key == ord("s"):
                if len(seats) < minimum or points:
                    message = (
                        f"{len(seats)} seats confirmed. "
                        f"{current_id}: {len(points)} points. Click its corners, then N/ENTER before S."
                    )
                    print(message)
                    continue
                config = ROIConfig(room_id, width, height, tuple(seats))
                save_config(config, output, overwrite=overwrite)
                print(f"Saved {len(seats)} seat polygons ({width}x{height}) to {output}; no images saved.")
                return config
    except cv2.error as error:
        raise RuntimeError(f"OpenCV camera/window error: {error}") from error
    finally:
        if snapshot is not None:
            del snapshot
        if display is not None:
            del display
        if window_open:
            cv2.destroyAllWindows()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True, help="Room ID, e.g. IT4-101")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--max-seats", type=int, help="Maximum polygons to draw; seat IDs are assigned automatically")
    selection.add_argument("--seats", nargs="+", help="Seat IDs in calibration order, e.g. A-01 A-02")
    parser.add_argument("--camera", type=int, default=0, help="Local camera index (default: 0)")
    parser.add_argument("--output", default="roi_config.json", help="Coordinate JSON output")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing coordinate file")
    args = parser.parse_args(argv)
    try:
        calibrate(args.room, args.seats, max_seats=args.max_seats, camera=args.camera, output=args.output, overwrite=args.overwrite)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Calibration failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Calibration cancelled; no new coordinates saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
