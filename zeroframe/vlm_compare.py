"""One optional Pi worker for same-frame comparisons; only the parent owns the camera."""

import json
import multiprocessing
import os
import signal
import socket
import struct
from time import monotonic

from prepare_vlm import DEFAULT_MODEL
from .state import Status
from .vlm import LocalSeatVLM, protect_camera_process, seat_image, stop_worker


# A local sequenced-packet socket sends one bounded RAM crop atomically. The
# nonblocking parent can skip a sample without stalling the inference loop.
HEADER = struct.Struct("!QHH")
EDGE = 256
MAX_PACKET = HEADER.size + EDGE * EDGE * 3


def _worker(channel, parent_pid):
    try:
        protect_camera_process(live=True)
        if os.getppid() != parent_pid:
            return
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(60)
        from PIL import Image
        model = LocalSeatVLM(DEFAULT_MODEL)
        signal.alarm(0)
        channel.sendall(b"ready")
        while True:
            packet = channel.recv(MAX_PACKET)
            if not packet:
                break
            signal.alarm(15)
            protect_camera_process(live=True)
            frame_id, width, height = HEADER.unpack_from(packet)
            with Image.frombytes("RGB", (width, height), packet[HEADER.size:]) as image:
                del packet
                result = model.classify(image)
            channel.sendall(json.dumps([frame_id, result["label"], result["inference_seconds"],
                                        result["reason"]]).encode("ascii"))
            signal.alarm(0)
    except Exception:
        # Model exceptions can contain arbitrary input; send only a fixed error.
        try:
            channel.sendall(b"failed")
        except OSError:
            pass
    finally:
        channel.close()


class VLMComparison:
    def __init__(self, config):
        self.config = config
        self.process = self.channel = None
        self.ready = False
        self.pending = None
        self.next_seat = 0
        self.label = "VLM loading"

    def __enter__(self):
        protect_camera_process(live=True, worker=False)
        self.channel, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self.channel.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, MAX_PACKET * 2)
            self.channel.setblocking(False)
            self.process = multiprocessing.get_context("spawn").Process(
                target=_worker, args=(child, os.getpid()), daemon=True)
            self.process.start()
            self.deadline = monotonic() + 60
        except BaseException:
            self.channel.close()
            raise
        finally:
            child.close()
        return self

    def poll(self, latest_frame):
        result = None
        if self.channel is None:
            return
        try:
            message = self.channel.recv(1024)
        except BlockingIOError:
            if monotonic() < self.deadline or self.ready:
                return
            message = b"failed"
        except OSError:
            message = b""
        if message == b"ready":
            self.ready = True
            self.label = "VLM ready"
        elif not message or message == b"failed":
            self.close()
            self.label = "VLM stopped (error or deadline); YOLO running"
        else:
            frame_id, label, seconds, reason = json.loads(message)
            seat, submitted, captured, status = self.pending
            self.pending = None
            self.ready = True
            if frame_id != submitted or submitted != latest_frame or not 0 <= monotonic() - captured < 15:
                self.label = "VLM expired comparison"
            else:
                self.label = (f"#{frame_id} {seat} | seat {status} | VLM {Status(label).value} "
                              f"({seconds:.1f}s{', ' + reason if reason else ''})")
                result = (seat, captured, Status.UNKNOWN if reason else Status(label))
        print(self.label, flush=True)
        return result

    def submit(self, frame, states, frame_id, captured, *, reviews=None, lighting_luts=None):
        if not self.ready:
            return
        seats = self.config.seats
        eligible = reviews
        if reviews is not None:
            if not reviews:
                self.label = "VLM waiting for a marginal empty reference"
                return
            # Finish a pair on the next check, then resume rotation among candidates.
            eligible = {seat for seat, follow_up in reviews.items() if follow_up} or reviews
        for offset in range(len(seats)):
            index = (self.next_seat + offset) % len(seats)
            seat = seats[index].seat_id
            if eligible is None or seat in eligible:
                self.next_seat = (index + 1) % len(seats)
                break
        else:
            return
        try:
            protect_camera_process(live=True, worker=False)
            self._submit(frame, states, frame_id, captured, seat,
                         lighting_lut=lighting_luts.get(seat) if lighting_luts else None)
        except (ImportError, OSError, RuntimeError, ValueError):
            self.close()
            self.label = "VLM unavailable (privacy, crop or IPC); YOLO running"
            print(self.label, flush=True)

    def _submit(self, frame, states, frame_id, captured, seat, *, lighting_lut):
        status = states[seat].status.value
        with seat_image(frame, self.config, seat, lighting_lut=lighting_lut) as image:
            visibility = image.info["seat_visibility"]
            if not visibility["usable"]:
                self.label = f"#{frame_id} {seat} | seat {status} | VLM UNKNOWN ({visibility['reason']})"
                print(self.label, flush=True)
                return
            image.thumbnail((EDGE, EDGE))
            header = HEADER.pack(frame_id, image.width, image.height)
            try:
                self.channel.sendmsg([header, image.tobytes()])
            except BlockingIOError:
                return
        self.pending = (seat, frame_id, captured, status)
        self.deadline = captured + 15
        self.ready = False
        self.label = f"VLM processing #{frame_id} {seat}"

    def close(self):
        if self.process is not None:
            stop_worker(self.process, self.channel)
            self.process.close()
            self.process = None
            self.channel = None
        elif self.channel is not None:
            self.channel.close()
            self.channel = None
        self.ready = False
        self.pending = None

    def __exit__(self, *exc):
        self.close()
