"""Replace with standard-library tests for the copied adapter."""

import unittest


class ExtensionScaffoldTests(unittest.TestCase):
    def test_scaffold_is_not_a_runtime_extension(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
