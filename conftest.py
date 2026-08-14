"""Test bootstrap.

The repo root carries an `__init__.py` because ComfyUI imports a custom node pack as a
package. pytest would otherwise try to collect that file as a test module and choke on its
relative imports, so it is ignored here and the root is put on sys.path instead.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

collect_ignore = ["__init__.py"]
