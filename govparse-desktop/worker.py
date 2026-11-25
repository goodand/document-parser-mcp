"""Entry point started by GovParse Desktop.exe (runtime/python.exe -I -X utf8 worker.py ...).

modes: preview <src> <png> <page> | json|html|hwpx|hwp <src> <outdir> |
       convert <document.json> <outdir> <html|hwpx> | ocr <png> | serve
"""
import sys

from gpdesk.desktop_worker import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
