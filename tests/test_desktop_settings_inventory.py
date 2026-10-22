import copy
import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location('desktop_contracts', Path(__file__).parents[1] / 'tool/check_desktop_contracts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class InventoryTests(unittest.TestCase):
    def test_registry_is_covered(self):
        expected = module.inventory()
        module.validate_inventory(expected, expected)
    def test_missing_and_extra_entries_fail(self):
        expected = module.inventory()
        for entries in (expected['settings'][:-1], expected['settings'] + [expected['settings'][0]]):
            actual = copy.deepcopy(expected)
            actual['settings'] = entries
            with self.assertRaises(ValueError):
                module.validate_inventory(actual, expected)
