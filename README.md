# OBS Remote Camera Controller

Proof of principle Python script that creates a dockable interface in [OBS](https://obsproject.com/de) to control any NDI PTZ enabled camera.

Developed for the [OBSBOT Tail Air](https://www.obsbot.com/de/obsbot-tail-air-streaming-camera)

Based on earlier work https://github.com/under-score/NDI-webRTC and https://github.com/under-score/NDI-PTZ

Needs MacOS, tested under Sequoia 15.1 but may work in other environments as well

Needs Python 3.8

Needs NDI SDK https://ndi.video/for-developers/ndi-sdk/download 

(NDI is a registered trademark of Vizrt NDI AB)

pip install ndi-python # for NDIlib

Needs the libraries sys, numpy, time, logging, socket, asyncio, aiohttp, aiortc, av and fractions

Firewall is OK but disable any VPN

With help of ChatGPT 4o


![Bildschirmfoto 2025-01-08 um 11 37 59](https://github.com/user-attachments/assets/13fc5d29-e6ef-471d-9fef-6e0714424032)
