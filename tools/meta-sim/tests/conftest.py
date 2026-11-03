import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))        # `metasim` lives next to the tests; nothing of the product is on the path
