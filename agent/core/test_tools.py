import unittest
import asyncio
from pathlib import Path
from agent.core.tool_registry import registry

class TestTools(unittest.TestCase):
    def setUp(self):
        self.test_file = Path.home() / "jarvis/logs/test_tool.txt"
        if self.test_file.exists(): self.test_file.unlink()

    def test_write_and_read(self):
        async def run():
            await registry.write_file(str(self.test_file), "Hello JARVIS")
            content = await registry.read_file(str(self.test_file))
            return content
        
        res = asyncio.run(run())
        self.assertEqual(res, "Hello JARVIS")

    def test_shell_output(self):
        async def run():
            return await registry.run_shell("echo 'working'")
        
        res = asyncio.run(run())
        self.assertIn("working", res)

if __name__ == "__main__":
    unittest.main()
