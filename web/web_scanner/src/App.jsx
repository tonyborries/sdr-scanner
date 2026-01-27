import { useEffect, useState, useReducer } from 'react'
import useWebSocket, { ReadyState } from "react-use-websocket"

import scannerConfigReducer from './scannerConfigReducer.js'
import {ChannelConfigList, ActiveChannelList} from './Channel.jsx'

import './App.css'
import 'bootstrap/dist/css/bootstrap.min.css';


function App() {

  /////////////////////
  // Control Websocket

  // const controlWebsocketUrl = process.env.REACT_APP_CONTROL_WS_URL;
  const controlWebsocketUrl = import.meta.env.VITE_CONTROL_WS_URL;

  // const [socketUrl, setSocketUrl] = useState('ws://0.0.0.0:8080/control_ws');
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
      <h1>SDR WebScanner</h1>
      <span>The Control WebSocket is currently <b>{connectionStatus}</b></span>

      <div className="container">
        <div className="row">
          <div className="col-md-6">
            <ActiveChannelList scannerConfigData={scannerConfigData}/>
          </div>
          <div className="col-md-6">
            <ChannelConfigList scannerConfigData={scannerConfigData}/>
          </div>
        </div>
      </div>

    </>
  )
}

export default App
