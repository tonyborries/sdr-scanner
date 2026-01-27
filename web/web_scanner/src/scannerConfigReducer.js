export default function scannerConfigReducer(prevScannerConfigData, action) {
    // Take API messages from the Control Websocket and update the configData


    switch (action.type) {
        case 'config':
            {
                let newScannerConfigData = {
                    'channelConfigs': []
                };
                for (const scanWindowConfig of action.data.scanWindows) {
                    for (const channelConfig of scanWindowConfig.channels) {
                        newScannerConfigData.channelConfigs.push(channelConfig);
                    }
                }
                return newScannerConfigData;
            }
        case 'ChannelStatus':
            {
                let newScannerConfigData = {...prevScannerConfigData};
                if (newScannerConfigData) {
                    newScannerConfigData.channelConfigs = newScannerConfigData.channelConfigs.map(channelConfig => {
                        if (channelConfig.id === action.data.id) {
                            return { ...channelConfig, statusData: action.data };
                        } else {
                            return channelConfig;
                        }
                    });
                }
                return newScannerConfigData;
            }
        case 'ChannelConfig':
            {
                let newScannerConfigData = {...prevScannerConfigData};
                if (newScannerConfigData) {
                    newScannerConfigData.channelConfigs = newScannerConfigData.channelConfigs.map(channelConfig => {
                        if (channelConfig.id === action.data.id) {
                            return { ...action.data, statusData: channelConfig.statusData };
                        } else {
                            return channelConfig;
                        }
                    });
                }
                return newScannerConfigData;
            }
        default:
            return prevScannerConfigData;
    }

    
}
