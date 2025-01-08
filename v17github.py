#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

Created on Wed Jan 8 08:05:18 2024
Converts a NDI video stream to WebRTC and adds PTZ control to web app
Needs OSX Sequoia 15.1
Needs Python 3.8
Needs NDI SDK https://ndi.video/for-developers/ndi-sdk/download/
Needs the libraries sys, numpy, time, logging, asyncio, NDIlib, aiohttp, aiortc, av and fractions
With help of ChatGPT 4o

@author: wjst
"""

import sys
import numpy as np
import time
import logging
import socket
import asyncio
import NDIlib as ndi
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from av import VideoFrame
from fractions import Fraction

# Global variables for NDI PTZ control
ndi_target_ip = None
ndi_target_port = '5960'  # Default NDI PTZ control port

# Logging setup
logging.basicConfig(level=logging.INFO)

def ndi_connect(source_name=None):
    if not ndi.initialize():
        raise RuntimeError("Failed to initialize NDI.")

    ndi_find = ndi.find_create_v2()
    if not ndi_find:
        ndi.destroy()
        raise RuntimeError("Failed to create NDI finder.")

    # Find sources
    sources = []
    while not sources:
        print('Looking for sources ...')
        ndi.find_wait_for_sources(ndi_find, 5000)
        sources = ndi.find_get_current_sources(ndi_find)

    if not sources:
        ndi.find_destroy(ndi_find)
        ndi.destroy()
        raise RuntimeError("No NDI sources found.")

    for s in sources:
        logging.info(f"Available NDI Source: {s.ndi_name}")

    if source_name:
        chosen_source = next((src for src in sources if src.ndi_name == source_name), None)
        if not chosen_source:
            logging.warning(f"Specified NDI source '{source_name}' not found. Using first source.")
            chosen_source = sources[0]
    else:
        chosen_source = sources[0]

    # Set NDI target IP for PTZ control
    global ndi_target_ip
    ndi_target_ip = chosen_source.url_address.split(':')[0]

    print(f"Selected Source: {chosen_source.ndi_name}")
    print(f"Source IP: {ndi_target_ip}")

    ndi_recv_create = ndi.RecvCreateV3(
        color_format=ndi.RECV_COLOR_FORMAT_BGRX_BGRA,
        bandwidth=ndi.RECV_BANDWIDTH_LOWEST,
        allow_video_fields=False
    )

    ndi_recv = ndi.recv_create_v3(ndi_recv_create)
    if not ndi_recv:
        ndi.find_destroy(ndi_find)
        ndi.destroy()
        raise RuntimeError("Failed to create NDI receiver.")

    ndi.recv_connect(ndi_recv, chosen_source)
    time.sleep(2)  # Allow receiver to lock onto source

    ndi.find_destroy(ndi_find)
    start_time = time.time()

    return ndi_recv, start_time

def ndi_receive_frame(ndi_recv):
    t, v, a, _ = ndi.recv_capture_v2(ndi_recv, timeout_in_ms=5000)
    if t == ndi.FRAME_TYPE_VIDEO and v is not None:
        frame_data = np.copy(v.data)
        frame_data = np.delete(frame_data, 3, axis=2)  # Remove alpha channel
        ndi.recv_free_video_v2(ndi_recv, v)
        return frame_data
    return None

def send_ndi_ptz_command(command, value):
    if ndi_target_ip is None:
        logging.error("NDI target IP not set. Ensure a source is connected.")
        return

    try:
        if command == "pan_tilt_speed":
            ndi.recv_ptz_pan_tilt_speed(ndi_recv, value["pan"], value["tilt"])
        elif command == "zoom_speed":
           zoom = value.get("zoom")
           if -1 <= zoom <= 1:
               ndi.recv_ptz_zoom_speed(ndi_recv, zoom)
        elif command == "home":
            ndi.recv_ptz_pan_tilt(ndi_recv, 0, 0)
        elif command == "auto":
            ndi.recv_ptz_auto_focus(ndi_recv)
        elif command == "recall_preset":
            ndi.recv_ptz_recall_preset(ndi_recv, value["preset"], 0.5)
        elif command == "store_preset":
            ndi.recv_ptz_store_preset(ndi_recv, value["preset"])    
        elif command == "focus":
           distance = value.get("distance")
           if 0 <= distance <= 1:
               ndi.recv_ptz_focus(ndi_recv, distance)
        logging.info(f"PTZ command executed: {command} with value {value}")
    except Exception as e:
        logging.error(f"Failed to execute PTZ command: {e}")

class NDIVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, ndi_recv, start_time):
        super().__init__()
        self.ndi_recv = ndi_recv
        self.start_time = start_time
        self.last_frame = None

    async def recv(self):
        now = time.time()

        frame_data = ndi_receive_frame(self.ndi_recv)
        if frame_data is not None:
            self.last_frame = frame_data
        elif self.last_frame is None:
            frame_data = np.zeros((480, 640, 3), dtype=np.uint8)
        else:
            frame_data = self.last_frame

        frame = VideoFrame.from_ndarray(frame_data, format="bgr24")
        frame.pts = int((now - self.start_time) * 90000)
        frame.time_base = Fraction(1, 90000)

        return frame

async def handle_ptz_control(request):
    data = await request.json()
    try:
        command = data['command']
        value = data['value']
        send_ndi_ptz_command(command, value)
        return web.json_response({"status": "success"})
    except KeyError:
        return web.Response(status=400, text="Invalid PTZ command data")

async def offer(request):
    params = await request.json()
    if "sdp" not in params or "type" not in params:
        return web.Response(status=400, text="Invalid SDP")

    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")

    global ndi_recv, ndi_start_time
    ndi_track = NDIVideoTrack(ndi_recv, ndi_start_time)
    pc.addTrack(ndi_track)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

async def index(request):
    return web.Response(
        content_type="text/html",
        text="""
<!DOCTYPE html>
<html>
<head><title></title></head>
<body>
<style>
input {
    width: 40px;
}

body, div {
    background-color: #000000;
}

button {
    appearance: none;
    background-color: #FAFBFC;
    border: 1px solid rgba(27, 31, 35, 0.15);
    border-radius: 6px;
    box-sizing: border-box;
    color: #24292E;
    cursor: pointer;
    display: inline-block;
    font-family: -apple-system, system-ui, "Segoe UI", Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
    font-size: 12px;
    line-height: 20px;
    list-style: none;
    position: relative;
    transition: background-color 0.2s cubic-bezier(0.3, 0, 0.5, 1);
    user-select: none;
    -webkit-user-select: none;
    touch-action: manipulation;
    vertical-align: middle;
    text-align: center;
    white-space: nowrap;
    word-wrap: break-word;
    width: 50px;
    height: 50px;
    margin-bottom: 5px;
}

button:hover {
    text-decoration: none;
    transition-duration: 0.1s;
    opacity: 0.4
}

#preset-1, #preset-2, #preset-3, #preset-4, #preset-5, #preset-6  {
    background-color: blue;
    color: white;
}

#home, #pan-left, #pan-right, #tilt-up, #tilt-down {
    background-color: yellow;
}

div {
    float:left;
}
</style>


OBSBOT Tail Air Controller
<div>
    <button id="focus-near" onclick="adjustFocus(-0.05)">Focus+</button>
    <button id="tilt-up" onmousedown="sendPTZCommand('pan_tilt_speed', {pan: 0, tilt: 0.05})" onmouseup="sendPTZCommand('pan_tilt_speed', {pan: 0, tilt: 0})">Up</button>
    <button id="focus-far" onclick="adjustFocus(0.05)">Focus+</button>
    <br>
    <button id="pan-left" onmousedown="sendPTZCommand('pan_tilt_speed', {pan: 0.05, tilt: 0})" onmouseup="sendPTZCommand('pan_tilt_speed', {pan: 0, tilt: 0})">Left</button>
    <button id="home" onclick="sendPTZCommand('home', {})">Home</button>
    <button id="pan-right" onmousedown="sendPTZCommand('pan_tilt_speed', {pan: -0.05, tilt: 0})" onmouseup="sendPTZCommand('pan_tilt_speed', {pan: 0, tilt: 0})">Right</button>
    <br>
    <button id="zoom-in" onmousedown="sendPTZCommand('zoom_speed', {zoom: 0.2})" onmouseup="sendPTZCommand('zoom_speed', {zoom: 0})">Zoom+</button>    
    <button id="tilt-down" onmousedown="sendPTZCommand('pan_tilt_speed', {pan: 0, tilt: -0.05})" onmouseup="sendPTZCommand('pan_tilt_speed', {pan: 0, tilt: 0})">Down</button>
    <button id="zoom-out" onmousedown="sendPTZCommand('zoom_speed', {zoom: -0.2})" onmouseup="sendPTZCommand('zoom_speed', {zoom: 0})">Zoom-</button>
</div>
<div>
    &nbsp;
</div>
<div>
    <button id="preset-1" onclick="sendPTZCommand('recall_preset', {preset: 1})" oncontextmenu="storePresetAndEdit(this, 1); return false;">1</button>
    <button id="preset-2" onclick="sendPTZCommand('recall_preset', {preset: 2})" oncontextmenu="storePresetAndEdit(this, 2); return false;">2</button>
    <button id="preset-3" onclick="sendPTZCommand('recall_preset', {preset: 3})" oncontextmenu="storePresetAndEdit(this, 3); return false;">3</button>
    <br>
    <button id="preset-4" onclick="sendPTZCommand('recall_preset', {preset: 4})" oncontextmenu="storePresetAndEdit(this, 4); return false;">4</button>
    <button id="preset-5" onclick="sendPTZCommand('recall_preset', {preset: 5})" oncontextmenu="storePresetAndEdit(this, 4); return false;">5</button>
    <button id="preset-6" onclick="sendPTZCommand('recall_preset', {preset: 6})" oncontextmenu="storePresetAndEdit(this, 4); return false;">6</button>
    <br>    
    <button id="start" onclick="startStream()">Start</button>
    <button id="stop" onclick="stopStream()">Stop</button>
    <button id="focus-auto" onclick="sendPTZCommand('auto', {})">Auto</button>

</div>
<p style="clear: both;">
<div>
    <video id="video" width="320" autoplay></video>
</div>


<script>
let focusDistance = 0.5; // Initialize focus distance

async function sendPTZCommand(command, value) {
    try {
        const response = await fetch('/ptz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command, value })
        });
        const data = await response.json();
        console.log('PTZ Command Response:', data);
    } catch (error) {
        console.error('Error sending PTZ command:', error);
    }
}

function storePresetAndEdit(button, preset) {
    sendPTZCommand('store_preset', {preset: preset});

    // Enable editing of the button label
    const currentLabel = button.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.value = currentLabel;
    button.textContent = '';
    button.appendChild(input);
    input.focus();

    input.addEventListener('blur', () => {
        button.textContent = input.value || currentLabel;
    });

    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            input.blur();
        }
    });
}

function adjustFocus(change) {
    focusDistance = Math.max(0, Math.min(1, focusDistance + change)); // Keep focus within [0, 1]
    sendPTZCommand('focus', {distance: focusDistance});
}

let pc = null;
const video = document.getElementById('video');

async function negotiate() {
    pc = new RTCPeerConnection();
    pc.ontrack = (event) => { video.srcObject = event.streams[0]; };
    pc.addTransceiver('video', { direction: 'recvonly' });
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const response = await fetch('/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pc.localDescription)
    });
    const answer = await response.json();
    await pc.setRemoteDescription(answer);
}

function startStream() {
    if (pc) {
        pc.close();
        pc = null;
    }
    negotiate();
}

function stopStream() {
    if (pc) {
        pc.close();
        pc = null;
    }
    video.srcObject = null;
    console.log("Stream stopped");
}
</script>

</body>
</html>
""")

async def cleanup(app):
    logging.info("Server shutting down...")

async def main():
    global ndi_recv, ndi_start_time
    ndi_recv, ndi_start_time = ndi_connect()

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_post("/ptz", handle_ptz_control)
    app.on_shutdown.append(cleanup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8080)
    logging.info("Server running at http://127.0.0.1:8080")
    await site.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Interrupted")
