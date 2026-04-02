"""Pattern Recognition — texture classification, anomaly detection.

Uses Local Binary Patterns (LBP) computed from pure numpy.
Detects repeating patterns, textures, and visual anomalies.
"""

import numpy as np
import cv2


class PatternRecognizer:
    """Texture analysis and anomaly detection on an 8x6 grid."""

    def __init__(self):
        self._cell_baselines: np.ndarray | None = None  # (48, 16) LBP histograms
        self._edge_baselines: np.ndarray | None = None   # (48,) edge densities
        self._calibration_frames: int = 0
        self.anomaly_cells: list[int] = []
        self.anomaly_regions: list[str] = []
        self.texture_type: str = "unknown"
        self._anomaly_persistence: np.ndarray = np.zeros(48)  # frames an anomaly persists

    def process(self, gray: np.ndarray, **kwargs) -> dict:
        """Analyze texture patterns and detect anomalies."""
        h, w = gray.shape[:2]
        cell_h = h // 6
        cell_w = w // 8

        # Compute LBP histogram and edge density per cell
        cell_lbps = np.zeros((48, 16))
        cell_edges = np.zeros(48)

        for row in range(6):
            for col in range(8):
                idx = row * 8 + col
                y0, y1 = row * cell_h, (row + 1) * cell_h
                x0, x1 = col * cell_w, (col + 1) * cell_w
                cell = gray[y0:y1, x0:x1]

                # LBP: compare each pixel to its 8 neighbors
                lbp = self._compute_lbp(cell)
                # Quantize to 16 bins
                bins = np.clip(lbp.ravel() // 16, 0, 15).astype(int)
                hist = np.bincount(bins, minlength=16).astype(np.float32)
                total = np.sum(hist)
                if total > 0:
                    hist /= total
                cell_lbps[idx] = hist

                # Edge density via Sobel magnitude
                sx = cv2.Sobel(cell, cv2.CV_32F, 1, 0, ksize=3)
                sy = cv2.Sobel(cell, cv2.CV_32F, 0, 1, ksize=3)
                mag = np.sqrt(sx * sx + sy * sy)
                cell_edges[idx] = float(np.mean(mag))

        # Calibration: build baselines from first 10 frames
        if self._calibration_frames < 10:
            if self._cell_baselines is None:
                self._cell_baselines = cell_lbps.copy()
                self._edge_baselines = cell_edges.copy()
            else:
                alpha = 1.0 / (self._calibration_frames + 1)
                self._cell_baselines = self._cell_baselines * (1 - alpha) + cell_lbps * alpha
                self._edge_baselines = self._edge_baselines * (1 - alpha) + cell_edges * alpha
            self._calibration_frames += 1
            self.texture_type = "calibrating"
            return self._result()

        # Anomaly detection: chi-squared distance per cell
        self.anomaly_cells = []
        for idx in range(48):
            diff = (cell_lbps[idx] - self._cell_baselines[idx]) ** 2
            denom = cell_lbps[idx] + self._cell_baselines[idx] + 1e-8
            chi_sq = float(np.sum(diff / denom))

            edge_diff = abs(cell_edges[idx] - self._edge_baselines[idx])
            edge_norm = self._edge_baselines[idx] + 1e-6

            if chi_sq > 0.5 or edge_diff / edge_norm > 0.8:
                self.anomaly_cells.append(idx)
                self._anomaly_persistence[idx] += 1
            else:
                # If anomaly persisted long enough, adopt as new baseline
                if self._anomaly_persistence[idx] > 5:
                    self._cell_baselines[idx] = cell_lbps[idx]
                    self._edge_baselines[idx] = cell_edges[idx]
                self._anomaly_persistence[idx] = 0

        # Slow baseline adaptation for non-anomalous cells
        for idx in range(48):
            if idx not in self.anomaly_cells:
                self._cell_baselines[idx] = self._cell_baselines[idx] * 0.98 + cell_lbps[idx] * 0.02
                self._edge_baselines[idx] = self._edge_baselines[idx] * 0.98 + cell_edges[idx] * 0.02

        # Map anomaly cells to regions
        self.anomaly_regions = self._cells_to_regions(self.anomaly_cells)

        # Overall texture type
        mean_edge = float(np.mean(cell_edges))
        edge_var = float(np.var(cell_edges))
        if mean_edge < 5:
            self.texture_type = "smooth"
        elif edge_var > 100:
            self.texture_type = "structured"
        else:
            self.texture_type = "textured"

        return self._result()

    def _compute_lbp(self, cell: np.ndarray) -> np.ndarray:
        """Compute simplified LBP (Local Binary Pattern) using numpy."""
        h, w = cell.shape
        if h < 3 or w < 3:
            return np.zeros((max(h - 2, 1), max(w - 2, 1)), dtype=np.uint8)

        center = cell[1:-1, 1:-1].astype(np.int16)
        lbp = np.zeros_like(center, dtype=np.uint8)

        # 8 neighbors
        neighbors = [
            cell[:-2, :-2], cell[:-2, 1:-1], cell[:-2, 2:],
            cell[1:-1, :-2],                  cell[1:-1, 2:],
            cell[2:, :-2],  cell[2:, 1:-1],   cell[2:, 2:],
        ]

        for bit, neighbor in enumerate(neighbors):
            lbp |= ((neighbor.astype(np.int16) >= center).astype(np.uint8) << bit)

        return lbp

    @staticmethod
    def _cells_to_regions(cells: list[int]) -> list[str]:
        """Map cell indices to human-readable regions."""
        regions = set()
        for idx in cells:
            row = idx // 8
            col = idx % 8
            v = "top" if row < 2 else ("mid" if row < 4 else "bottom")
            h = "left" if col < 3 else ("center" if col < 5 else "right")
            regions.add(f"{v}-{h}")
        return sorted(regions)

    def _result(self) -> dict:
        return {
            "texture_type": self.texture_type,
            "anomalies_detected": len(self.anomaly_cells),
            "anomaly_regions": self.anomaly_regions,
        }
