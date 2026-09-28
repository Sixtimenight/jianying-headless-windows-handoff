"""Approved previews must not open the source package to arbitrary binaries."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('check_package_media', ROOT / 'tools/check_package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
WORK = ROOT / 'work/public-media-tests'
WORK.mkdir(parents=True, exist_ok=True)


class PublicMedia(unittest.TestCase):
    def test_all_reviewed_bytes_match(self):
        for relative, (digest, _) in package.PUBLIC_MEDIA.items():
            self.assertEqual(package.verify_public_media(ROOT / relative, relative), digest)

    def test_modified_media_rejected(self):
        folder = Path(tempfile.mkdtemp(dir=WORK))
        relative = next(iter(package.PUBLIC_MEDIA))
        data = bytearray((ROOT / relative).read_bytes())
        data[-1] ^= 1
        path = folder / 'changed.png'
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            package.verify_public_media(path, relative)

    def test_unlisted_media_rejected(self):
        folder = Path(tempfile.mkdtemp(dir=WORK))
        (folder / 'private.mp4').write_bytes(b'not allowed')
        with patch.object(package, 'ROOT', folder), self.assertRaisesRegex(ValueError, 'Unexpected source type'):
            package.source_files()

    def test_symlink_rejected_even_with_valid_target(self):
        folder = Path(tempfile.mkdtemp(dir=WORK))
        relative = next(iter(package.PUBLIC_MEDIA))
        path = folder / 'linked.png'
        path.symlink_to(ROOT / relative)
        with self.assertRaisesRegex(ValueError, 'Nonregular public media'):
            package.verify_public_media(path, relative)


if __name__ == '__main__':
    unittest.main()
