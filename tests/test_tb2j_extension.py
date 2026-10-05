"""Include independent TB2J bundle acceptance in the workbench regression."""
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('tb2j_v2_tests',Path(__file__).resolve().parents[1]/'resources/extensions/tb2j/tests/test_v2.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
TB2JV2Tests=module.TB2JV2Tests
