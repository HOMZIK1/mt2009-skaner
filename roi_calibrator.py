
import json, os, cv2, mss, numpy as np
CONFIG_FILE = "config.json"
def select_roi_size():
    with mss.mss() as sct:
        mon = sct.monitors[0]
        shot = np.array(sct.grab(mon))[:, :, :3]
    cv2.namedWindow("Zaznacz obszar dymka (Enter=OK, Esc=Anuluj)", cv2.WINDOW_NORMAL)
    cv2.setWindowProperty("Zaznacz obszar dymka (Enter=OK, Esc=Anuluj)", cv2.WND_PROP_TOPMOST, 1)
    roi = cv2.selectROI("Zaznacz obszar dymka (Enter=OK, Esc=Anuluj)", shot, fromCenter=False, showCrosshair=True)
    cv2.destroyWindow("Zaznacz obszar dymka (Enter=OK, Esc=Anuluj)")
    x,y,w,h = map(int, roi)
    if w<=0 or h<=0: return None
    return w,h
def save_size_to_config(w,h):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f: cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    cfg["roi_width"] = int(w); cfg["roi_height"] = int(h)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
