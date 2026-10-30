"""QQ attachment boundary; imports remain governed by existing quality rules."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
spec=importlib.util.spec_from_file_location('qq_size_worker',Path(__file__).resolve().parents[1]/'plugins/danbooru-archive/worker.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)

class QqSizeTest(unittest.TestCase):
    def test_threshold_and_manual_import_exemption(self):
        with tempfile.TemporaryDirectory() as folder:
            arc=w.Archive(folder,object())
            try:
                for size,meta,expected in ((307199,{'groupId':'123'},'qq_image_below_300kb'),(307200,{'groupId':'123'},'decode_reached'),(1,{},'decode_reached')):
                    with patch.object(w,'read_source',return_value=b'x'*size),patch.object(w,'decode_image',side_effect=w.Rejected('decode_reached')):
                        result=arc.process({'id':'size-test','meta':meta})
                    self.assertEqual(result['reason'],expected)
                    self.assertEqual(result['status'],'rejected')
            finally:arc.close()
