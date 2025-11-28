import argparse
import datetime

from sdr_scanner.Scanner import Scanner


def main():

    parser = argparse.ArgumentParser(prog='wxSDRScanner')

    parser.add_argument('-c', '--config', default='sdrscan.yaml')

    args = parser.parse_args()


    ###
    # Setup Scanner

    scanner = Scanner.fromConfigFile(args.config)


    ###
    # Setup Status Callbacks

    lastChannelStatusById = {}

    def scanWindowStartCb(scanWindowId, rxId):
#        print(f"Scan Window {scanWindowId} on {rxId}")
        pass

    def scanWindowDoneCb(scanWindowId):
#        print(f"Scan Window Done: {scanWindowId}")
        print('.', end='', flush=True)
        if scanWindowId == scanner.scanWindowConfigs[0].id:
            print('', end='\r')

    def channelStatusCb(data):
        status = data['status']
        channelId = data['id']
        lastStatus = lastChannelStatusById.get(channelId)
        if status == 1 and lastStatus != status:
            channel = scanner.getChannelById(channelId)
            print(f"\n {datetime.datetime.now().time().isoformat()[0:8]}  {channel.label} ({channel.freq_hz/1e6})")
        lastChannelStatusById[channelId] = status

    scanner.addChannelStatusCb(channelStatusCb)
    scanner.addScanWindowStartCb(scanWindowStartCb)
    scanner.addScanWindowDoneCb(scanWindowDoneCb)

    ###
    # Run

    scanner.runReceiverProcesses()


if __name__ == '__main__':
    main()

