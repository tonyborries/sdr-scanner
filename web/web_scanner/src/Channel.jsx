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


function RSSIBars({ rssi_dBFS, squelchThreshold_dBFS }) {

  const numRssiBars = 4;
  const rssiOverThreshold_dB = rssi_dBFS - squelchThreshold_dBFS;

  const getActiveBarCount = (rssiOverThreshold_dB) => {
    let numBars = Math.floor(rssiOverThreshold_dB / 10) + 1;
    if (numBars < 0) {numBars = 0}
    if (numBars > numRssiBars) {numBars = numRssiBars}
    return numBars;
  };

  const filledBars = getActiveBarCount(rssiOverThreshold_dB);

  const rssiStyles = {
    container: {
      display: 'flex',
      alignItems: 'flex-end',
      width: '50px',
      height: '35px',
      gap: '3px',
    },
    bar: {
      flex: 1,
      borderRadius: '2px',
      border: '2px solid black',
    },
  };

  // const getColor = (count) => {
  //   if (count >= 3) return '#2ecc71';
  //   if (count >= 2) return '#f1c40f';
  //   return '#e74c3c';
  // };
  // const barColor = getColor(filledBars);
  const barColor = '#000000';

  return (
    <>
      <div style={{...rssiStyles.container, width: '150px'}} >
        <div style={rssiStyles.container} >
          {[...Array(numRssiBars)].map((_, index) => (
            <div
              key={index}
              style={{
                ...rssiStyles.bar,
                height: `${((index + 1) * 100) / numRssiBars}%`, // Stairs effect
                backgroundColor: index < filledBars ? barColor : '#ccc',
              }}
            />
          ))}
        </div>
        <span>{rssi_dBFS != null ? rssi_dBFS.toFixed(0) : null} dBFS</span>
      </div>
    </>
  );
};


function VolumeBar({ channelConfig }) {

  const minVol = -50;
  const maxVol = 0;

  const volumeStyles = {
    container: {
      display: 'flex',
      alignItems: 'flex-end',
      width: '150px',
      height: '15px',
      gap: '3px',
      border: '3px solid black',
      borderRadius: '2px',
      overflow: 'hidden', /* Ensures the inner bar stays within the rounded corners */
    },
    bar: {
      
      height: '100%',
      boxSizing: 'border-box', /* Ensures padding/border doesn't add to the width/height calculation */      
    },
  };

  const getColor = (volume_dBFS) => {
    if (volume_dBFS >= -3) return '#e74c3c';
    if (volume_dBFS >= -6) return '#f1c40f';
    // return '#2ecc71';
    return '#000000';
  };

  const getVolumePct = (volume_dBFS) => {
    if (volume_dBFS > maxVol) {return 100;}
    if (volume_dBFS < minVol) {return 0;}
    return Math.floor(((volume_dBFS - minVol) / (maxVol - minVol)) * 100);
  }

  const [volume_dBFS, setVolume_dBFS] = useState(-150);

  useEffect(() => {
    const vol = channelConfig.statusData ? channelConfig.statusData.volume : null;
    if (vol == null) {
      if (volume_dBFS != -150) {
        setVolume_dBFS(-150);
      }
    } else {
      setVolume_dBFS(vol);
    }
  }, [channelConfig.statusData]);

  return (
    <>
      <div style={volumeStyles.container} >
        <div
          style={{
            ...volumeStyles.bar,
            width: `${getVolumePct(volume_dBFS)}%`,
            backgroundColor: `${getColor(volume_dBFS)}`,
          }}
        />
      </div>
    </>
  );
};

function ActiveChannelConfigDisplay({channelConfig}) {

  return (
    <>
      <div className={styles.statusContainer}>
        <div className={`${styles.statusBox} ${channelConfig.hold == true ? styles.holdActive : ""}`}>
          <p className={`${styles.statusBox} ${channelConfig.hold == true ? styles.holdActive : ""}`}>H</p>
        </div>
        <div className={`${styles.statusBox} ${channelConfig.solo == true ? styles.soloActive : ""}`}>
          <p className={`${styles.statusBox} ${channelConfig.solo == true ? styles.soloActive : ""}`}>S</p>
        </div>
        <div className={`${styles.statusBox} ${channelConfig.mute == true ? styles.muteActive : ""}`}>
          <p className={`${styles.statusBox} ${channelConfig.mute == true ? styles.muteActive : ""}`}>M</p>
        </div>
        <div className={`${styles.statusBox} ${channelConfig.enabled != true ? styles.disableActive : ""}`}>
          <p className={`${styles.statusBox} ${channelConfig.enabled != true ? styles.disableActive : ""}`}>D</p>
        </div>

      </div>
    </>
  );
};


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
        <div className={styles.topLineContainer} >
          <div className={styles.channelLabelContainer} >
            <span className={styles.channelLabel}>{channelConfig.label}</span>
            <br />
            <span>{(channelConfig.freq_hz / 1_000_000).toFixed(3)}</span>
            <br />
            <ActiveChannelConfigDisplay channelConfig={channelConfig} />
          </div>
          <br />
          <div className={styles.channelRssiContainer} >
            <RSSIBars rssi_dBFS={channelConfig.statusData ? channelConfig.statusData.rssi : null} squelchThreshold_dBFS={channelConfig.squelchThreshold} />
            <span>Noise: {(channelConfig.statusData && channelConfig.statusData.noiseFloor) ? channelConfig.statusData.noiseFloor.toFixed(0) : null}</span>
            <br />
            <VolumeBar channelConfig={channelConfig} dBFS/>
          </div>
        </div>

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
