import argparse

from sdr_scanner.Scanner import Scanner
from sdr_scanner.wxScanner import run as wxScannerRun


def main():

    parser = argparse.ArgumentParser(prog='wxSDRScanner')

    parser.add_argument('-c', '--config', default='sdrscan.yaml')
    parser.add_argument('--controlWsHost', default=None, help="If controlWsHost and controlWsPort are specified, enable the Control Websocket")
    parser.add_argument('--controlWsPort', default=None, type=int, help="If controlWsHost and controlWsPort are specified, enable the Control Websocket")

    args = parser.parse_args()


    ###
    # Setup Scanner

    scanner = Scanner.fromConfigFile(args.config, args.controlWsHost, args.controlWsPort)

    ###
    # Load GUI

    wxScannerRun(scanner)


if __name__ == '__main__':
    main()
