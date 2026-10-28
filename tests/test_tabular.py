import subprocess
import sys


def test_tabular_imports_first_in_fresh_interpreter():
    """Раньше `import docflow.tabular` падал циклическим импортом через connectors."""
    done = subprocess.run([sys.executable, "-c", "import docflow.tabular"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
