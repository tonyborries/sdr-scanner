import { useEffect, useState, useRef } from 'react'
import styles from './Channel.module.css';

const getStatusString = (status) =>{

    switch (status) {
      case 0:
        return 'IDLE';
      case 1:
        return 'ACTIVE';
      case 2:
        return 'DWELL';
      case 3:
        return 'HOLD';
      case 4:
        return 'FORCE ACTIVE';
      default:
        return 'UNKNOWN';
    }
  };

const getChannelBackgroundColor = (currentStatus) => {
  switch (currentStatus) {
    case 0:
      return 'white';
    case 1:
      return '#70F070';
    case 2:
    case 3:
      return '#FCF55F';
    case 4:
      return '#E08080';
    default:
      return 'white'; // Default color
  }
};


///////////////////////////////////////////////////////////////////////
//                                                                   //
//                        Channel Config List                        //

function ChannelConfig({channelConfig}) {

  return (
    <>
    <tr 
      style={{
        backgroundColor: getChannelBackgroundColor(channelConfig.statusData ? channelConfig.statusData.status : 0),
      }}
    >
      <td>{channelConfig.label}</td>
      <td>{(channelConfig.freq_hz / 1_000_000).toFixed(3)}</td>
      <td>{getStatusString(channelConfig.statusData ? channelConfig.statusData.status : 0)}</td>
      <td>{JSON.stringify(channelConfig.mute)}</td>
    </tr>
    
    </>
  )
}


export function ChannelConfigList({scannerConfigData}) {

  return (
    <>
      <h2>Channel Configs</h2>

      <table>
        <thead>
          <tr>
            <th>Label</th>
            <th>Freq</th>
            <th>Status</th>
            <th>Mute</th>
          </tr>
        </thead>
        <tbody>
          {scannerConfigData && scannerConfigData.channelConfigs && scannerConfigData.channelConfigs.map(channelConfig => (
            <ChannelConfig key={channelConfig.id} channelConfig={channelConfig} />
          ))}
        </tbody>
      </table>
    </>
  )
}



//                        Channel Config List                        //
//                                                                   //
///////////////////////////////////////////////////////////////////////


///////////////////////////////////////////////////////////////////
//                                                               //
//                        Active Channels                        //


function ActiveChannel({channelConfig}) {

  ///////////////////
  // Display Timeout

  const timeoutMillis = 15_000;
  const [isVisible, setIsVisible] = useState(true);  // TODO: If we start out as false, the timer breaks
  const lastActive = useRef(0);

  // Interval to check if we need to timeout the display

  useEffect(() => {
    const intervalId = setInterval(() => {
      if (isVisible) {
        if ((Date.now() - lastActive.current) > timeoutMillis) {
          setIsVisible(false);
        }
      }
    }, 2000);

    return () => {
      clearInterval(intervalId);
    };
  }, []);

  // Display Timeout
  ///////////////////


  if (channelConfig && channelConfig.statusData && channelConfig.statusData.status) {
    lastActive.current = Date.now();
    if (! isVisible) {
      setIsVisible(true);
    }
  }

  if (! isVisible) {
    return null;
  }

  return (
    <>
      <div className={styles.activeChannel}
        style={{
          backgroundColor: getChannelBackgroundColor(channelConfig.statusData ? channelConfig.statusData.status : 0),
          // padding: '20px',
          // color: 'white', // Ensure text is visible
          // borderRadius: '5px',
          // textAlign: 'center',
        }}
      >
        <span className={styles.channelLabel}>{channelConfig.label}</span>
        <br />
        <span>{(channelConfig.freq_hz / 1_000_000).toFixed(3)}</span>
      </div>
    </>
  )
}

export function ActiveChannelList({scannerConfigData}) {

  return (
    <>
      <h2>Active Channels</h2>

      {scannerConfigData && scannerConfigData.channelConfigs && scannerConfigData.channelConfigs.map(channelConfig => (
        <ActiveChannel key={channelConfig.id} channelConfig={channelConfig} />
      ))}
    </>
  )
}



//                        Active Channels                        //
//                                                               //
///////////////////////////////////////////////////////////////////
