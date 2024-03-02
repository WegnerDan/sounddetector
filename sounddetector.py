#!/usr/bin/env python3
# Tone detection shamelessly stolen from:
# https://benchodroff.com/2017/02/18/using-a-raspberry-pi-with-a-microphone-to-hear-an-audio-alarm-using-fft-in-python/
#
# this entire thing stolen from  Sufficiently-Advanced (Allen Pan)
#
# from __future__ import absolute_import
# from __future__ import print_function
import pyaudio
from numpy import zeros, linspace, short, fromstring, hstack, transpose, log
from scipy.fft import fft
from time import sleep
from collections import deque
import paho.mqtt.client as mqtt
import datetime
import time
import sys
import signal
from mqtt_auth import mqtthost, mqttuser, mqttpass

# from six.moves import range


def terminateProcess(signalNumber, frame):
    client.publish("sounddetector/state", "offline")
    sys.exit()


if __name__ == "__main__":
    # register the signals to be caught
    signal.signal(signal.SIGTERM, terminateProcess)

# mqtt stuff
if hasattr(mqtt, "CallbackAPIVersion"):
    # paho version 2 or greater
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="sounddetector")
else:
    # paho version below 2
    client = mqtt.Client(client_id="sounddetector")
client.username_pw_set(username=mqttuser, password=mqttpass)
client.connect(mqtthost, 1883, 300)

# Volume Sensitivity, 0.05: Extremely Sensitive, may give false alarms
#             0.1: Probably Ideal volume
#             1: Poorly sensitive, will only go off for relatively loud
SENSITIVITY = 1.0
# set debug=True to find out which number correspons to which mic
MICROPHONE_DEVICE_INDEX = 0

# Bandwidth for detection (i.e., detect frequencies within this margin of error of the TONE)
BANDWIDTH = 20
# How many 46ms blips before we declare a beep? (Take the beep length in ms, divide by 46ms, subtract a bit)
# 0.5 seconds = 500ms/8 = 10,8695652174
beeplength = 10
# How many beeps before we declare a tone?
tonelength = 5
# How many false 46ms blips before we declare the alarm is not ringing
resetlength = 12
# How many reset counts until we clear an active alarm?
clearlength = 2
# Enable blip, beep, and reset debug output
debug = True
# Show the most intense frequency detected (useful for configuration)
frequencyoutput = True

blipcount = 0
beepcount = 0
resetcount = 0
clearcount = 0
tone = False

# what devices are we using
if debug:
    pd = pyaudio.PyAudio()
    info = pd.get_host_api_info_by_index(0)
    numdevices = info.get("deviceCount")
    for i in range(0, numdevices):
        if (
            pd.get_device_info_by_host_api_device_index(0, i).get("maxInputChannels")
        ) > 0:
            print(
                "Input Device id ",
                i,
                " - ",
                pd.get_device_info_by_host_api_device_index(0, i).get("name"),
            )

# Alarm frequencies (Hz) to detect (Use audacity to record a wave and then do Analyze->Plot Spectrum)
D1 = 530
D2 = 675
F = 755
G = 806
D5 = 4000
# frequency ranges for each note
"""rangeD1 = range(D1-BANDWIDTH,D4+BANDWIDTH)
rangeD2 = range(D2-BANDWIDTH,D2+BANDWIDTH)
rangeF = range(F-BANDWIDTH,F+BANDWIDTH)
rangeG = range(G-BANDWIDTH,G+BANDWIDTH)
rangeD5 = range(D5-BANDWIDTH,D5+BANDWIDTH)"""
# These numbers work for my ocarina in my house with a blue yeti, ymmv
minD1 = D1 - BANDWIDTH
maxD1 = D1 + BANDWIDTH
minD2 = D2 - BANDWIDTH
maxD2 = D2 + BANDWIDTH
minF = F - 40
maxF = F + BANDWIDTH
minG = G - BANDWIDTH
maxG = G + BANDWIDTH
minD5 = D5 - BANDWIDTH
maxD5 = D5 + BANDWIDTH

# Song note sequences
doorbell = deque(["D1", "D2"])
test = deque(["D2", "F"])
# heard note sequence deque
notes = deque(["G", "G"], maxlen=2)

# Show the most intense frequency detected (useful for configuration)
frequencyoutput = True
freqNow = 1.0
freqPast = 1.0

# Set up audio sampler -
NUM_SAMPLES = 2048
SAMPLING_RATE = 44100  # make sure this matches the sampling rate of your mic!
pa = pyaudio.PyAudio()
_stream = pa.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=SAMPLING_RATE,
    input=True,
    input_device_index=MICROPHONE_DEVICE_INDEX,
    frames_per_buffer=NUM_SAMPLES,
)

if debug:
    print("Alarm detector working. Press CTRL-C to quit.")
client.publish("sounddetector/state", "starting")

while True:
    timeout = 30  # report in to mqtt every 30 seconds
    timeout_start = time.time()
    while time.time() < timeout_start + timeout:
        test = 0
        if test == 5:
            break
        test -= 1

        while _stream.get_read_available() < NUM_SAMPLES:
            sleep(0.05)
        audio_data = fromstring(
            _stream.read(_stream.get_read_available(), exception_on_overflow=False),
            dtype=short,
        )[-NUM_SAMPLES:]
        # Each data point is a signed 16 bit number, so we can normalize by dividing 32*1024
        normalized_data = audio_data / 32768.0
        intensity = abs(fft(normalized_data))[: NUM_SAMPLES // 2]
        frequencies = linspace(0.0, float(SAMPLING_RATE) // 2, num=NUM_SAMPLES // 2)
        if frequencyoutput:
            which = intensity[1:].argmax() + 1
            # use quadratic interpolation around the max
            if which != len(intensity) - 1:
                y0, y1, y2 = log(intensity[which - 1 : which + 2 :])
                x1 = (y2 - y0) * 0.5 / (2 * y1 - y2 - y0)
                # find the frequency and output it
                freqPast = freqNow
                freqNow = (which + x1) * SAMPLING_RATE / NUM_SAMPLES
            else:
                freqNow = which * SAMPLING_RATE / NUM_SAMPLES

        if debug:
            print("\t\t\t\tfreq=", freqNow, "\t", freqPast)
        if debug:
            print("\t\t\t\tnotes=", notes)

        if (
            max(
                intensity[
                    (frequencies < maxD5 + BANDWIDTH)
                    & (frequencies > minD1 - BANDWIDTH)
                ]
            )
            > max(
                intensity[(frequencies < maxD5 - 1000) & (frequencies > minD1 - 2000)]
            )
            + SENSITIVITY
        ):
            blipcount += 1
            resetcount = 0
            if debug:
                print("\t\tBlip", blipcount)
            if blipcount >= beeplength:
                blipcount = 0
                resetcount = 0
                beepcount += 1
                if debug:
                    print("\tBeep", beepcount, freqNow)
                if beepcount >= tonelength:
                    clearcount = 0
                    tone = True
                    if debug:
                        print("ToneDetected")
                    beepcount = 0
        else:
            blipcount = 0
            resetcount += 1
            if debug:
                print("\t\t\treset", resetcount)
            if resetcount >= resetlength:
                resetcount = 0
                beepcount = 0
                if tone:
                    clearcount += 1
                    if debug:
                        print("\t\tclear", clearcount)
                    if clearcount >= clearlength:
                        clearcount = 0
                        if debug:
                            print("Tone Too Short - Cleared")
                        tone = False

        #        if minD1 <= freqPast <= maxD5 and abs(freqNow-freqPast) <= 20:
        if tone:
            if debug:
                print("\t\t\t\tRicecooker done!")
            ricecooker_finish_time = datetime.datetime.now(
                datetime.timezone.utc
            ) + datetime.timedelta(seconds=10)
            ricecooker_finish_time = (
                '{"datetime":"' + ricecooker_finish_time.astimezone().isoformat() + '"}'
            )

            client.publish(
                "sounddetector/ricecooker_finish_time", ricecooker_finish_time
            )

    client.publish("sounddetector/state", "online")
