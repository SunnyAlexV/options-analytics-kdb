"""Assemble the public demo (a Hugging Face Space) and optionally upload it.

    python scripts/deploy_demo.py --bundle demo/bundle                     # assemble build/hf-space only
    python scripts/deploy_demo.py --bundle demo/bundle --space USER/btc-options-desk   # and upload

The Space is a Docker app (deploy/hf-space/Dockerfile) holding only what the replay needs:
dashboard/, feed/schema.py, risk/history.py and the bundle. Uploading needs a Hugging Face
account and a write token:  pip install huggingface_hub && huggingface-cli login
"""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["feed/__init__.py", "feed/schema.py", "risk/__init__.py", "risk/history.py"]


def assemble(bundle: Path, out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in (ROOT / "deploy" / "hf-space").iterdir():
        shutil.copy(f, out / f.name)
    shutil.copytree(ROOT / "dashboard", out / "dashboard", ignore=shutil.ignore_patterns("__pycache__"))
    for f in FILES:
        (out / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, out / f)
    shutil.copytree(bundle, out / "bundle")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"Assembled {out} ({size:.1f} MB)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--out", default=str(ROOT / "build" / "hf-space"))
    ap.add_argument("--space", help="USER/NAME of the Hugging Face Space to create/update")
    a = ap.parse_args()
    out = assemble(Path(a.bundle), Path(a.out))
    if a.space:
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(a.space, repo_type="space", space_sdk="docker", exist_ok=True)
        api.upload_folder(folder_path=str(out), repo_id=a.space, repo_type="space",
                          commit_message="Update demo")
        print(f"Uploaded: https://huggingface.co/spaces/{a.space}")


if __name__ == "__main__":
    main()
