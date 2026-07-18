"""Import manuel des médias de l'Atelier — vidéos de scènes et plans A/B.

Quand une scène est produite hors Higgsfield (montage local, autre studio),
l'admin l'importe ici. Les fichiers vivent dans le VOLUME de données
(./data/media, monté sur /app/data en prod) : ils survivent aux
redéploiements, contrairement à app/static/ qui est reconstruit avec l'image.

Contraintes vérifiées côté serveur (type réel par octets de signature, pas
l'extension) ; résolution et durée sont contrôlées si ffmpeg est disponible
et remontées en AVERTISSEMENTS (on n'empêche pas d'importer, on prévient).
"""
from __future__ import annotations

import os
import re
import subprocess

MEDIA_DIR = os.environ.get("LAB_MEDIA_DIR", "./data/media")

VIDEO_EXTS = {".webm": "video/webm", ".mp4": "video/mp4"}
IMAGE_EXTS = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}
MAX_VIDEO_BYTES = 30 * 1024 * 1024  # 30 Mo — large pour 720p bien encodé
MAX_IMAGE_BYTES = 5 * 1024 * 1024   # 5 Mo

# Référence : le décor actuel est en 1280×720 ; les boucles font 4-8 s,
# les one-shots 3-6 s, la naissance ~10 s.
EXPECTED_W, EXPECTED_H = 1280, 720
MAX_DURATION_S = 15.0

# Texte affiché dans l'admin — la notice d'import.
CONSTRAINTS = (
    "VIDÉOS de scène : WebM (VP9) ou MP4 (H.264), 30 Mo max. "
    f"Résolution du décor : {EXPECTED_W}×{EXPECTED_H} (16:9) — une autre taille "
    "s'affiche mais casse la superposition avec les plans fixes. "
    "Durées conseillées : boucles 4-8 s, scènes ponctuelles 3-6 s, "
    "naissance ~10 s (15 s max). MP4 seul suffit pour les navigateurs "
    "modernes ; WebM en plus est un bonus de compatibilité.\n"
    "PLANS DE RÉFÉRENCE (A/B) : WebP, PNG ou JPEG, 5 Mo max, même "
    f"{EXPECTED_W}×{EXPECTED_H}, SANS élément qui trahit le mouvement "
    "(pas de vapeur, pas de bulles, pas de flou de bougé).\n"
    "RACCORD STRICT : la PREMIÈRE frame de chaque vidéo doit être "
    "identique au plan de référence de départ, la DERNIÈRE identique au "
    "plan d'arrivée (A = atelier inerte, B = Hermès vivant) ; tous les "
    "cycles (bras, fumée, lumières) doivent être clos avant la fin."
)


def sniff(data: bytes) -> str | None:
    """Extension réelle d'après la signature binaire — l'extension du nom de
    fichier ne fait pas foi. None si le format n'est pas reconnu."""
    if data[:4] == b"\x1aE\xdf\xa3":  # EBML → WebM/Matroska
        return ".webm"
    if len(data) > 12 and data[4:8] == b"ftyp":  # ISO BMFF → MP4/MOV
        return ".mp4"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return None


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def probe_warnings(path: str, is_image: bool) -> list[str]:
    """Avertissements non bloquants : résolution inattendue, durée excessive.
    Silencieux si ffmpeg n'est pas disponible (l'import reste possible)."""
    exe = _ffmpeg_exe()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-hide_banner", "-i", path],
                             capture_output=True, text=True, timeout=20).stderr
    except Exception:
        return []
    warnings = []
    m = re.search(r"(\d{3,5})x(\d{3,5})", out)
    if m and (int(m.group(1)), int(m.group(2))) != (EXPECTED_W, EXPECTED_H):
        warnings.append(
            f"Résolution {m.group(1)}×{m.group(2)} au lieu de "
            f"{EXPECTED_W}×{EXPECTED_H} — la superposition avec les plans "
            "fixes ne sera pas raccord."
        )
    if not is_image:
        d = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
        if d:
            secs = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
            if secs > MAX_DURATION_S:
                warnings.append(
                    f"Durée {secs:.1f} s > {MAX_DURATION_S:.0f} s conseillées — "
                    "l'interface reste voilée pendant toute la scène."
                )
    return warnings


def list_custom() -> dict[str, list[str]]:
    """Fichiers importés présents : {nom-sans-extension: [extensions]} —
    le moteur du front sait ainsi exactement quelles sources charger."""
    out: dict[str, list[str]] = {}
    try:
        for f in sorted(os.listdir(MEDIA_DIR)):
            base, ext = os.path.splitext(f)
            if ext in VIDEO_EXTS and re.match(r"^[a-z0-9-]+$", base):
                out.setdefault(base, []).append(ext.lstrip("."))
    except OSError:
        pass
    return out


def purge_target(target: str) -> None:
    """Supprime tous les imports d'une cible (u-<cible>-*) — un nouvel import
    remplace l'ancien, un retour au défaut nettoie tout."""
    try:
        for f in os.listdir(MEDIA_DIR):
            if f.startswith(f"u-{target}-"):
                os.remove(os.path.join(MEDIA_DIR, f))
    except OSError:
        pass
