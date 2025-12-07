import collections
from multiprocessing import shared_memory, Process, Value
import numpy as np
import os
import pyaudio
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

from gnuradio import gr

from .const import AUDIO_SAMPLERATE
from .hpSharedMem import HighPerformanceCircularBuffer


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
        if configDict['type'].lower() == 'udp':
            return AudioServerOutput_UDP(configDict['serverIp'], configDict['serverPort'])
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
    Stand-alone process, receives audio streams from Receivers, mixes them down, 
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
        ):
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

    def stop(self):
        self._stopFlag = True

    def run(self):
        print("Audio Server Running")

        mixBuffers = []
        for i in range(0, self._numInputStreams):
            mixBuffers.append( collections.deque(maxlen=self.BUFFER_LEN) )

        for o in self._outputs:
            o.reconnect()

        try:
            os.nice(-5)
        except Exception as e:
            print("Couldn't nice ourself")
            print(e)

        ###
        # Mix Loop

        startTime = time.time()
        samplesMixed = 0
        while not self._stopFlag:
            # Read ShmBuffers
            for i in range(0, self._numInputStreams):
                inBuf = []
                numRead = self.inputStreamCircularBuffers[i].read(inBuf)
                # print(f"{len(mixBuffers[i])}  ", end='')
                if numRead:
                    mixBuffers[i].extend(inBuf)
            # print("")

            # Mix Audio
            curTime = time.time()
            samplesToMix = int((curTime - startTime) * AUDIO_SAMPLERATE) - samplesMixed
            newSamples = []
            for _ in range(0, samplesToMix):
                outSum = 0.0
                for i in range(0, self._numInputStreams):
                    buf = mixBuffers[i]
                    lenBuf = len(buf)
                    if lenBuf > 0:
                        outSum += buf.popleft()
                newSamples.append(outSum)
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
    ):
        audioServer = cls(numInputStreams, inputStreamShmBuffers, inputStreamHeadIdxs, inputStreamTailIdxs, outputConfigDicts)
        audioServer.run()


class AudioServerOutput_Base(object):
    def __init__(self):
        pass

    def reconnect(self):
        raise NotImplementedError()

    def close(self):
        raise NotImplementedError()
    
    def send(self, samples: List[float]):
        raise NotImplementedError()


class AudioServerOutput_Local(AudioServerOutput_Base):
    """
    Play audio locally with pyAudio / PortAudio
    """
    FRAMES_PER_BUFFER = 1000

    def __init__(self):
        self._outputBuffer = collections.deque(maxlen=self.FRAMES_PER_BUFFER * 4)

        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._pyaudioStream: Optional[pyaudio.Stream] = None

    def reconnect(self):
        """
        Initial connect or reconnect
        """

        self.close()

        # init the pyAudio stream
        self._pyaudio = pyaudio.PyAudio()

        # Open stream using callback (3)
        self._pyaudioStream = self._pyaudio.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=AUDIO_SAMPLERATE,
            output=True,
            stream_callback=self._pyAudioCb,
            frames_per_buffer=1000,
        )

        # Dump outputBuffer 
        self._outputBuffer.clear()

    def close(self):
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

    def _pyAudioCb(self, in_data, frame_count, time_info, status):

        # If len(data) is less than requested frame_count, PyAudio automatically
        # assumes the stream is finished, and the stream stops - provide 0s if empty buffer

        # status flags - paInputUnderflow, paInputOverflow, paOutputUnderflow, paOutputOverflow, paPrimingOutput

        byteArray = bytearray()
        try:
            for i in range(0, frame_count):
                samp = self._outputBuffer.popleft()
                byteArray.extend(struct.pack('<f', samp))
        except IndexError:
            byteArray.extend( bytearray((frame_count - i) * 4) )

        outputBufferLen = len(self._outputBuffer)
        if outputBufferLen > self.FRAMES_PER_BUFFER:
            # buffer is growing, discard samples to keep it in check
            if outputBufferLen > self.FRAMES_PER_BUFFER * 2:
                for _ in range(0, self.FRAMES_PER_BUFFER):
                    self._outputBuffer.popleft()
            else:
                self._outputBuffer.popleft()

        return (bytes(byteArray), pyaudio.paContinue)

    def send(self, samples: List[float]):
        self._outputBuffer.extend(samples)

        if self._pyaudioStream is None or not self._pyaudioStream.is_active():
            print("pyAudio Stream Inactive - Reconnecting")
            self.reconnect()


class AudioServerOutput_UDP(AudioServerOutput_Base):
    """
    Send raw audio to a UDP port.

    Currently only supports float samples

        nc -l -u 12345 | sox -t raw -r 16k -e floating-point -b 32 -c 1 - -t alsa
    """
    SAMPLES_PER_PACKET = 100
    BUFFER_LEN = 10000

    def __init__(self, serverIp, serverPort):
        self._outputBuffer = collections.deque(maxlen=self.BUFFER_LEN)

        self._socket: Optional[socket.socket] = None
        self._serverIp = serverIp
        self._serverPort = serverPort

    def reconnect(self):
        """
        Initial connect or reconnect
        """
        self.close()
        # init the socket
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Dump outputBuffer 
        self._outputBuffer.clear()

    def close(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception as e:
                print("Failed closing UDP socket")
                print(e)
            self._socket = None

    def send(self, samples: List[float]):
        self._outputBuffer.extend(samples)
        while len(self._outputBuffer) > self.SAMPLES_PER_PACKET:
            byteArray = bytearray()
            for _ in range(0, self.SAMPLES_PER_PACKET):
                samp = self._outputBuffer.popleft()
                byteArray.extend(struct.pack('<f', samp))

            try:
                if self._socket is None:
                    self.reconnect()
                    self._outputBuffer.clear()
                    return
                self._socket.sendto(byteArray, (self._serverIp, self._serverPort))
            except Exception as e:
                print("Failed Sending to UDP - reconnect")
                print(e)
                self.reconnect()

