import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
