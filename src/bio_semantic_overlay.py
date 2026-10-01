"""Import alias so ``import bio_semantic_overlay`` works with ``PYTHONPATH=src``.

The kernel itself is vendored verbatim at
``src/governed_stack/bio_semantic_overlay.py``. This alias exists so the
kernel's own test file (``tests/test_bio_semantic_overlay.py``, also verbatim)
runs unmodified. It binds the *same* module object, so enums, the executor and
config are shared rather than duplicated.
"""

import sys

from governed_stack import bio_semantic_overlay as _kernel

sys.modules[__name__] = _kernel
