import argparse

from sdr_scanner.Scanner import Scanner
from sdr_scanner.wxScanner import run as wxScannerRun


def main():

    parser = argparse.ArgumentParser(prog='wxSDRScanner')

    parser.add_argument('-c', '--config', default='sdrscan.yaml')

    args = parser.parse_args()


    ###
    # Setup Scanner

    scanner = Scanner.fromConfigFile(args.config)

    ###
    # Load GUI

    wxScannerRun(scanner)


if __name__ == '__main__':
    main()
