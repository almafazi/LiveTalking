import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from manager import RuntimeSupervisor


class RuntimeManagerSafetyTest(unittest.TestCase):
    def test_rejects_unsafe_avatar_id(self):
        with self.assertRaises(ValueError):
            RuntimeSupervisor._safe_identifier("../../escape")

    def test_rejects_archive_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "bad.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                payload = b"bad"
                entry = tarfile.TarInfo("../escape.txt")
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
            with self.assertRaises(ValueError):
                RuntimeSupervisor._safe_extract(archive_path, Path(temp) / "out")


if __name__ == "__main__":
    unittest.main()
