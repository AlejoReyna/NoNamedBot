import json
import time
from pathlib import Path

def get_cache(filename):
    p = Path(filename)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except:
            pass
    return {}

def save_cache(filename, data):
    Path(filename).write_text(json.dumps(data))
