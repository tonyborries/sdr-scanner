import asyncio
import collections
from multiprocessing import shared_memory, Process, Value
import numpy as np
import os
import socket
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Tuple
import websockets

from gnuradio import gr

from .const import AUDIO_SAMPLERATE
from .hpSharedMem import HighPerformanceCircularBuffer


LOCAL_AUDIO_SUPPORT = True
try:
    import pyaudio
except ImportError:
    LOCAL_AUDIO_SUPPORT = False
    print("Warning: Missing pyAudio, Local Audio support not available.")

ICECAST_SUPPORT = True
try:
    import lameenc
    import requests
except ImportError:
    ICECAST_SUPPORT = False
    print("Warning: Missing Packages, Icecast support not available.")


class AudioServerConfig(object):
    """
    Ran in the Supervisor process, builds the ShmBuffers to give to the AudioServer process and Receiver Senders
    """
    def __init__(self, numInputStreams: int, outputConfigDicts: List[Dict[Any, Any]]):
        self._numInputStreams = numInputStreams

        ###
        # Shared Memory Input Buffers

        self.inputStreamShmBuffers: List[shared_memory.SharedMemory] = []
        self.inputStreamHeadIdxs: List[Any] = []
        self.inputStreamTailIdxs: List[Any] = []

        for i in range(0, numInputStreams):
            self.inputStreamShmBuffers.append(shared_memory.SharedMemory(
                create=True,
                size=AUDIO_SAMPLERATE,  # means we effectively have a 0.25 second buffer
            ))
            self.inputStreamHeadIdxs.append(Value('i', lock=False))
            self.inputStreamHeadIdxs[i].value = 0
            self.inputStreamTailIdxs.append(Value('i', lock=False))
            self.inputStreamTailIdxs[i].value = 0
        
        self._outputConfigDicts = outputConfigDicts

    def getInputShmBuffers(self, inputStreamIdx: int) -> Tuple[shared_memory.SharedMemory, Any, Any]:
        return (
            self.inputStreamShmBuffers[inputStreamIdx],
            self.inputStreamHeadIdxs[inputStreamIdx],
            self.inputStreamTailIdxs[inputStreamIdx],
        )

    def getProcess(self):
        return Process(
            target=AudioServer.runAsProcess, args=(
                self._numInputStreams,
                self.inputStreamShmBuffers,
                self.inputStreamHeadIdxs,
                self.inputStreamTailIdxs,
                self._outputConfigDicts,
            )
        )

    def cleanup(self):
        """
        Release ShmBuffers
        """
        for shmBuf in self.inputStreamShmBuffers:
            shmBuf.close()
            shmBuf.unlink()
        self.inputStreamShmBuffers = []

    @classmethod
    def getOutputFromConfig(cls, configDict):
        if configDict['type'].lower() == 'local':
            return AudioServerOutput_Local()
        elif configDict['type'].lower() == 'udp':
            return AudioServerOutput_UDP(configDict['serverIp'], configDict['serverPort'])
        elif configDict['type'].lower() == 'icecast':
            return AudioServerOutput_Icecast(configDict['url'], configDict['password'])
        elif configDict['type'].lower() == 'websocket':
            return AudioServerOutput_Websocket(configDict['host'], configDict['port'])
        raise Exception(f"Unknown Audio Output Type: {configDict}")


class AudioSender(object):
    """
    Send an audioStream to a shared buffer
    """
    def __init__(self, audioShmBuffer: shared_memory.SharedMemory, headIdx: Any, tailIdx: Any):

        self.audioCircularBuffer = HighPerformanceCircularBuffer(
            shmBuffer=audioShmBuffer,
            itemDtype=np.dtype('float32'),
            headPointer=headIdx,
            tailPointer=tailIdx,
        )

    def write(self, samples: List[float]) -> int:
        """
        returns the number successfully written
        """
        numWrote = self.audioCircularBuffer.write(samples)
        return numWrote


class AudioSender_grEmbeddedPythonBlock(gr.sync_block):
    """
    gnuradio block that sends the samples to the Audio server with AudioSender()
    """

    def __init__(self, audioSender: AudioSender):
        gr.sync_block.__init__(
            self,
            name='AudioSender Embedded Python Block',
            in_sig=[np.float32],
            out_sig=[]
        )
        self._audioSender = audioSender

    def work(self, input_items, output_items):
        numWrote = self._audioSender.write(input_items[0])
        return numWrote


class AudioServer(object):
    """
    Stand-alone process, receives audio streams from Receivers, mixes them down.

    input samples are floats, we convert to short for outputs
    """
    BUFFER_LEN = 10000
    BUFFER_TARGET_LEN = 4000  # if the buffers are larger than this, we start discarding samples to avoid building up latency

    def __init__(
            self,
            numInputStreams: int,
            inputStreamShmBuffers: List[shared_memory.SharedMemory],
            inputStreamHeadIdxs: List[Any],
            inputStreamTailIdxs: List[Any],
            outputConfigDicts: List[Dict[Any, Any]],
        ) -> None:
        self._numInputStreams = numInputStreams
        self.inputStreamShmBuffers = inputStreamShmBuffers
        self.inputStreamHeadIdxs = inputStreamHeadIdxs
        self.inputStreamTailIdxs = inputStreamTailIdxs

        self.inputStreamCircularBuffers: List[HighPerformanceCircularBuffer] = []
        for i in range(0, numInputStreams):
            self.inputStreamCircularBuffers.append(HighPerformanceCircularBuffer(
                shmBuffer=self.inputStreamShmBuffers[i],
                itemDtype=np.dtype('float32'),
                headPointer=self.inputStreamHeadIdxs[i],
                tailPointer=self.inputStreamTailIdxs[i],
            ))

        self._outputs: List[AudioServerOutput_Base] = []
        self._outputConfigDicts = outputConfigDicts
        if self._outputConfigDicts:
            for oc in self._outputConfigDicts:
                self._outputs.append(AudioServerConfig.getOutputFromConfig(oc))
        else:
            self._outputs.append(AudioServerOutput_Local())

        self._stopFlag = False

    def stop(self) -> None:
        self._stopFlag = True

    def run(self) -> None:
        print("Audio Server Running")

        mixBuffers: List[collections.deque] = []
        for i in range(0, self._numInputStreams):
            mixBuffers.append( collections.deque(maxlen=self.BUFFER_LEN) )

        for o in self._outputs:
            o.reconnect()

        try:
            os.nice(-5)
        except Exception as e:
            print("Couldn't nice ourself (only root can)")
            print(e)

        ###
        # Mix Loop

        startTime = time.time()
        samplesMixed = 0
        while not self._stopFlag:
            # Read ShmBuffers
            for i in range(0, self._numInputStreams):
                inBuf: List[float] = []
                numRead = self.inputStreamCircularBuffers[i].read(inBuf)
                if numRead:
                    mixBuffers[i].extend(inBuf)
            # print("")

            # Mix Audio
            curTime = time.time()
            samplesToMix = int((curTime - startTime) * AUDIO_SAMPLERATE) - samplesMixed
            newSamples: List[int] = []
            for _ in range(0, samplesToMix):
                outSum = 0.0
                for i in range(0, self._numInputStreams):
                    buf = mixBuffers[i]
                    lenBuf = len(buf)
                    if lenBuf > 0:
                        outSum += buf.popleft()

                # convert to short
                i_out = int(outSum * 32767.0)
                if i_out > 32767:
                    i_out = 32767
                elif i_out < -32767:
                    i_out = -32767

                newSamples.append(i_out)
            samplesMixed += samplesToMix

            for i in range(0, self._numInputStreams):
                buf = mixBuffers[i]
                lenBuf = len(buf)
                if lenBuf > self.BUFFER_TARGET_LEN:
                    print(f"AudioServer - mixBuf - Discarding {lenBuf - self.BUFFER_TARGET_LEN} samples")
                    for _ in range(0, lenBuf - self.BUFFER_TARGET_LEN):
                        buf.popleft()


            # Send to outputs
            for o in self._outputs:
                o.send(newSamples)

            time.sleep(0.001)

        print("Audio Server Stop")
        for o in self._outputs:
            o.close()

    @classmethod
    def runAsProcess(
        cls,
        numInputStreams: int,
        inputStreamShmBuffers: List[shared_memory.SharedMemory],
        inputStreamHeadIdxs: List[Any],
        inputStreamTailIdxs: List[Any],
        outputConfigDicts: List[Dict[Any, Any]],
    ) -> None:
        audioServer = cls(numInputStreams, inputStreamShmBuffers, inputStreamHeadIdxs, inputStreamTailIdxs, outputConfigDicts)
        audioServer.run()


class AudioServerOutput_Base(object):
    def __init__(self) -> None:
        pass

    def reconnect(self) -> None:
        raise NotImplementedError()

    def close(self) -> None:
        raise NotImplementedError()
    
    def send(self, samples: List[int]) -> None:
        raise NotImplementedError()


class AudioServerOutput_Local(AudioServerOutput_Base):
    """
    Play audio locally with pyAudio / PortAudio
    """
    FRAMES_PER_BUFFER = 1000

    def __init__(self) -> None:
        self._outputBuffer: collections.deque = collections.deque(maxlen=self.FRAMES_PER_BUFFER * 4)

        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._pyaudioStream: Optional[pyaudio.Stream] = None

    def reconnect(self) -> None:
        """
        Initial connect or reconnect
        """

        self.close()

        # init the pyAudio stream
        self._pyaudio = pyaudio.PyAudio()

        # Open stream using callback (3)
        self._pyaudioStream = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=AUDIO_SAMPLERATE,
            output=True,
            stream_callback=self._pyAudioCb,
            frames_per_buffer=1000,
        )

        # Dump outputBuffer 
        self._outputBuffer.clear()

    def close(self) -> None:
        if self._pyaudioStream is not None:
            try:
                self._pyaudioStream.close()
            except Exception as e:
                print("Failed closing pyaudio stream")
                print(e)
            self._pyaudioStream = None

        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception as e:
                print("Failed terminating pyaudio")
                print(e)
            self._pyaudio = None

    def _pyAudioCb(self, in_data, frame_count, time_info, status) -> Tuple[bytes, int]:

        # If len(data) is less than requested frame_count, PyAudio automatically
        # assumes the stream is finished, and the stream stops - provide 0s if empty buffer

        # status flags - paInputUnderflow, paInputOverflow, paOutputUnderflow, paOutputOverflow, paPrimingOutput

        outdata = np.zeros([frame_count],  dtype=np.int16)
        try:
            for i in range(0, frame_count):
                outdata[i] = self._outputBuffer.popleft()
        except IndexError:
            pass

        outputBufferLen = len(self._outputBuffer)
        if outputBufferLen > self.FRAMES_PER_BUFFER:
            # buffer is growing, discard samples to keep it in check
            if outputBufferLen > self.FRAMES_PER_BUFFER * 2:
                for _ in range(0, self.FRAMES_PER_BUFFER):
                    self._outputBuffer.popleft()
            else:
                self._outputBuffer.popleft()

        return (outdata.tobytes(), pyaudio.paContinue)

    def send(self, samples: List[int]) -> None:
        self._outputBuffer.extend(samples)

        if self._pyaudioStream is None or not self._pyaudioStream.is_active():
            print("pyAudio Stream Inactive - Reconnecting")
            self.reconnect()


class AudioServerOutput_UDP(AudioServerOutput_Base):
    """
    Send raw audio to a UDP port.

    Currently only supports short int samples

        nc -l -u 12345 | sox -t raw -r 16k -e signed-integer -b 16 -c 1 - -t alsa
    """
    SAMPLES_PER_PACKET = 100
    BUFFER_LEN = 10000

    def __init__(self, serverIp, serverPort) -> None:
        self._outputBuffer: collections.deque = collections.deque(maxlen=self.BUFFER_LEN)

        self._socket: Optional[socket.socket] = None
        self._serverIp = serverIp
        self._serverPort = serverPort

    def reconnect(self) -> None:
        """
        Initial connect or reconnect
        """
        self.close()
        # init the socket
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Dump outputBuffer 
        self._outputBuffer.clear()

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception as e:
                print("Failed closing UDP socket")
                print(e)
            self._socket = None

    def send(self, samples: List[int]) -> None:
        self._outputBuffer.extend(samples)
        while len(self._outputBuffer) > self.SAMPLES_PER_PACKET:
            outdata: np.ndarray = np.ndarray([self.SAMPLES_PER_PACKET],  dtype=np.int16)

            for i in range(0, self.SAMPLES_PER_PACKET):
                outdata[i] = self._outputBuffer.popleft()

            try:
                if self._socket is None:
                    self.reconnect()
                    return
                self._socket.sendto(outdata.tobytes(), (self._serverIp, self._serverPort))
            except Exception as e:
                print("Failed Sending to UDP - reconnect")
                print(e)
                self.reconnect()


class AudioServerOutput_Icecast(AudioServerOutput_Base):
    """
    Stream MP3 Audio to an Icecast server
    """
    SAMPLES_PER_FRAME = AUDIO_SAMPLERATE // 4
    BUFFER_LEN = SAMPLES_PER_FRAME * 3

    def __init__(self, url, password) -> None:
        self._outputBuffer: collections.deque = collections.deque(maxlen=self.BUFFER_LEN)

        self._url = url
        self._password = password
        self._mp3Bitrate = 48000

        self._stopEvent: Optional[threading.Event] = None
        self._streamingThread: Optional[threading.Thread] = None

    def reconnect(self) -> None:
        """
        Initial connect or reconnect
        """
        self.close()

        # Launch Streaming thread
        self._stopEvent = threading.Event()
        self._streamingThread = threading.Thread(target=self._runIcecastStream, daemon=True, args=(self._stopEvent, ))
        self._streamingThread.start()

    def close(self) -> None:
        if self._stopEvent is not None:
            self._stopEvent.set()
            self._stopEvent = None

        if self._streamingThread is not None:
            self._streamingThread.join()
            self._streamingThread = None

    def _streamDataGen(self, stopEvt) -> Generator[bytes, None, None]:
        # MP3 encoder
        mp3Encoder = lameenc.Encoder()
        if mp3Encoder is None:
            print("ERROR: Failed initializing MP3 encoding for Icecast")
            return
        mp3Encoder.set_bit_rate(self._mp3Bitrate)
        mp3Encoder.set_in_sample_rate(AUDIO_SAMPLERATE)
        mp3Encoder.set_channels(1)
        mp3Encoder.set_quality(2)

        while not stopEvt.is_set():
            if len(self._outputBuffer) >= self.SAMPLES_PER_FRAME:
                samps: np.ndarray = np.ndarray([self.SAMPLES_PER_FRAME],  dtype=np.int16)
                for i in range(0, self.SAMPLES_PER_FRAME):
                    samps[i] = self._outputBuffer.popleft()

                mp3out = mp3Encoder.encode(samps.tobytes())
                if mp3out:
                    yield mp3out
            else:
                time.sleep(0.1)

    def _runIcecastStream(self, stopEvt) -> None:
        while not stopEvt.is_set():
            session = requests.Session()
            try:
                print(f"Connecting to Icecast Stream: {self._url}")

                # Dump outputBuffer 
                self._outputBuffer.clear()

                resp = session.put(
                    self._url,
                    data=self._streamDataGen(stopEvt),
                    auth=("source", self._password),
                    headers={"Content-Type": "audio/mpeg"},
                    stream=True,
                    timeout=10,
                )
            except Exception as e:
                print(f"Error streaming to Icecast: {e}")

            timeoutTime = time.time() + 30
            while time.time() < timeoutTime:
                if stopEvt.is_set():
                    return
                time.sleep(0.001)
        print("Exiting Icecast Thread")

    def send(self, samples: List[int]) -> None:
        self._outputBuffer.extend(samples)


class AudioServerOutput_Websocket(AudioServerOutput_Base):
    """
    Stream raw audio to websockets
    """
    SAMPLES_PER_FRAME = AUDIO_SAMPLERATE // 4
    BUFFER_LEN = SAMPLES_PER_FRAME * 3

    def __init__(self, host: str, port: int) -> None:
        self._outputBuffer: collections.deque = collections.deque(maxlen=self.BUFFER_LEN)

        self._host = host
        self._port = port

        self._socketClients = set()

        self._stopEvent: Optional[threading.Event] = None
        self._serverThread: Optional[threading.Thread] = None

    def reconnect(self) -> None:
        """
        Initial connect or reconnect
        """
        self.close()

        # Launch Streaming thread
        self._stopEvent = threading.Event()
        self._serverThread = threading.Thread(target=self._runServer, daemon=True, args=(self._stopEvent, ))
        self._serverThread.start()

    def close(self):
        if self._stopEvent is not None:
            self._stopEvent.set()
            self._stopEvent = None

        if self._serverThread is not None:
            self._serverThread.join()
            self._serverThread = None

    async def _wsStreamer(self, stopEvt) -> None:
        """
        Stream the audio samples
        """
        while not stopEvt.is_set():
            if len(self._outputBuffer) >= self.SAMPLES_PER_FRAME:
                samps: np.ndarray = np.ndarray([self.SAMPLES_PER_FRAME],  dtype=np.int16)
                for i in range(0, self.SAMPLES_PER_FRAME):
                    samps[i] = self._outputBuffer.popleft()
                dataBytes = samps.tobytes()

                deadClients = []
                for ws in list(self._socketClients):
                    try:
                        await ws.send(dataBytes)
                    except Exception as e:
                        print(f"send error: {e}")
                        deadClients.append(ws)

                for ws in deadClients:
                    self._socketClients.discard(ws)

            else:
                await asyncio.sleep(0.1)

    async def _wsHandler(self, websocket) -> None:
        """
        New connection handler
        """
        print(f"client connected: {getattr(websocket, 'remote_address', None)}")
        self._socketClients.add(websocket)

        try:
            async for _ in websocket:
                pass
        except Exception as e:
            print(f"client error: {e}")
        finally:
            self._socketClients.discard(websocket)
            print(" client disconnected")

    async def _wsServe(self, stopEvt):
        """
        Start the websocket and configure callbacks
        """
        print(f"Starting Websocket Server {self._host}:{self._port}")
        async with websockets.serve(self._wsHandler, self._host, self._port) as server:
            await self._wsStreamer(stopEvt)

    def _runServer(self, stopEvt):
        """
        async run the websocket
        """

        while not stopEvt.is_set():

            # Dump outputBuffer 
            self._outputBuffer.clear()

            try:
                asyncio.run(self._wsServe(stopEvt))
                break
            except OSError as e:
                print(f"bind failed: {e}")
                if stopEvt.is_set():
                    break
                print("retrying bind in 1 second...")
                time.sleep(1)
            except Exception as e:
                print("loop error:", e)
                break

            print("Websocket serve done")

            timeoutTime = time.time() + 30
            while time.time() < timeoutTime:
                if stopEvt.is_set():
                    return
                time.sleep(0.001)
        print("Exiting Websocket Thread")

    def send(self, samples: List[int]):
        self._outputBuffer.extend(samples)

