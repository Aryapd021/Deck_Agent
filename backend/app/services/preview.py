import logging
from pathlib import Path
import shutil
import subprocess
import tempfile

from app.config import get_settings

logger = logging.getLogger(__name__)


def generate_previews(pptx_path: Path, out_dir: Path) -> list[Path]:
    """Generates slide preview PNGs from a PPTX presentation using headless LibreOffice and PyMuPDF.

    Returns an empty list without error when preview_mode is 'outline' or when 'soffice' is absent.
    Any failure is logged and returns [] (preview generation is non-fatal).
    """
    settings = get_settings()
    if settings.preview_mode == "outline":
        return []

    soffice_bin = shutil.which("soffice")
    if soffice_bin is None:
        return []

    pptx_file = Path(pptx_path)
    if not pptx_file.exists() or not pptx_file.is_file():
        logger.warning("PPTX file does not exist: %s", pptx_path)
        return []

    tmp_parent = Path(tempfile.mkdtemp(prefix="preview_lo_"))
    try:
        profile_dir = tmp_parent / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_uri = profile_dir.as_uri()

        pdf_out_dir = tmp_parent / "pdf"
        pdf_out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            soffice_bin,
            "--headless",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_out_dir),
            str(pptx_file.resolve()),
        ]

        res = subprocess.run(cmd, capture_output=True, timeout=120)
        if res.returncode != 0:
            stderr_text = res.stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "LibreOffice PDF conversion failed with exit code %s: %s",
                res.returncode,
                stderr_text,
            )
            return []

        pdf_files = list(pdf_out_dir.glob("*.pdf"))
        if not pdf_files:
            logger.warning("No PDF file produced by LibreOffice in %s", pdf_out_dir)
            return []

        pdf_file = pdf_files[0]

        try:
            import pymupdf as fitz
        except ImportError:
            import fitz  # type: ignore

        doc = fitz.open(str(pdf_file))
        slides_dir = Path(out_dir) / "slides"
        slides_dir.mkdir(parents=True, exist_ok=True)

        output_pngs: list[Path] = []
        zoom = 1.5
        mat = fitz.Matrix(zoom, zoom)

        for i, page in enumerate(doc):
            k = i + 1
            pix = page.get_pixmap(matrix=mat)
            png_path = slides_dir / f"{k}.png"
            pix.save(str(png_path))
            output_pngs.append(png_path)

        doc.close()
        return output_pngs

    except Exception as e:
        logger.warning("Failed to generate slide previews: %s", e)
        return []
    finally:
        if tmp_parent.exists():
            shutil.rmtree(tmp_parent, ignore_errors=True)
