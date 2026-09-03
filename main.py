#!/usr/bin/env python3
import sys
import os

# Wayland 環境での無害な requestActivate 警告ログを抑制
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.wayland=false")

from PyQt5.QtWidgets import QApplication
from gui_window import GUIPipeWireWindow

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GUIPipeWire")
    app.setOrganizationName("GUIPipeWire")

    window = GUIPipeWireWindow()
    window.show()

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
