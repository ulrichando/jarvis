#!/usr/bin/env python3
"""Auto-fix common JS→Python syntax issues in converted files."""

import os
import re
import py_compile
import sys

def fix_file(path: str) -> bool:
    """Attempt to fix common JS artifacts in a Python file. Returns True if fixed."""
    with open(path, 'r', errors='replace') as f:
        original = f.read()

    text = original

    # Remove 'export ' prefix
    text = re.sub(r'^export\s+(default\s+)?', '', text, flags=re.MULTILINE)

    # const/let/var declarations → plain assignment
    text = re.sub(r'^(\s*)const\s+', r'\1', text, flags=re.MULTILINE)
    text = re.sub(r'^(\s*)let\s+', r'\1', text, flags=re.MULTILINE)
    text = re.sub(r'^(\s*)var\s+', r'\1', text, flags=re.MULTILINE)

    # Remove 'declare ' prefix
    text = re.sub(r'^declare\s+', '', text, flags=re.MULTILINE)

    # === and !== → == and !=
    text = text.replace(' === ', ' == ')
    text = text.replace(' !== ', ' != ')

    # true/false/null → Python (only standalone words)
    text = re.sub(r'\btrue\b', 'True', text)
    text = re.sub(r'\bfalse\b', 'False', text)
    text = re.sub(r'\bnull\b', 'None', text)
    text = re.sub(r'\bundefined\b', 'None', text)

    # Remove trailing semicolons
    text = re.sub(r';(\s*$)', r'\1', text, flags=re.MULTILINE)

    # Simple arrow functions: (x) => expr → lambda x: expr (single line)
    text = re.sub(r'\(([^)]*)\)\s*=>\s*([^{}\n]+)$', r'lambda \1: \2', text, flags=re.MULTILINE)

    # Remove 'function ' keyword before names
    text = re.sub(r'^(\s*)function\s+(\w+)\s*\(', r'\1def \2(', text, flags=re.MULTILINE)

    # async function → async def
    text = re.sub(r'^(\s*)async\s+function\s+(\w+)\s*\(', r'\1async def \2(', text, flags=re.MULTILINE)

    # Remove lone closing braces that are JS block ends (not in strings/dicts)
    # Only remove } on a line by itself (possibly with whitespace)
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip lone closing braces (JS artifact)
        if stripped == '}' or stripped == '};' or stripped == '},':
            # Check if we're likely in a dict/list context
            # Look at previous non-empty line
            prev = ''
            for prev_line in reversed(cleaned_lines):
                if prev_line.strip():
                    prev = prev_line.strip()
                    break
            # If previous line ends with , or : or { it's likely a dict — keep it
            if prev.endswith((',', ':', '{', '[')):
                cleaned_lines.append(line)
            else:
                # Likely a JS block end — remove it
                continue
        # Skip lone opening braces (JS artifact for blocks)
        elif stripped == '{':
            prev = ''
            for prev_line in reversed(cleaned_lines):
                if prev_line.strip():
                    prev = prev_line.strip()
                    break
            # If previous line is a class/def/if/for/while, this is a JS block opener
            if any(prev.startswith(kw) for kw in ('class ', 'def ', 'async def ', 'if ', 'elif ', 'else', 'for ', 'while ', 'try', 'except', 'finally')):
                continue
            else:
                cleaned_lines.append(line)
        else:
            cleaned_lines.append(line)

    text = '\n'.join(cleaned_lines)

    # Remove TypeScript type annotations in function params: (x: string) → (x)
    # Be conservative — only simple cases
    text = re.sub(r': (string|number|boolean|any|void|never)\b', '', text)

    # ?? → or (not perfect but handles most cases)
    text = re.sub(r'\s*\?\?\s*', ' or ', text)

    # Optional chaining x?.y → (x.y if x else None) — simplified to x.y
    text = re.sub(r'(\w+)\?\.([\w(])', r'\1.\2', text)

    # Remove 'implements X' from class declarations
    text = re.sub(r'(\bclass\s+\w+)\s+implements\s+\w+', r'\1', text)

    # Remove 'extends X' → just class name (Python uses different inheritance syntax)
    # Only for simple cases
    text = re.sub(r'(\bclass\s+\w+)\s+extends\s+(\w+)', r'\1(\2)', text)

    # interface Foo { → class Foo: (dataclass pattern)
    text = re.sub(r'^(\s*)interface\s+(\w+)\s*\{', r'\1class \2:', text, flags=re.MULTILINE)

    # type Foo = → Foo = (type alias)
    text = re.sub(r'^(\s*)type\s+(\w+)\s*=', r'\1\2 =', text, flags=re.MULTILINE)

    # enum Foo { → class Foo(Enum):
    text = re.sub(r'^(\s*)enum\s+(\w+)\s*\{', r'\1class \2(Enum):', text, flags=re.MULTILINE)

    # Remove 'readonly ' modifier
    text = re.sub(r'\breadonly\s+', '', text)

    # Remove 'public '/'private '/'protected ' modifiers
    text = re.sub(r'\b(public|private|protected)\s+', '', text)

    # Remove 'static ' before methods (Python uses @staticmethod but that's more complex)
    # text = re.sub(r'\bstatic\s+', '', text)

    # Fix template literals: `hello ${name}` → f"hello {name}"
    def fix_template(m):
        s = m.group(0)
        s = s[1:-1]  # strip backticks
        s = s.replace('${', '{')
        return f'f"{s}"'
    text = re.sub(r'`[^`]*`', fix_template, text)

    if text != original:
        with open(path, 'w') as f:
            f.write(text)
        return True
    return False


def main():
    src_dir = 'src'
    broken_before = 0
    fixed = 0
    still_broken = 0

    # Find all broken files
    broken_files = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'node_modules', 'frontend', 'static', 'static-react')]
        for f in files:
            if f.endswith('.py') and not f.startswith('_convert'):
                path = os.path.join(root, f)
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError:
                    broken_files.append(path)
                    broken_before += 1

    print(f"Found {broken_before} broken files")

    # Fix each broken file
    for path in broken_files:
        fix_file(path)
        try:
            py_compile.compile(path, doraise=True)
            fixed += 1
        except py_compile.PyCompileError:
            still_broken += 1

    # Second pass — try again on still-broken files
    broken_files2 = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'node_modules', 'frontend', 'static', 'static-react')]
        for f in files:
            if f.endswith('.py') and not f.startswith('_convert'):
                path = os.path.join(root, f)
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError:
                    broken_files2.append(path)

    print(f"Fixed: {fixed}")
    print(f"Still broken after auto-fix: {len(broken_files2)}")

    if broken_files2:
        print("\nStill broken:")
        for p in broken_files2[:30]:
            print(f"  {p}")


if __name__ == '__main__':
    main()
