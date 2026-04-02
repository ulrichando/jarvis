"""JARVIS Vision Describer — analyze images using local CV + AI reasoning.

Since we don't have a cloud vision API, we:
1. Use OpenCV to detect faces, objects, colors, text
2. Feed the analysis to the text AI to generate a natural description
"""

import cv2
import numpy as np


def analyze_image(image_path: str) -> dict:
    """Analyze an image and extract everything we can see locally."""
    img = cv2.imread(image_path)
    if img is None:
        return {"error": "Couldn't read image"}

    h, w = img.shape[:2]
    analysis = {
        "width": w,
        "height": h,
        "faces": [],
        "dominant_colors": [],
        "brightness": "",
        "scene": "",
        "text_regions": 0,
    }

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Face detection
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
    smile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_smile.xml')

    faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(30, 30))
    for (x, y, fw, fh) in faces:
        face_info = {
            "position": "center" if abs(x + fw//2 - w//2) < w//4 else "left" if x < w//2 else "right",
            "size": "close" if fw > w * 0.3 else "medium" if fw > w * 0.15 else "far",
        }

        # Check for eyes and smile in face region
        roi_gray = gray[y:y+fh, x:x+fw]
        eyes = eye_cascade.detectMultiScale(roi_gray, 1.1, 3, minSize=(15, 15))
        smiles = smile_cascade.detectMultiScale(roi_gray, 1.8, 20, minSize=(25, 25))
        face_info["eyes_visible"] = len(eyes) > 0
        face_info["smiling"] = len(smiles) > 0

        analysis["faces"].append(face_info)

    # Dominant colors
    small = cv2.resize(img, (50, 50))
    pixels = small.reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _, labels, centers = cv2.kmeans(pixels, 3, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    for center in centers:
        b, g, r = int(center[0]), int(center[1]), int(center[2])
        color_name = _rgb_to_name(r, g, b)
        analysis["dominant_colors"].append(color_name)

    # Brightness
    mean_brightness = np.mean(gray)
    if mean_brightness < 50:
        analysis["brightness"] = "very dark"
    elif mean_brightness < 100:
        analysis["brightness"] = "dim"
    elif mean_brightness < 170:
        analysis["brightness"] = "well lit"
    else:
        analysis["brightness"] = "very bright"

    # Scene type guess
    # Check if mostly one color (wall/sky) or complex (outdoor/room)
    edges = cv2.Canny(gray, 50, 150)
    edge_ratio = np.sum(edges > 0) / (w * h)
    if edge_ratio > 0.15:
        analysis["scene"] = "complex/detailed scene"
    elif edge_ratio > 0.05:
        analysis["scene"] = "moderate detail"
    else:
        analysis["scene"] = "simple/plain background"

    # Upper body detection
    upper_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_upperbody.xml')
    bodies = upper_cascade.detectMultiScale(gray, 1.1, 3, minSize=(60, 60))
    analysis["bodies_detected"] = len(bodies)

    return analysis


def describe_analysis(analysis: dict) -> str:
    """Turn CV analysis into a natural language description for the AI."""
    if "error" in analysis:
        return analysis["error"]

    parts = []

    # Faces
    n_faces = len(analysis["faces"])
    if n_faces == 0:
        if analysis["bodies_detected"] > 0:
            parts.append(f"I can see {analysis['bodies_detected']} person(s) but can't see their face clearly")
        else:
            parts.append("No people visible in frame")
    elif n_faces == 1:
        f = analysis["faces"][0]
        desc = f"One person visible, {f['size']} up, positioned {f['position']}"
        if f["smiling"]:
            desc += ", appears to be smiling"
        if f["eyes_visible"]:
            desc += ", eyes visible"
        parts.append(desc)
    else:
        parts.append(f"{n_faces} faces visible")

    # Scene
    parts.append(f"Scene: {analysis['brightness']}, {analysis['scene']}")

    # Colors
    if analysis["dominant_colors"]:
        parts.append(f"Main colors: {', '.join(analysis['dominant_colors'][:3])}")

    return ". ".join(parts)


def _rgb_to_name(r: int, g: int, b: int) -> str:
    """Rough RGB to color name."""
    if r > 200 and g > 200 and b > 200:
        return "white"
    if r < 50 and g < 50 and b < 50:
        return "black"
    if r > 150 and g < 80 and b < 80:
        return "red"
    if r < 80 and g > 150 and b < 80:
        return "green"
    if r < 80 and g < 80 and b > 150:
        return "blue"
    if r > 150 and g > 150 and b < 80:
        return "yellow"
    if r > 150 and g > 100 and b < 80:
        return "orange"
    if r > 100 and g < 80 and b > 100:
        return "purple"
    if r > 150 and g > 100 and b > 100:
        return "warm/beige"
    if r < 100 and g > 100 and b > 100:
        return "cool/teal"
    if abs(r - g) < 30 and abs(g - b) < 30:
        if r > 128:
            return "light gray"
        return "dark gray"
    return "mixed"
