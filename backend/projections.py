"""Only estimate explicitly simple species:s/p/d/f projection lists."""
import re


def body(text):
    match = re.fullmatch(r'\s*begin\s+projections\s*\n([\s\S]*?)\n\s*end\s+projections\s*', text, re.I)
    return match[1] if match else text


def simple_count(text, atoms, spinors=False):
    count, found = 0, False
    for line in body(text).splitlines():
        line = re.split(r'[!#]', line, maxsplit=1)[0].strip()
        if not line: continue
        match = re.fullmatch(r'([A-Z][a-z]?)\s*:\s*([spdf])', line)
        if not match or not any(atom['element'] == match[1] for atom in atoms): return None
        count += sum(atom['element'] == match[1] for atom in atoms) * {'s':1,'p':3,'d':5,'f':7}[match[2]]
        found = True
    return count * (2 if spinors else 1) if found else None
