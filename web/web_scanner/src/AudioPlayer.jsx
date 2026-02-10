import { useEffect, useState, useRef } from 'react'
import styles from './AudioPlayer.module.css';


export default function WebSocketAudioPlayer() {
    // const wsAudioUrl = "ws://0.0.0.0:8123/";
    const wsAudioUrl = import.meta.env.VITE_AUDIO_WS_URL;

    const [audioCtx, setAudioCtx] = useState(null);
    const [gainNode, setGainNode] = useState(null);

    const [playerStarted, setPlayerStarted] = useState(false);
    const [playerStatus, setPlayerStatus] = useState('Disconnected');
    const [volume, setVolume] = useState(1.0);

    ////////////////////
    //  Audio WebSocket 

    const [socket, setSocket] = useState(null);

    const AUDIO_BUFFER_MAX_SIZE = 16000;


    const handleStartButton = (event) => {

        console.log("Start Audio");

        /////////////////
        //  Audio Buffer

        let audioBuffer = [];

        const handleAudioData = (event) => {
            const arrayBuffer = event.data;
            const intBuffer = new Int16Array(arrayBuffer);  // 16-bit PCM audio

            // Convert to float
            for (let i = 0; i < intBuffer.length; i++) {
                if (audioBuffer.length < AUDIO_BUFFER_MAX_SIZE) {
                    audioBuffer.push(intBuffer[i] / 32767.0);
                }
            }
        };

        //  Audio Buffer
        /////////////////

        ////////////////////
        //  Audio WebSocket 

        const newSocket = new WebSocket(wsAudioUrl);
        newSocket.binaryType = "arraybuffer";

        newSocket.onopen = () => {
            setPlayerStatus('connected');
            console.log('Connected to socket');
        };

        newSocket.onclose = () => {
            setPlayerStatus('disconnected');
            console.log('Disconnected from socket');
            setPlayerStarted(false);
        };

        newSocket.onerror = (e) => {
            setPlayerStatus('Error');
            console.error(e);
            setPlayerStarted(false);
        };

        newSocket.onmessage = handleAudioData;
        setSocket(newSocket);

        //  Audio WebSocket 
        ////////////////////

        //////////////////
        //  Audio Context

        let newAudioCtx = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: 16000
        });
        setAudioCtx(newAudioCtx);

        // Gain node for volume control
        let newGainNode = newAudioCtx.createGain();
        setGainNode(newGainNode);
        newGainNode.gain.value = parseFloat(1.0);
        newGainNode.connect(newAudioCtx.destination);

        // Connect Audio Buffers

        const audioBufferSource = newAudioCtx.createBufferSource();
        const AUDIO_CTX_BUFFER_SIZE = 4096;

        const scriptNode = newAudioCtx.createScriptProcessor(AUDIO_CTX_BUFFER_SIZE, 0, 1);

        // Give the node a function to process audio events
        scriptNode.addEventListener("audioprocess", (audioProcessingEvent) => {
            
            let outputBuffer = audioProcessingEvent.outputBuffer;
            let outputData = outputBuffer.getChannelData(0);

            let i = 0;
            let inCount = Math.min(audioBuffer.length, AUDIO_CTX_BUFFER_SIZE)
            for (; i < inCount; i++) {
                // make output equal to the same as the input
                outputData[i] = audioBuffer[i];
            }
            audioBuffer = audioBuffer.slice(i);

            // Fill missing samples with silence
            for (; i < AUDIO_BUFFER_MAX_SIZE; i++) {
                outputData[i] = 0.0;
            }
        });

        audioBufferSource.connect(scriptNode);
        scriptNode.connect(newGainNode);
        audioBufferSource.start();

        // When the buffer source stops playing, disconnect everything
        // audioBufferSource.addEventListener("ended", () => {
        //     console.log("Script Ended")
        //     audioBufferSource.disconnect(scriptNode);
        //     scriptNode.disconnect(audioCtx.destination);
        // });


        //  Audio Context
        //////////////////

        setPlayerStarted(true);

        event.stopPropagation();
    };


    const handleVolumeChange = (event) => {
        let newGain = parseFloat(event.target.value);
        console.log("New Volume" + newGain);
        setVolume(newGain);

        if (gainNode) {
            gainNode.gain.value = newGain;
        }
    }


    if (playerStarted == false) {
        return (
            <>
                <h2>Audio Player</h2>
                <p id="status">{playerStatus}</p>

                <button onClick={handleStartButton}>Start Audio</button>
            </>
        )
    } else {
        return (
            <>
                <h2>Audio Player</h2>

                <p id="status">{playerStatus}</p>

                <div className={styles.volumeControl}>
                    <label>Volume:</label>
                    <input onChange={handleVolumeChange} type="range" id="volume" min="0" max="1" step="0.01" value={volume} />
                </div>

            </>
        )
    }
}

