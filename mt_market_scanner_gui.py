import sys
import zipfile
import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QPushButton,
    QVBoxLayout, QWidget, QFileDialog, QTextEdit, QMessageBox
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt
import cv2
import numpy as np
import pytesseract

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Metin2 SHOP SCANNER v1.1.1")
        self.setGeometry(200, 200, 800, 600)

        layout = QVBoxLayout()

        self.label = QLabel("Podgląd OCR (kolor)")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

        self.text_output = QTextEdit()
        self.text_output.setReadOnly(True)
        layout.addWidget(self.text_output)

        self.scan_button = QPushButton("Skanuj ekran (kolor)")
        self.scan_button.clicked.connect(self.scan_screen)
        layout.addWidget(self.scan_button)

        self.update_button = QPushButton("Wgraj ZIP i zaktualizuj (1 klik)")
        self.update_button.clicked.connect(self.update_from_zip)
        layout.addWidget(self.update_button)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def scan_screen(self):
        # Zrzut ekranu (kolorowy)
        import pyautogui
        screenshot = pyautogui.screenshot()
        img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

        # OCR w kolorze
        text = pytesseract.image_to_string(img, lang="eng")
        self.text_output.setPlainText(text)

        # Wyświetl podgląd w GUI
        height, width, channel = img.shape
        bytes_per_line = 3 * width
        qimg = QPixmap.fromImage(
            QPixmap.fromImage(QPixmap(
                cv2.imencode('.png', img)[1].tobytes()
            ))
        )
        self.label.setPixmap(QPixmap(qimg))

    def update_from_zip(self):
        zip_path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik ZIP z aktualizacją", "", "ZIP Files (*.zip)")
        if not zip_path:
            return

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(".")
            QMessageBox.information(self, "Aktualizacja", "Pliki zostały zaktualizowane! Uruchom ponownie aplikację.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zainstalować aktualizacji: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
