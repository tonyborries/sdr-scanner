import { useEffect, useMemo, useReducer } from 'react'
import useWebSocket, { ReadyState } from "react-use-websocket"

import scannerConfigReducer from './scannerConfigReducer.js'
import WebSocketAudioPlayer from './AudioPlayer.jsx'
import {ChannelConfigList, ActiveChannelList} from './Channel.jsx'

import './App.css'
import styles from './App.module.css';


function App() {

  /////////////////////
  // Control Websocket

  // const controlWebsocketUrl = process.env.REACT_APP_CONTROL_WS_URL;
  const controlWebsocketUrl = useMemo(() => {
    if (import.meta.env.VITE_CONTROL_WS_URL.includes('<HOSTNAME>')) {
      return import.meta.env.VITE_CONTROL_WS_URL.replace('<HOSTNAME>', window.location.hostname)
    }
    return import.meta.env.VITE_CONTROL_WS_URL;
  }, []);

  const { sendJsonMessage, lastJsonMessage, readyState } = useWebSocket(controlWebsocketUrl);

  useEffect(() => {
    if (lastJsonMessage !== null) {
      console.log(`Received: ${JSON.stringify(lastJsonMessage)}`);
    }
  }, [lastJsonMessage]);

  
  const connectionStatus = {
    [ReadyState.CONNECTING]: 'Connecting',
    [ReadyState.OPEN]: 'Open',
    [ReadyState.CLOSING]: 'Closing',
    [ReadyState.CLOSED]: 'Closed',
    [ReadyState.UNINSTANTIATED]: 'Uninstantiated',
  }[readyState];

  // Control Websocket
  /////////////////////

  ///////////////
  // Config Data

  const initialConfigData = {};
  const [scannerConfigData, scannerConfigDispatch] = useReducer(scannerConfigReducer, initialConfigData);

  useEffect(() => {
    if (lastJsonMessage !== null) {
      scannerConfigDispatch(lastJsonMessage);
    }
  }, [lastJsonMessage]);

  // Config Data
  ///////////////


  return (
    <>
      <h1>Web SDRScanner</h1>
      <span>The Control WebSocket is currently <b>{connectionStatus}</b></span>

      <div className={styles.channelsContainer}>
        <div className={styles.activeChannelList}>
          <WebSocketAudioPlayer />
          <ActiveChannelList scannerConfigData={scannerConfigData} controlWsSendJsonMessage={sendJsonMessage} />
        </div>
        <div className={styles.channelsConfigList}>
          <ChannelConfigList scannerConfigData={scannerConfigData} controlWsSendJsonMessage={sendJsonMessage} />
        </div>
      </div>

    </>
  )
}

export default App
