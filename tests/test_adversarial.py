import io, os, sys, unittest, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
import adversarial


class AdversarialRuntime(unittest.TestCase):
    def test_no_wrong_decisive_answer_against_runtime(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            stats = adversarial.main()
        self.assertEqual(stats["wrong"], 0, buf.getvalue())
        self.assertGreater(stats["correct"], 50)


if __name__ == "__main__":
    unittest.main()
