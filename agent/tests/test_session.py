import unittest
import asyncio
from unittest.mock import patch
from agent.core.session import manager

class TestSession(unittest.TestCase):
    @patch("agent.core.gateway.gateway.ask")
    def test_session_persistence(self, mock_ask):
        # 1. Setup a more realistic mock behavior
        async def side_effect(prompt, history=None, model=None, session_id="default"):
            if history is not None:
                history.append({"role": "user", "content": prompt})
            return "Hello there!"
        
        mock_ask.side_effect = side_effect
        
        # 2. Get the session
        sess = manager.get_or_create("test-user")
        sess.clear_history() # Ensure clean state
        
        # 3. Run the async process
        # Using asyncio.run() is the modern, non-deprecated way
        response = asyncio.run(sess.process_request("My name is Alex"))
        
        # 4. Assertions
        self.assertEqual(response, "Hello there!")
        self.assertEqual(len(sess.history), 1)
        self.assertEqual(sess.history[0]["content"], "My name is Alex")
        self.assertEqual(sess.history[0]["role"], "user")

if __name__ == "__main__":
    unittest.main()
