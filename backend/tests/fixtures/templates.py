import io
from pathlib import Path
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
from pptx.dml.color import RGBColor
from pptx.oxml import parse_xml
from pptx.util import Inches, Pt

from PIL import Image as PILImage


def create_stock_default_template(path: Path) -> Path:
    prs = Presentation()
    prs.slide_width = 12192000
    prs.slide_height = 6858000
    prs.save(str(path))
    return path


def create_dark_monochrome_43_template(path: Path) -> Path:
    prs = Presentation()
    # 4:3 Aspect Ratio: 10 x 7.5 in
    prs.slide_width = 9144000
    prs.slide_height = 6858000

    try:
        master = prs.slide_masters[0]
        master.background.fill.solid()
        master.background.fill.fore_color.rgb = RGBColor(15, 23, 42)

        for rel in master.part.rels.values():
            if "theme" in rel.reltype:
                theme_blob = rel.target_part.blob.decode("utf-8")
                theme_blob = theme_blob.replace('name="Office"', 'name="CustomDark"')
                rel.target_part._blob = theme_blob.encode("utf-8")
                break
    except Exception:
        pass

    prs.save(str(path))
    return path


def create_fill_mode_169_template(path: Path) -> Path:
    prs = Presentation()
    prs.slide_width = 12192000
    prs.slide_height = 6858000

    # Save a valid PNG image for picture shape using PIL
    temp_img_path = path.parent / f"{path.stem}_logo.png"
    img = PILImage.new("RGB", (64, 64), color=(0, 111, 192))
    img.save(str(temp_img_path), format="PNG")

    slide_titles = [
        "Executive Summary",
        "Problem Analysis",
        "Proposed Architecture",
        "Market Feasibility",
        "Roadmap & Next Steps",
    ]

    for i, title in enumerate(slide_titles):
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank layout

        # 1. Static Header
        header = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(4.0), Inches(0.4))
        header.text_frame.text = "PROJECT DELTA CONFIDENTIAL"

        # 2. Static Title with real text
        t_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.8), Inches(8.0), Inches(0.6))
        t_box.text_frame.text = title

        # 3. Static Logo Picture
        slide.shapes.add_picture(
            str(temp_img_path), Inches(11.5), Inches(0.4), Inches(1.2), Inches(0.8)
        )

        # 4. Static Footer
        footer = slide.shapes.add_textbox(Inches(0.5), Inches(6.8), Inches(6.0), Inches(0.4))
        footer.text_frame.text = "Copyright 2026 - All Rights Reserved"

        # 5. Static Slide Number
        num = slide.shapes.add_textbox(Inches(12.0), Inches(6.8), Inches(0.8), Inches(0.4))
        num.text_frame.text = str(i + 1)

        # 6. FILLABLE shape: large text box with sample body text covering > 12% slide area
        body = slide.shapes.add_textbox(
            Inches(0.5), Inches(1.6), Inches(12.0), Inches(5.0)
        )
        body.text_frame.text = (
            f"Sample placeholder text for slide {i + 1}. "
            "Please replace this content with the actual project details and technical descriptions. "
            "• Bullet point 1: Key observation regarding performance and scalability.\n"
            "• Bullet point 2: Implementation strategy and framework selection.\n"
            "• Bullet point 3: Verification benchmarks and target metrics."
        )

    prs.save(str(path))
    return path
