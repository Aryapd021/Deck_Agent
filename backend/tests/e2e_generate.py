import argparse
import json
from pathlib import Path
import sys
import time

import httpx
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def run_e2e(
    repo_arg: str,
    template_id: str,
    pitch_file: Path,
    chat_file: Path | None = None,
    base_url: str = "http://localhost:8000",
) -> bool:
    print(f"Starting E2E generation test against {base_url}...")
    start_time = time.time()

    # 1. Read input files
    if not pitch_file.exists():
        print(f"[FAIL] Pitch notes file not found: {pitch_file}", file=sys.stderr)
        return False
    pitch_notes = pitch_file.read_text(encoding="utf-8")

    chat_text = ""
    if chat_file is not None and chat_file.exists():
        chat_text = chat_file.read_text(encoding="utf-8")

    with httpx.Client(base_url=base_url, timeout=120.0) as client:
        # 2. POST /api/projects
        is_url = repo_arg.startswith(("http://", "https://", "git@"))
        payload: dict[str, str] = {
            "name": f"E2E Test {Path(repo_arg).name or 'Repo'}",
            "template_id": template_id,
        }
        if is_url:
            payload["repo_url"] = repo_arg
        else:
            payload["repo_path"] = str(Path(repo_arg).resolve())

        print(f"[1/6] Creating project with repo: {repo_arg}...")
        res = client.post("/api/projects", json=payload)
        if res.status_code != 201:
            print(f"[FAIL] POST /api/projects returned {res.status_code}: {res.text}", file=sys.stderr)
            return False
        project_data = res.json()
        project_id = project_data["id"]
        print(f"      Project created: {project_id}")

        # 3. PUT /api/projects/{id}/inputs
        print("[2/6] Uploading pitch notes and chat...")
        res = client.put(
            f"/api/projects/{project_id}/inputs",
            json={"pitch_notes": pitch_notes, "chat_text": chat_text, "criteria": ""},
        )
        if res.status_code != 200:
            print(f"[FAIL] PUT inputs returned {res.status_code}: {res.text}", file=sys.stderr)
            return False

        # 4. POST /api/projects/{id}/generate
        print("[3/6] Starting generate job...")
        res = client.post(f"/api/projects/{project_id}/generate")
        if res.status_code != 202:
            print(f"[FAIL] POST generate returned {res.status_code}: {res.text}", file=sys.stderr)
            return False
        job_id = res.json()["job_id"]
        print(f"      Job started: {job_id}")

        # 5. Read SSE stream until terminal event
        print("[4/6] Streaming SSE events...")
        terminal_status = None
        terminal_version = None
        stages_seen = []

        with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
            for line in stream.iter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    event_data = json.loads(line[6:])
                    seq = event_data.get("seq")
                    stage = event_data.get("stage")
                    status = event_data.get("status")
                    message = event_data.get("message")
                    version = event_data.get("version")
                    print(f"      [{stage.upper()}] status={status} msg={message!r}")

                    if not stages_seen or stages_seen[-1] != stage:
                        stages_seen.append(stage)

                    if stage == "done" or status == "error":
                        terminal_status = status
                        terminal_version = version
                        break

        if terminal_status != "done":
            print(f"[FAIL] Job terminated with non-success status: {terminal_status}", file=sys.stderr)
            return False

        # 6. GET /api/projects/{id}/deck
        print("[5/6] Validating generated deck...")
        res = client.get(f"/api/projects/{project_id}/deck")
        if res.status_code != 200:
            print(f"[FAIL] GET /deck returned {res.status_code}: {res.text}", file=sys.stderr)
            return False
        deck_resp = res.json()
        deck = deck_resp.get("deck", {})
        version = deck_resp.get("version", terminal_version or 1)

        # 7. Download deck.pptx to ./storage/e2e/
        print("[6/6] Downloading artifacts and running checks...")
        e2e_dir = Path("./storage/e2e").resolve()
        e2e_dir.mkdir(parents=True, exist_ok=True)
        local_pptx = e2e_dir / f"deck_v{version}.pptx"

        res_pptx = client.get(f"/api/projects/{project_id}/v/{version}/deck.pptx")
        if res_pptx.status_code != 200:
            print(f"[FAIL] GET deck.pptx returned {res_pptx.status_code}: {res_pptx.text}", file=sys.stderr)
            return False
        local_pptx.write_bytes(res_pptx.content)

        # Download / check diagram.png
        res_diag = client.get(f"/api/projects/{project_id}/v/{version}/diagram.png")
        diag_ok = res_diag.status_code == 200

        # Perform all verification checks
        slides = deck.get("slides", [])
        arch_slide = next((s for s in slides if s.get("type") == "architecture"), None)

        checks = {}
        # Check 1: version committed
        checks["version committed"] = version is not None and version >= 1

        # Check 2: deck has 10 slides (or max_slides)
        checks["deck has 10 slides (or max_slides)"] = len(slides) == 10

        # Check 3: the architecture slide has image_ref "diagram"
        checks['the architecture slide has image_ref "diagram"'] = (
            arch_slide is not None and arch_slide.get("image_ref") == "diagram"
        )

        # Check 4: the pptx re-opens with python-pptx and the architecture slide contains a picture shape
        pptx_check = False
        try:
            prs = Presentation(str(local_pptx))
            if len(prs.slides) == len(slides):
                arch_idx = slides.index(arch_slide) if arch_slide else 4
                if arch_idx < len(prs.slides):
                    pptx_arch_slide = prs.slides[arch_idx]
                    pics = [s for s in pptx_arch_slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
                    pptx_check = len(pics) >= 1
        except Exception as e:
            print(f"Error opening PPTX: {e}", file=sys.stderr)
            pptx_check = False
        checks["the pptx re-opens with python-pptx and the architecture slide contains a picture shape"] = pptx_check

        # Check 5: GET diagram.png returns 200
        checks["GET diagram.png returns 200"] = diag_ok

        elapsed = time.time() - start_time
        print("\n" + "=" * 60)
        print(f"E2E GENERATE RESULTS (elapsed: {elapsed:.2f}s)")
        print("=" * 60)
        all_passed = True
        for name, passed in checks.items():
            status_str = "PASS" if passed else "FAIL"
            print(f"  [{status_str}] {name}")
            if not passed:
                all_passed = False
        print("=" * 60 + "\n")

        return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E test for DeckAgent generate pipeline")
    parser.add_argument("--repo", required=True, help="Repository URL or local path")
    parser.add_argument("--template", required=True, help="Template ID")
    parser.add_argument("--pitch", required=True, type=Path, help="Path to pitch notes file")
    parser.add_argument("--chat", type=Path, default=None, help="Path to exported chat file")
    parser.add_argument("--base", default="http://localhost:8000", help="Backend base URL")

    args = parser.parse_args()

    success = run_e2e(
        repo_arg=args.repo,
        template_id=args.template,
        pitch_file=args.pitch,
        chat_file=args.chat,
        base_url=args.base,
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
