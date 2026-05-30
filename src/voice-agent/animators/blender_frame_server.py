"""MJPEG frame server injected into Blender.

A bpy.app.timers callback (main thread, required for GPU) renders the face
camera offscreen via EEVEE RENDERED viewport shading, writes a JPEG to
/dev/shm via atomic rename, and a daemon HTTP thread serves it as
multipart/x-mixed-replace on 127.0.0.1:8770.

Idempotent: a guard flag in bpy.app.driver_namespace prevents double-install
of the timer + server across re-runs (e.g. animator restarts). Validated on
Blender 5.1.2 / OpenGL.

Endpoints:
  GET /stream.mjpg  -> multipart/x-mixed-replace MJPEG
  GET /frame.jpg    -> single latest JPEG (health/smoke test)
"""

CODE = r'''
import bpy, gpu, os, threading, time
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NS = bpy.app.driver_namespace
PORT = int(os.environ.get("JARVIS_FACE_PORT", "8770"))
W = int(os.environ.get("JARVIS_FACE_W", "448"))
H = int(os.environ.get("JARVIS_FACE_H", "448"))
FPS = float(os.environ.get("JARVIS_FACE_FPS", "24"))
SHM = "/dev/shm/jarvis_face.jpg"
TMP = "/dev/shm/jarvis_face.tmp.jpg"

def _find_view3d():
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            return area.spaces.active, region
    return None, None

def _prepare_space():
    """Configure the viewport for a clean render once: no overlays, EEVEE
    RENDERED shading, nothing selected."""
    space, region = _find_view3d()
    if space is None:
        return
    space.overlay.show_overlays = False
    space.shading.type = "RENDERED"
    for o in bpy.data.objects:
        try:
            o.select_set(False)
        except Exception:
            pass

def _render_to_shm():
    scene = bpy.context.scene
    cam = bpy.data.objects.get("JarvisFaceCam")
    if cam is None:
        return
    space, region = _find_view3d()
    if space is None or region is None:
        return
    off = NS.get("jarvis_off")
    if off is None or NS.get("jarvis_off_dim") != (W, H):
        if off is not None:
            try:
                off.free()
            except Exception:
                pass
        off = gpu.types.GPUOffScreen(W, H)
        NS["jarvis_off"] = off
        NS["jarvis_off_dim"] = (W, H)
    view_matrix = cam.matrix_world.inverted()
    projection_matrix = cam.calc_matrix_camera(
        bpy.context.evaluated_depsgraph_get(), x=W, y=H)
    off.draw_view3d(scene, bpy.context.view_layer, space, region,
                    view_matrix, projection_matrix, do_color_management=True)
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        buf = fb.read_color(0, 0, W, H, 4, 0, "UBYTE")
    buf.dimensions = W * H * 4
    arr = np.frombuffer(bytes(buf), dtype=np.uint8).astype(np.float32) / 255.0
    img = bpy.data.images.get("JarvisFaceFrame")
    if img is None or tuple(img.size) != (W, H):
        if img:
            bpy.data.images.remove(img)
        img = bpy.data.images.new("JarvisFaceFrame", width=W, height=H)
    img.pixels.foreach_set(arr)
    img.file_format = "JPEG"
    img.filepath_raw = TMP
    img.save()
    os.replace(TMP, SHM)

def _timer():
    try:
        _render_to_shm()
    except Exception as e:
        print("[face-server] render error:", e)
    return 1.0 / FPS

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _latest(self):
        try:
            with open(SHM, "rb") as f:
                return f.read()
        except OSError:
            return None
    def do_GET(self):
        if self.path.startswith("/frame.jpg"):
            data = self._latest()
            if not data:
                self.send_response(503); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(data); return
        if self.path.startswith("/stream.mjpg"):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type",
                "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    data = self._latest()
                    if data:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            ("Content-Length: %d\r\n\r\n" % len(data)).encode())
                        self.wfile.write(data); self.wfile.write(b"\r\n")
                    time.sleep(1.0 / FPS)
            except (BrokenPipeError, ConnectionResetError):
                return
            return
        self.send_response(404); self.end_headers()

def _install():
    if NS.get("jarvis_face_installed"):
        print("RESULT: ALREADY_INSTALLED port=%d" % PORT)
        return
    _prepare_space()
    bpy.app.timers.register(_timer, first_interval=0.1, persistent=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    NS["jarvis_face_installed"] = True
    NS["jarvis_face_srv"] = srv
    print("RESULT: INSTALLED port=%d %dx%d@%.0ffps" % (PORT, W, H, FPS))

_install()
'''


def install(conn):
    """Send the frame-server CODE into Blender via a BlenderConnection."""
    return conn.send("execute_code", {"code": CODE})
