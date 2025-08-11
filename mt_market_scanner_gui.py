
import sys, os, re, json, csv, hashlib, time, threading
from datetime import datetime
from collections import deque
import numpy as np, cv2, pytesseract, mss, pyautogui, requests
from PyQt5 import QtCore, QtGui, QtWidgets

from roi_calibrator import select_roi_size, save_size_to_config
from updater import check_and_update, CURRENT_VERSION
from github_tools import create_or_get_release, upload_asset, update_version_json, GhError

APP_NAME = "MT Market Scanner v1.0.2"
CFG_PATH = "config.json"

def load_config():
    if not os.path.exists(CFG_PATH): return {}
    with open(CFG_PATH,"r",encoding="utf-8") as f: return json.load(f)
def save_config(cfg):
    with open(CFG_PATH,"w",encoding="utf-8") as f: json.dump(cfg,f,ensure_ascii=False,indent=2)

# --- OCR utils (as before) ---
def ensure_dir(p): os.makedirs(p, exist_ok=True); return p
def now_ts(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def text_cleanup(s): import re; s=s.replace("—","-"); s=re.sub(r"[ \t]+"," ",s); return s.strip()
def preprocess_for_ocr(img_bgr):
    gray=cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray=cv2.equalizeHist(gray)
    gray=cv2.resize(gray, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
    gray=cv2.bilateralFilter(gray,7,60,60)
    th=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,35,10)
    return th
def parse_fields(txt):
    import re
    lines=[l.strip() for l in txt.splitlines() if l.strip()]
    if not lines: return None,None,None
    name=None
    for l in lines[:4]:
        if not re.search(r"(Od Poziomu|Cena|Yang|kup przedmiot|kup wszystkie podobne)", l, re.I): name=l; break
    if name is None: name=lines[0]
    price=unit=None
    for l in lines:
        m=re.search(r"cena[: ]+([\d\s\.]+)\s*yang", l, re.I)
        if m:
            d=re.sub(r"[^\d]","",m.group(1)); 
            if d: price=int(d)
        m2=re.search(r"(za\s*sztuk[eę]|szt\.)[: ]+([\d\s\.]+)\s*yang", l, re.I)
        if m2:
            d=re.sub(r"[^\d]","",m2.group(2)); 
            if d: unit=int(d)
    return name,price,unit
def classify_category(name):
    import re
    n=name.lower()
    if re.search(r"ksi[eę]ga\s+umiej[eę]tno[sś]ci", n, re.I): return "book"
    if re.search(r"pergamin", n, re.I): return "scroll"
    if re.search(r"kamie[nń]\s*duszy.*\+4", n, re.I): return "soulstone+4"
    if re.search(r"ulepszacz|ulepszacze", n, re.I): return "upgrade"
    return "other"

class OcrWorker(QtCore.QThread):
    frameReady = QtCore.pyqtSignal(np.ndarray)
    itemLogged = QtCore.pyqtSignal(dict)
    lastText = QtCore.pyqtSignal(str)
    status = QtCore.pyqtSignal(bool)
    def __init__(self, cfg):
        super().__init__(); self.cfg=cfg; self.running=False; self.stop_flag=False
        self.recent=deque(maxlen=1000)
        self.log_dir=os.path.abspath(self.cfg.get("log_dir","./logs_market")); ensure_dir(self.log_dir)
        self.csv_path=os.path.join(self.log_dir,"market_log.csv")
        pytesseract.pytesseract.tesseract_cmd=self.cfg.get("tesseract_path", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if not os.path.exists(self.csv_path):
            with open(self.csv_path,"w",newline="",encoding="utf-8") as f:
                csv.writer(f).writerow(["timestamp","item_name","price","unit_price","category","raw_text"])
    def hash_entry(self,name,price): return hashlib.md5(f"{name}|{price}".encode("utf-8")).hexdigest()
    def should_log(self,h):
        t=time.time(); dedup=float(self.cfg.get("dedup_seconds",2.0))
        while self.recent and (t-self.recent[0][1])>dedup: self.recent.popleft()
        for hh,ts in self.recent:
            if hh==h: return False
        self.recent.append((h,t)); return True
    def set_running(self,b): self.running=b; self.status.emit(b)
    def run(self):
        fps=float(self.cfg.get("fps",4.0)); min_dt=1.0/max(0.5,fps)
        with mss.mss() as sct:
            while not self.stop_flag:
                if not self.running: time.sleep(0.05); continue
                try:
                    pos=pyautogui.position(); x,y=pos.x,pos.y
                    roi_w=int(self.cfg.get("roi_width",520)); roi_h=int(self.cfg.get("roi_height",300))
                    mx=int(self.cfg.get("margin_x",30)); my=int(self.cfg.get("margin_y",30))
                    mon=sct.monitors[0]; left=max(0,x-mx); top=max(0,y-my)
                    if left+roi_w>mon["width"]: left=mon["width"]-roi_w
                    if top+roi_h>mon["height"]: top=mon["height"]-roi_h
                    bbox={"top":int(top),"left":int(left),"width":roi_w,"height":roi_h}
                    img=np.array(sct.grab(bbox))[:,:,:3]
                    th=preprocess_for_ocr(img); self.frameReady.emit(th)
                    lang=self.cfg.get("language","pol+eng")
                    txt=pytesseract.image_to_string(th, config=f'--oem 3 --psm 6 -l {lang}'); txt=text_cleanup(txt)
                    self.lastText.emit(txt)
                    if not txt or len(txt)<4: 
                        dt=time.time()-t0 if (t0:=time.time()) else 0
                        if dt<min_dt: time.sleep(min_dt-dt); 
                        continue
                    name,price,unit=parse_fields(txt)
                    if name and price:
                        import re
                        passed = any(k.strip().lower() in name.lower() for k in self.cfg.get("keywords",[])) \
                                 or re.search(r"ksi[eę]ga\s+umiej[eę]tno[sś]ci", name, re.I) \
                                 or re.search(r"pergamin", name, re.I) \
                                 or re.search(r"kamie[nń]\s*duszy.*\+4", name, re.I)
                        if not passed:
                            dt=time.time()-t0; 
                            if dt<min_dt: time.sleep(min_dt-dt); 
                            continue
                        h=self.hash_entry(name,price)
                        if self.should_log(h):
                            cat=classify_category(name)
                            with open(self.csv_path,"a",newline="",encoding="utf-8") as f:
                                csv.writer(f).writerow([now_ts(),name,price,unit if unit else "",cat,txt])
                            self.itemLogged.emit({"timestamp":now_ts(),"name":name,"price":price,"unit_price":unit,"category":cat})
                    dt=time.time()-t0; 
                    if dt<min_dt: time.sleep(min_dt-dt)
                except Exception:
                    time.sleep(0.1)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME); self.setMinimumSize(1000,680)
        self.cfg=load_config()
        if self.cfg.get("auto_update_on_start", True): self.try_update()
        self.worker=OcrWorker(self.cfg)
        self.worker.frameReady.connect(self.on_frame_ready)
        self.worker.itemLogged.connect(self.on_item_logged)
        self.worker.lastText.connect(self.on_last_text)
        self.worker.status.connect(self.on_status)
        self.count_total=0; self.counts={"book":0,"scroll":0,"soulstone+4":0,"upgrade":0,"other":0}
        self.tabs=QtWidgets.QTabWidget(); self.setCentralWidget(self.tabs)
        self.tab_view=QtWidgets.QWidget(); self.tab_settings=QtWidgets.QWidget()
        self.tabs.addTab(self.tab_view,"Podgląd"); self.tabs.addTab(self.tab_settings,"Ustawienia")
        self.build_view_tab(); self.build_settings_tab(); self.update_status_ui()
    def try_update(self):
        url=self.cfg.get("update_version_url"); 
        if not url: return
        info=check_and_update(url, os.path.dirname(os.path.abspath(sys.argv[0])))
        if info.get("updated"):
            QtWidgets.QMessageBox.information(self,"Aktualizacja",info.get("message",""))
            QtCore.QTimer.singleShot(150, lambda: (self.close(), os.execv(sys.executable,[sys.executable]+sys.argv)))
    def build_view_tab(self):
        layout=QtWidgets.QHBoxLayout(self.tab_view)
        left=QtWidgets.QVBoxLayout()
        self.preview_label=QtWidgets.QLabel("Podgląd OCR"); self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setStyleSheet("background-color:#111; color:#ddd; border:1px solid #444;"); self.preview_label.setMinimumSize(540,320)
        left.addWidget(self.preview_label,1)
        self.last_text=QtWidgets.QPlainTextEdit(); self.last_text.setReadOnly(True); self.last_text.setMaximumHeight(110)
        self.last_text.setStyleSheet("background-color:#1b1b1b; color:#c8c8c8; border:1px solid #333;")
        left.addWidget(QtWidgets.QLabel("Ostatni tekst OCR:")); left.addWidget(self.last_text)
        layout.addLayout(left,2)
        right=QtWidgets.QVBoxLayout()
        self.status_label=QtWidgets.QLabel("STATUS: STOP"); self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setStyleSheet("font-weight:bold; padding:8px; border-radius:6px; background:#5a1d1d; color:#ffd27f;")
        right.addWidget(self.status_label)
        self.counter_label=QtWidgets.QLabel("Zalogowano (sesja): 0"); self.counter_label.setStyleSheet("color:#ffd27f;"); right.addWidget(self.counter_label)
        self.cat_label=QtWidgets.QLabel("Księgi: 0 | Ulepszacze: 0 | Kamienie+4: 0 | Inne: 0"); self.cat_label.setStyleSheet("color:#ffd27f;"); right.addWidget(self.cat_label)
        self.last_read_label=QtWidgets.QLabel("Ostatni odczyt: —"); self.last_read_label.setStyleSheet("color:#e6e6e6;"); right.addWidget(self.last_read_label)
        right.addWidget(QtWidgets.QLabel("Ostatnie logi:"))
        self.logs_list=QtWidgets.QListWidget(); self.logs_list.setStyleSheet("background-color:#1b1b1b; color:#c8c8c8; border:1px solid #333;")
        right.addWidget(self.logs_list,1)
        btns=QtWidgets.QHBoxLayout()
        self.btn_start=QtWidgets.QPushButton("Start"); self.btn_start.clicked.connect(self.toggle_run)
        self.btn_roi=QtWidgets.QPushButton("Ustaw obszar skanu"); self.btn_roi.clicked.connect(self.set_roi_area)
        self.btn_publish=QtWidgets.QPushButton("Opublikuj ZIP na GitHub"); self.btn_publish.clicked.connect(self.publish_zip)
        self.btn_update=QtWidgets.QPushButton("Sprawdź aktualizacje"); self.btn_update.clicked.connect(self.try_update)
        self.btn_close=QtWidgets.QPushButton("Zamknij"); self.btn_close.clicked.connect(self.close)
        for b in (self.btn_start,self.btn_roi,self.btn_publish,self.btn_update,self.btn_close):
            b.setStyleSheet("background:#2a2a2a; color:#ffd27f; padding:8px; border:1px solid #444; border-radius:6px;")
        for b in (self.btn_start,self.btn_roi,self.btn_publish,self.btn_update,self.btn_close): btns.addWidget(b)
        right.addLayout(btns); layout.addLayout(right,1)
    def build_settings_tab(self):
        form=QtWidgets.QFormLayout(self.tab_settings)
        self.cfg=load_config()
        self.in_repo=QtWidgets.QLineEdit(self.cfg.get("github_repo","HOMZIK1/mt2009-skaner"))
        self.in_token=QtWidgets.QLineEdit(self.cfg.get("github_token","")); self.in_token.setEchoMode(QtWidgets.QLineEdit.Password)
        self.in_fps=QtWidgets.QDoubleSpinBox(); self.in_fps.setRange(0.5,60.0); self.in_fps.setValue(float(self.cfg.get("fps",4.0)))
        self.in_roi_w=QtWidgets.QSpinBox(); self.in_roi_w.setRange(120,2000); self.in_roi_w.setValue(int(self.cfg.get("roi_width",520)))
        self.in_roi_h=QtWidgets.QSpinBox(); self.in_roi_h.setRange(120,1200); self.in_roi_h.setValue(int(self.cfg.get("roi_height",300)))
        self.in_mx=QtWidgets.QSpinBox(); self.in_mx.setRange(0,600); self.in_mx.setValue(int(self.cfg.get("margin_x",30)))
        self.in_my=QtWidgets.QSpinBox(); self.in_my.setRange(0,600); self.in_my.setValue(int(self.cfg.get("margin_y",30)))
        self.in_dedup=QtWidgets.QDoubleSpinBox(); self.in_dedup.setRange(0.0,30.0); self.in_dedup.setValue(float(self.cfg.get("dedup_seconds",2.0)))
        self.in_title=QtWidgets.QLineEdit(self.cfg.get("window_title_filter","NEWMT2"))
        self.in_tess=QtWidgets.QLineEdit(self.cfg.get("tesseract_path", r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
        self.in_logdir=QtWidgets.QLineEdit(self.cfg.get("log_dir","./logs_market"))
        self.in_lang=QtWidgets.QLineEdit(self.cfg.get("language","pol+eng"))
        btn_save=QtWidgets.QPushButton("Zapisz"); btn_save.clicked.connect(self.save_settings)
        for label, widget in [
            ("Repo (owner/repo):", self.in_repo),
            ("GitHub Token (repo):", self.in_token),
            ("FPS:", self.in_fps),
            ("Szerokość ROI:", self.in_roi_w),
            ("Wysokość ROI:", self.in_roi_h),
            ("Margines X:", self.in_mx),
            ("Margines Y:", self.in_my),
            ("Antyduplikacja [s]:", self.in_dedup),
            ("Tytuł okna gry:", self.in_title),
            ("Ścieżka Tesseract:", self.in_tess),
            ("Folder logów:", self.in_logdir),
            ("Język OCR:", self.in_lang),
        ]: form.addRow(label, widget)
        form.addRow("", btn_save)
    def save_settings(self):
        self.cfg.update({
            "github_repo": self.in_repo.text(),
            "github_token": self.in_token.text(),
            "fps": float(self.in_fps.value()),
            "roi_width": int(self.in_roi_w.value()),
            "roi_height": int(self.in_roi_h.value()),
            "margin_x": int(self.in_mx.value()),
            "margin_y": int(self.in_my.value()),
            "dedup_seconds": float(self.in_dedup.value()),
            "window_title_filter": self.in_title.text(),
            "tesseract_path": self.in_tess.text(),
            "log_dir": self.in_logdir.text(),
            "language": self.in_lang.text(),
        }); save_config(self.cfg); QtWidgets.QMessageBox.information(self,"OK","Zapisano ustawienia.")
        self.worker.cfg=self.cfg
    def toggle_run(self):
        self.worker.set_running(not self.worker.running); self.update_status_ui()
    def on_status(self,_): self.update_status_ui()
    def update_status_ui(self):
        if self.worker.running:
            self.status_label.setText("STATUS: LOGOWANIE"); self.status_label.setStyleSheet("font-weight:bold; padding:8px; border-radius:6px; background:#1d5a2a; color:#ffd27f;"); self.btn_start.setText("Stop")
        else:
            self.status_label.setText("STATUS: STOP"); self.status_label.setStyleSheet("font-weight:bold; padding:8px; border-radius:6px; background:#5a1d1d; color:#ffd27f;"); self.btn_start.setText("Start")
    def on_frame_ready(self, gray):
        h,w=gray.shape; qimg=QtGui.QImage(gray.data,w,h,w,QtGui.QImage.Format_Grayscale8)
        pix=QtGui.QPixmap.fromImage(qimg).scaled(self.preview_label.width(), self.preview_label.height(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.preview_label.setPixmap(pix)
    def on_last_text(self, txt): self.last_text.setPlainText(txt)
    def on_item_logged(self, d):
        self.count_total+=1; self.counts[d["category"]]=self.counts.get(d["category"],0)+1
        self.counter_label.setText(f"Zalogowano (sesja): {self.count_total}")
        self.cat_label.setText(f"Księgi: {self.counts.get('book',0)} | Ulepszacze: {self.counts.get('upgrade',0)} | Kamienie+4: {self.counts.get('soulstone+4',0)} | Inne: {self.counts.get('other',0)}")
        self.last_read_label.setText(f"Ostatni odczyt: {d['name']} — {d['price']} Yang")
        self.logs_list.insertItem(0, f\"[{d['timestamp']}] {d['name']} — {d['price']}Y\")
        while self.logs_list.count()>10: self.logs_list.takeItem(self.logs_list.count()-1)
    def set_roi_area(self):
        size=select_roi_size()
        if size:
            w,h=size; save_size_to_config(w,h)
            self.cfg=load_config(); self.worker.cfg=self.cfg
            QtWidgets.QMessageBox.information(self, "ROI", f"Ustawiono ROI: {w}x{h}")
    def publish_zip(self):
        repo=self.cfg.get("github_repo","").strip()
        token=self.cfg.get("github_token","").strip()
        if not repo or not token:
            QtWidgets.QMessageBox.warning(self,"Brak danych","Uzupełnij repo i token w Ustawieniach.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Wybierz update.zip", "", "Zip (*.zip)")
        if not path: return
        # wersję bierzemy z nazwy pliku: np. update_v1.0.2.zip lub wpisujemy ręcznie
        version, ok = QtWidgets.QInputDialog.getText(self, "Wersja", "Podaj wersję (np. 1.0.2):")
        if not ok or not version.strip(): return
        tag=f"v{version.strip()}"
        try:
            rel=create_or_get_release(repo, tag, token, title=tag)
            url = upload_asset(rel, path, token, name="update.zip")
            # zaktualizuj version.json
            raw_url = f"https://github.com/{repo}/releases/download/{tag}/update.zip"
            update_version_json(repo, token, version.strip(), raw_url, notes=f"Auto publish {version.strip()}")
            QtWidgets.QMessageBox.information(self,"OK", f"Opublikowano {tag}.")
            # spróbuj od razu zaktualizować aplikację
            info=check_and_update(load_config().get("update_version_url"), os.path.dirname(os.path.abspath(sys.argv[0])))
            if info.get("updated"):
                QtWidgets.QMessageBox.information(self,"Aktualizacja",info.get("message",""))
                QtCore.QTimer.singleShot(150, lambda: (self.close(), os.execv(sys.executable,[sys.executable]+sys.argv)))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self,"Błąd", str(e))
    def closeEvent(self, e):
        self.worker.stop_flag=True; self.worker.set_running(False); self.worker.wait(500)
        return super().closeEvent(e)

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet('''
        QMainWindow { background-color: #0f0f0f; }
        QLabel { color: #f2e7c9; }
        QTabWidget::pane { border: 1px solid #3b2d12; }
        QTabBar::tab { background: #201a10; color: #d9c089; padding: 8px 12px; border: 1px solid #3b2d12; }
        QTabBar::tab:selected { background: #2a2316; }
        QPushButton { background: #2a2a2a; color: #ffd27f; padding: 8px; border: 1px solid #444; border-radius: 6px; }
        QPushButton:hover { background: #333; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QListWidget, QPlainTextEdit { background: #1b1b1b; color: #c8c8c8; border: 1px solid #333; }
    ''')
    w=MainWindow(); w.show(); w.worker.start(); sys.exit(app.exec_())

if __name__ == "__main__":
    main()
