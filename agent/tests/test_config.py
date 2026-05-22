import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from agent.core.config import SecurityConfig, LMStudioConfig

class TestSecurityDefaults(unittest.TestCase):
    def test_internet_access_false(self):
        self.assertFalse(SecurityConfig().internet_access)
    def test_sandbox_on(self):
        self.assertTrue(SecurityConfig().sandbox_by_default)

class TestLMStudioDefaults(unittest.TestCase):
    def test_base_url_is_localhost(self):
        self.assertIn("localhost", LMStudioConfig().base_url)

if __name__ == "__main__":
    unittest.main(verbosity=2)