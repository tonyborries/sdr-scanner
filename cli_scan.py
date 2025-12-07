import argparse
import datetime
import queue
import signal
import sys
import time

from sdr_scanner.Scanner import Scanner


def main():

    parser = argparse.ArgumentParser(prog='wxSDRScanner')

    parser.add_argument('-c', '--config', default='sdrscan.yaml')

    args = parser.parse_args()

    ###
    # Setup Scanner

    scannerToUiQueue = queue.Queue()
    #uiToScannerQueue = queue.Queue()

    scanner = Scanner.fromConfigFile(args.config)


    ###
    # Setup Status Callbacks

    lastChannelStatusById = {}

    def _processScannerDataCb():
        try:
            while True:
                data = scannerToUiQueue.get(False)
                if data['type'] == "ChannelStatus":
                    channelStatusCb(data["data"])
                elif data['type'] == "ScanWindowDone":
                    scanWindowDoneCb(data['data']['id'])

                scannerToUiQueue.task_done()
        except queue.Empty:
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

    #scanner.addInputQueue(uiToScannerQueue)
    scanner.addOutputQueue(scannerToUiQueue)
    scanner.addProcessQueueCallback(_processScannerDataCb)

    
    def sig_handler(sig=None, frame=None):
        scanner.stop()
        
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    ###
    # Run

    scanner.runReceiverProcesses()


if __name__ == '__main__':
    main()

