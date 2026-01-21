import os
import json
import hashlib
from pathlib import Path
from typing import Dict, List
from datetime import datetime

class StorageMonitor:
    def __init__(self, storage_path: str, metadata_file: str):
        base_dir = Path(__file__).resolve().parent  # final_agent
        self.storage_path = base_dir / storage_path
        self.metadata_file = base_dir / metadata_file
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict:
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {"files": {}, "last_sync": None}

    def _save_metadata(self):
        os.makedirs(self.metadata_file.parent, exist_ok=True)
        with open(self.metadata_file, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)

    def _file_hash(self, filepath: Path) -> str:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def get_changes(self) -> Dict[str, List[str]]:
        articles_dir = self.storage_path / "articles"
        if not articles_dir.exists():
            return {"new": [], "modified": [], "deleted": []}

        current_files = {}
        for md_file in articles_dir.rglob("*.md"):
            rel_path = md_file.relative_to(articles_dir).as_posix()
            current_files[rel_path] = {
                "hash": self._file_hash(md_file),
                "mtime": datetime.fromtimestamp(md_file.stat().st_mtime).isoformat(),
                "size": md_file.stat().st_size
            }

        prev_files = self.metadata.get("files", {})
        changes = {"new": [], "modified": [], "deleted": []}

        for filename, info in current_files.items():
            if filename not in prev_files:
                changes["new"].append(filename)
            elif prev_files[filename]["hash"] != info["hash"]:
                changes["modified"].append(filename)

        for filename in prev_files:
            if filename not in current_files:
                changes["deleted"].append(filename)

        self.metadata["files"] = current_files
        self.metadata["last_sync"] = datetime.now().isoformat()
        self._save_metadata()

        return changes
