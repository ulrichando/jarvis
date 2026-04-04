#!/usr/bin/env python3
"""
Convert TypeScript/TSX files to Python.

Extracts business logic, types, interfaces, enums, constants, and functions.
Skips JSX rendering but preserves all business logic as Python functions/classes.
"""

import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Optional


def ts_type_to_python(ts_type: str) -> str:
    """Convert a TypeScript type annotation to Python type hint."""
    ts_type = ts_type.strip()
    if not ts_type:
        return 'Any'

    # Handle union types with undefined/null
    if '|' in ts_type:
        parts = [p.strip() for p in ts_type.split('|')]
        has_none = any(p in ('undefined', 'null') for p in parts)
        parts = [p for p in parts if p not in ('undefined', 'null')]
        if len(parts) == 0:
            return 'None'
        converted = [ts_type_to_python(p) for p in parts]
        inner = ' | '.join(converted) if len(converted) > 1 else converted[0]
        if has_none:
            return f'Optional[{inner}]'
        return inner

    # Basic type mappings
    type_map = {
        'string': 'str',
        'number': 'int | float',
        'boolean': 'bool',
        'void': 'None',
        'any': 'Any',
        'unknown': 'Any',
        'never': 'None',
        'object': 'dict',
        'undefined': 'None',
        'null': 'None',
        'Date': 'datetime',
        'Promise': 'Awaitable',
        'Record': 'dict',
        'Map': 'dict',
        'Set': 'set',
        'Uint8Array': 'bytes',
        'Buffer': 'bytes',
        'Error': 'Exception',
        'RegExp': 're.Pattern',
        'Function': 'Callable',
        'Symbol': 'str',
        'bigint': 'int',
        'React.ReactNode': 'Any',
        'React.ReactElement': 'Any',
        'JSX.Element': 'Any',
    }
    if ts_type in type_map:
        return type_map[ts_type]

    # Handle array types
    if ts_type.endswith('[]'):
        return f'list[{ts_type_to_python(ts_type[:-2])}]'

    # Handle Array<T>
    m = re.match(r'Array<(.+)>', ts_type)
    if m:
        return f'list[{ts_type_to_python(m.group(1))}]'

    # Handle Promise<T>
    m = re.match(r'Promise<(.+)>', ts_type)
    if m:
        return f'Awaitable[{ts_type_to_python(m.group(1))}]'

    # Handle Record<K,V>
    m = re.match(r'Record<(.+),\s*(.+)>', ts_type)
    if m:
        return f'dict[{ts_type_to_python(m.group(1))}, {ts_type_to_python(m.group(2))}]'

    # Handle Set<T>
    m = re.match(r'Set<(.+)>', ts_type)
    if m:
        return f'set[{ts_type_to_python(m.group(1))}]'

    # Handle Partial<T>
    m = re.match(r'Partial<(.+)>', ts_type)
    if m:
        return f'Optional[{ts_type_to_python(m.group(1))}]'

    # Handle Omit/Pick
    m = re.match(r'(?:Omit|Pick)<(.+?),', ts_type)
    if m:
        return ts_type_to_python(m.group(1))

    # Readonly
    m = re.match(r'Readonly<(.+)>', ts_type)
    if m:
        return ts_type_to_python(m.group(1))

    # String literal types
    if ts_type.startswith("'") or ts_type.startswith('"'):
        return f'Literal[{ts_type}]'

    # Numeric literal
    if re.match(r'^\d+$', ts_type):
        return f'Literal[{ts_type}]'

    return ts_type


def convert_value(val: str) -> str:
    """Convert a TypeScript value literal to Python."""
    val = val.strip()
    if val in ('undefined', 'null'):
        return 'None'
    if val == 'true':
        return 'True'
    if val == 'false':
        return 'False'
    if val == 'Infinity':
        return 'float("inf")'
    if val == '-Infinity':
        return 'float("-inf")'
    if val == 'NaN':
        return 'float("nan")'
    # Template literals
    if val.startswith('`') and val.endswith('`'):
        inner = val[1:-1]
        inner = re.sub(r'\$\{([^}]+)\}', r'{\1}', inner)
        return f'f"{inner}"'
    return val


def pythonize_expression(expr: str) -> str:
    """Convert common JS expressions to Python idioms."""
    e = expr

    # Template literals
    e = re.sub(r'`([^`]*)`', lambda m: 'f"' + re.sub(r'\$\{([^}]+)\}', r'{\1}', m.group(1)) + '"', e)

    # === / !==
    e = e.replace('===', '==')
    e = e.replace('!==', '!=')

    # && / ||
    e = e.replace(' && ', ' and ')
    e = e.replace(' || ', ' or ')

    # null / undefined
    e = re.sub(r'\bundefined\b', 'None', e)
    e = re.sub(r'\bnull\b', 'None', e)

    # true / false
    e = re.sub(r'\btrue\b', 'True', e)
    e = re.sub(r'\bfalse\b', 'False', e)

    # .length -> len()
    e = re.sub(r'([\w.]+)\.length\b', r'len(\1)', e)

    # .push(x) -> .append(x)
    e = re.sub(r'\.push\(', '.append(', e)

    # .includes(x) -> x in ...  (leave as method, Python lists have no .includes)
    # Actually just note it
    e = re.sub(r'(\w+)\.includes\(([^)]+)\)', r'\2 in \1', e)

    # .indexOf(x) !== -1 -> x in ...
    e = re.sub(r'(\w+)\.indexOf\(([^)]+)\)\s*!=\s*-1', r'\2 in \1', e)

    # .startsWith / .endsWith -> Python equivalents (same name!)
    # These are the same in Python, keep them.

    # .trim() -> .strip()
    e = e.replace('.trim()', '.strip()')

    # .toLowerCase() -> .lower()
    e = e.replace('.toLowerCase()', '.lower()')
    e = e.replace('.toUpperCase()', '.upper()')

    # .toString() -> str()
    e = re.sub(r'(\w+)\.toString\(\)', r'str(\1)', e)

    # .join(sep) -- JS: arr.join(sep), Python: sep.join(arr)
    m_join = re.search(r'([\w.]+)\.join\(([^)]*)\)', e)
    if m_join:
        arr = m_join.group(1)
        sep = m_join.group(2) or '""'
        e = e[:m_join.start()] + f'{sep}.join({arr})' + e[m_join.end():]

    # /regex/.test(x) -> re.match(r'regex', x)
    e = re.sub(r'/([^/]+)/\.test\((\w+)\)', r're.match(r"\1", \2)', e)

    # .slice(a, b) -> [a:b]
    e = re.sub(r'\.slice\((\w+),\s*(\w+)\)', r'[\1:\2]', e)
    e = re.sub(r'\.slice\((\w+)\)', r'[\1:]', e)

    # .find(x => ...) -> next((x for x in ... if ...), None) -- just comment it
    e = re.sub(r'([\w.]+)\.find\(([^)]+)\)', r'next((\2 for x in \1), None)  # .find()', e)
    # .filter(x => ...) -> [x for x in ... if ...]
    e = re.sub(r'([\w.]+)\.filter\(([^)]+)\)', r'[x for x in \1 if \2]  # .filter()', e)
    # .map(x => ...) -> [... for x in ...]
    e = re.sub(r'([\w.]+)\.map\(([^)]+)\)', r'[\2 for x in \1]  # .map()', e)
    # .forEach(x => ...) -> for x in ...: ...
    e = re.sub(r'([\w.]+)\.forEach\(([^)]+)\)', r'# for x in \1: \2  # .forEach()', e)

    # Array.isArray(x) -> isinstance(x, list)
    e = re.sub(r'Array\.isArray\((\w+)\)', r'isinstance(\1, list)', e)

    # Object.keys(x) -> list(x.keys())
    e = re.sub(r'Object\.keys\((\w+)\)', r'list(\1.keys())', e)

    # Object.values(x) -> list(x.values())
    e = re.sub(r'Object\.values\((\w+)\)', r'list(\1.values())', e)

    # Object.entries(x) -> list(x.items())
    e = re.sub(r'Object\.entries\((\w+)\)', r'list(\1.items())', e)

    # typeof x === 'string' -> isinstance(x, str)
    e = re.sub(r"typeof\s+(\w+)\s*==\s*'string'", r'isinstance(\1, str)', e)
    e = re.sub(r"typeof\s+(\w+)\s*==\s*'number'", r'isinstance(\1, (int, float))', e)
    e = re.sub(r"typeof\s+(\w+)\s*==\s*'boolean'", r'isinstance(\1, bool)', e)
    e = re.sub(r"typeof\s+(\w+)\s*==\s*'object'", r'isinstance(\1, dict)', e)

    # ?? -> or (nullish coalescing, approximation)
    e = e.replace(' ?? ', ' or ')

    # !expr -> not expr (negation, but not != or !==)
    e = re.sub(r'(?<![!=])!(\w)', r'not \1', e)
    e = re.sub(r'(?<![!=])!\(', r'not (', e)

    # Ternary: a ? b : c -> b if a else c
    m_tern = re.match(r'^(.+?)\s*\?\s*(.+?)\s*:\s*(.+)$', e)
    if m_tern and e.count('?') == 1 and e.count(':') == 1:
        cond = m_tern.group(1).strip()
        then = m_tern.group(2).strip()
        else_ = m_tern.group(3).strip()
        e = f'{then} if {cond} else {else_}'

    # console.log -> print
    e = re.sub(r'console\.(?:log|warn|error|info)\(', 'print(', e)

    # new Error(...) -> Exception(...)
    e = re.sub(r'\bnew\s+Error\(', 'Exception(', e)

    # Remove trailing semicolons
    e = e.rstrip(';')

    return e


def extract_interfaces_and_types(content: str) -> list[str]:
    """Extract TypeScript interfaces and type aliases, convert to dataclasses."""
    results = []

    # Match interfaces
    for m in re.finditer(
        r'(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+([\w,\s]+))?\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
        content, re.DOTALL
    ):
        name = m.group(1)
        extends = m.group(2)
        body = m.group(3)
        fields = parse_interface_fields(body)
        if not fields:
            continue

        lines = ['@dataclass']
        parent = f'({extends.strip()})' if extends else ''
        lines.append(f'class {name}{parent}:')
        # Sort: required fields first, optional fields after
        required = [(fn, ft, o, d) for fn, ft, o, d in fields if not o and not d]
        optional = [(fn, ft, o, d) for fn, ft, o, d in fields if o or d]
        for fname, ftype, opt, default in required:
            py_type = ts_type_to_python(ftype)
            lines.append(f'    {fname}: {py_type}')
        for fname, ftype, opt, default in optional:
            py_type = ts_type_to_python(ftype)
            if opt:
                py_type = f'Optional[{py_type}]'
            if default:
                lines.append(f'    {fname}: {py_type} = {convert_value(default)}')
            else:
                lines.append(f'    {fname}: {py_type} = None')
        results.append('\n'.join(lines))

    # Match simple type aliases: type Foo = { ... }
    for m in re.finditer(
        r'(?:export\s+)?type\s+(\w+)\s*(?:<[^>]+>)?\s*=\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
        content, re.DOTALL
    ):
        name = m.group(1)
        body = m.group(2)
        fields = parse_interface_fields(body)
        if not fields:
            continue

        lines = ['@dataclass']
        lines.append(f'class {name}:')
        required = [(fn, ft, o, d) for fn, ft, o, d in fields if not o and not d]
        optional = [(fn, ft, o, d) for fn, ft, o, d in fields if o or d]
        for fname, ftype, opt, default in required:
            py_type = ts_type_to_python(ftype)
            lines.append(f'    {fname}: {py_type}')
        for fname, ftype, opt, default in optional:
            py_type = ts_type_to_python(ftype)
            if opt:
                py_type = f'Optional[{py_type}]'
            if default:
                lines.append(f'    {fname}: {py_type} = {convert_value(default)}')
            else:
                lines.append(f'    {fname}: {py_type} = None')
        results.append('\n'.join(lines))

    # Match union string literal type aliases
    for m in re.finditer(
        r"(?:export\s+)?type\s+(\w+)\s*=\s*((?:'[^']*'|\"[^\"]*\")\s*(?:\|\s*(?:'[^']*'|\"[^\"]*\"))+)\s*;?$",
        content, re.MULTILINE
    ):
        name = m.group(1)
        union = m.group(2)
        parts = [p.strip() for p in union.split('|')]
        parts_str = ', '.join(parts)
        results.append(f'{name} = Literal[{parts_str}]')

    return results


def parse_interface_fields(body: str) -> list[tuple[str, str, bool, Optional[str]]]:
    """Parse interface/type body into (name, type, optional, default) tuples."""
    fields = []
    body = re.sub(r'//[^\n]*', '', body)
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.DOTALL)

    for line in body.split('\n'):
        line = line.strip().rstrip(',').rstrip(';')
        if not line or line.startswith('//') or line.startswith('/*'):
            continue
        m = re.match(r'(?:readonly\s+)?(\w+)(\?)?:\s*(.+)', line)
        if m:
            fname = m.group(1)
            optional = m.group(2) == '?'
            ftype = m.group(3).strip().rstrip(',').rstrip(';')
            fields.append((fname, ftype, optional, None))
    return fields


def extract_enums(content: str) -> list[str]:
    """Extract TypeScript enums and convert to Python."""
    results = []
    for m in re.finditer(
        r'(?:export\s+)?(?:const\s+)?enum\s+(\w+)\s*\{([^}]+)\}', content
    ):
        name = m.group(1)
        body = m.group(2)
        members = []
        for line in body.split('\n'):
            line = line.strip().rstrip(',')
            if not line or line.startswith('//'):
                continue
            if '=' in line:
                mname, mval = line.split('=', 1)
                members.append(f'    {mname.strip()} = {convert_value(mval.strip())}')
            else:
                members.append(f'    {line} = auto()')
        if members:
            results.append(f'class {name}(Enum):\n' + '\n'.join(members))
    return results


def extract_constants(content: str) -> list[str]:
    """Extract const declarations."""
    results = []

    # Simple scalar constants
    for m in re.finditer(
        r"(?:export\s+)?const\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(['\"][^'\"]*['\"]|\d+|true|false|null|undefined)\s*;?$",
        content, re.MULTILINE
    ):
        name = m.group(1)
        val = convert_value(m.group(2))
        results.append(f'{name} = {val}')

    # Object constants
    for m in re.finditer(
        r"(?:export\s+)?const\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*\{([^}]+)\}\s*(?:as\s+const)?\s*;?$",
        content, re.MULTILINE
    ):
        name = m.group(1)
        body = m.group(2)
        pairs = []
        for line in body.split('\n'):
            line = line.strip().rstrip(',')
            if not line or line.startswith('//'):
                continue
            if ':' in line:
                k, v = line.split(':', 1)
                pairs.append(f'    {k.strip()!r}: {convert_value(v.strip())},')
        if pairs:
            results.append(f'{name} = {{\n' + '\n'.join(pairs) + '\n}')

    return results


def extract_braced_block(content: str, start: int) -> Optional[str]:
    """Extract content between matching braces starting at position start."""
    if start >= len(content) or content[start] != '{':
        return None
    depth = 0
    i = start
    while i < len(content):
        ch = content[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return content[start + 1:i]
        elif ch in ('"', "'", '`'):
            quote = ch
            i += 1
            while i < len(content) and content[i] != quote:
                if content[i] == '\\':
                    i += 1
                i += 1
        elif ch == '/' and i + 1 < len(content):
            if content[i + 1] == '/':
                while i < len(content) and content[i] != '\n':
                    i += 1
            elif content[i + 1] == '*':
                i += 2
                while i < len(content) - 1 and not (content[i] == '*' and content[i + 1] == '/'):
                    i += 1
                i += 1
        i += 1
    return None


def convert_params(params_str: str) -> str:
    """Convert TypeScript function parameters to Python."""
    if not params_str.strip():
        return ''

    params_str = params_str.strip()

    # Handle destructured params like { a, b, c }: Props
    if params_str.startswith('{'):
        m = re.match(r'\{([^}]+)\}\s*(?::\s*\w+)?', params_str)
        if m:
            fields = []
            for f in m.group(1).split(','):
                f = f.strip()
                if not f:
                    continue
                # Handle renaming: original: alias
                if ':' in f and not f.startswith("'"):
                    parts = f.split(':')
                    f = parts[1].strip().split('=')[0].strip()
                else:
                    f = f.split('=')[0].strip()
                fields.append(f)
            return ', '.join(fields)

    params = []
    depth = 0
    current = ''
    for ch in params_str:
        if ch in ('<', '(', '{', '['):
            depth += 1
            current += ch
        elif ch in ('>', ')', '}', ']'):
            depth -= 1
            current += ch
        elif ch == ',' and depth == 0:
            params.append(current.strip())
            current = ''
        else:
            current += ch
    if current.strip():
        params.append(current.strip())

    py_params = []
    for p in params:
        p = p.strip()
        if not p:
            continue

        default = None
        if '=' in p:
            parts = p.split('=', 1)
            p = parts[0].strip()
            default = convert_value(parts[1].strip())

        m = re.match(r'(\w+)(\?)?(?:\s*:\s*(.+))?', p)
        if m:
            pname = m.group(1)
            optional = m.group(2) == '?'
            ptype = m.group(3)

            if ptype:
                py_type = ts_type_to_python(ptype.strip())
                if optional:
                    py_type = f'Optional[{py_type}]'
                if default:
                    py_params.append(f'{pname}: {py_type} = {default}')
                elif optional:
                    py_params.append(f'{pname}: {py_type} = None')
                else:
                    py_params.append(f'{pname}: {py_type}')
            else:
                if default:
                    py_params.append(f'{pname}={default}')
                else:
                    py_params.append(pname)

    return ', '.join(py_params)


def convert_function_body(body: str) -> str:
    """Convert a TypeScript function body to Python."""
    lines = body.split('\n')
    py_lines = []
    in_jsx_return = False

    # Determine base indentation of the TS body
    base_indent = None
    for line in lines:
        stripped = line.strip()
        if stripped:
            raw = len(line) - len(line.lstrip())
            if base_indent is None or raw < base_indent:
                base_indent = raw
    if base_indent is None:
        base_indent = 0

    for line in lines:
        stripped = line.strip()

        if not stripped:
            py_lines.append('')
            continue

        # Skip React compiler runtime
        if '_c(' in stripped or stripped.startswith('const $ = ') or re.match(r'^\$\[', stripped):
            continue
        if 'sourceMappingURL' in stripped:
            continue
        if stripped.startswith('import '):
            continue
        if stripped.startswith('export {') or stripped.startswith('export *'):
            continue

        # Skip React compiler runtime variables and patterns
        if re.match(r'^(?:let|const)\s+t\d+\s*$', stripped):
            continue
        if '$[' in stripped and ('!=' in stripped or '=' in stripped):
            continue
        if re.match(r'^const\s+\{$', stripped):  # destructuring start
            continue
        if re.match(r'^(?:let|const)\s+\{', stripped) and '=' not in stripped:
            continue
        if re.match(r'^\w+(?:,)?$', stripped) and len(stripped) < 30:
            # Bare variable names in destructuring
            if any(c.isalpha() for c in stripped.rstrip(',')):
                pass  # will be handled by convert_statement
        if stripped.startswith('} = t0') or stripped == '} = t0':
            continue

        # Detect JSX return blocks - skip them
        if re.match(r'return\s*\(?\s*<', stripped) or (stripped == 'return (' and not in_jsx_return):
            in_jsx_return = True
            py_lines.append('    return None  # JSX rendering omitted')
            continue
        if in_jsx_return:
            if stripped == ')' or stripped == ');':
                in_jsx_return = False
            continue

        # Skip standalone JSX lines
        if re.match(r'^<[A-Z/]', stripped) and not any(kw in stripped for kw in ['const ', 'let ', 'var ', 'return ', 'if ', '=']):
            continue
        if stripped.startswith('<>') or stripped.startswith('</>'):
            continue

        # Calculate relative indentation
        raw_indent = len(line) - len(line.lstrip())
        relative_indent = max(0, raw_indent - base_indent)
        # Convert 2-space indent to 4-space, then add 1 level for function body
        py_indent = '    ' + '    ' * (relative_indent // 2)

        converted = convert_statement(stripped)
        if converted is not None:
            # Handle multi-line conversions (e.g., single-line if)
            for subline in converted.split('\n'):
                py_lines.append(f'{py_indent}{subline}')

    # Remove leading/trailing blank lines but preserve indentation
    while py_lines and not py_lines[0].strip():
        py_lines.pop(0)
    while py_lines and not py_lines[-1].strip():
        py_lines.pop()

    if not py_lines:
        return '    pass'

    return '\n'.join(py_lines)


def convert_statement(line: str) -> Optional[str]:
    """Convert a single TypeScript statement to Python."""
    line = line.strip()
    if not line:
        return ''

    # Skip type-only lines
    if line.startswith('type ') and '=' in line:
        return None

    # Closing braces
    if line in ('}', '};', '},'):
        return None

    # Comments
    if line.startswith('//'):
        return f'# {line[2:].strip()}'
    if line.startswith('/*') or line.startswith('*') or line.startswith('*/'):
        comment_text = line.lstrip('/*').rstrip('*/').strip()
        return f'# {comment_text}' if comment_text else None

    # const/let/var declarations
    m = re.match(r'(?:export\s+)?(?:const|let|var)\s+(\[?\w[\w,\s\[\]]*\]?)\s*(?::\s*(?:[^=]+?))?\s*=\s*(.+?)(?:;?)$', line)
    if m:
        name = m.group(1).strip()
        val = pythonize_expression(m.group(2).rstrip(';').strip())
        return f'{name} = {val}'

    # return
    if line.startswith('return '):
        val = line[7:].rstrip(';').strip()
        if val.startswith('<') or val.startswith('(') and '<' in val:
            return 'return None  # JSX omitted'
        return f'return {pythonize_expression(val)}'
    if line == 'return' or line == 'return;':
        return 'return'

    # if / else if / else
    m = re.match(r'if\s*\((.+)\)\s*\{?\s*$', line)
    if m:
        cond = pythonize_expression(m.group(1))
        return f'if {cond}:'

    # Single-line if: if (cond) return val;
    m = re.match(r'if\s*\((.+?)\)\s*return\s+(.+);?$', line)
    if m:
        cond = pythonize_expression(m.group(1))
        val = pythonize_expression(m.group(2).rstrip(';'))
        return f'if {cond}:\n    return {val}'

    # Single-line if with other statement
    m = re.match(r'if\s*\((.+?)\)\s+(?!return)(.+);?$', line)
    if m and '{' not in line:
        cond = pythonize_expression(m.group(1))
        stmt = pythonize_expression(m.group(2).rstrip(';'))
        return f'if {cond}:\n    {stmt}'

    m = re.match(r'\}\s*else\s+if\s*\((.+)\)\s*\{?\s*$', line)
    if m:
        return f'elif {pythonize_expression(m.group(1))}:'

    if re.match(r'\}\s*else\s*\{?\s*$', line):
        return 'else:'

    # switch/case -> match/case (Python 3.10+)
    m = re.match(r'switch\s*\((.+?)\)\s*\{?\s*$', line)
    if m:
        return f'match {pythonize_expression(m.group(1))}:'

    m = re.match(r"case\s+(.+?)\s*:\s*$", line)
    if m:
        val = convert_value(m.group(1).strip())
        return f'case {val}:'

    if line.startswith('default:'):
        return 'case _:'

    if line == 'break' or line == 'break;':
        return 'pass  # break'

    # for loops
    m = re.match(r'for\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\s+(.+?)\)\s*\{?\s*$', line)
    if m:
        return f'for {m.group(1)} in {pythonize_expression(m.group(2))}:'

    m = re.match(r'for\s*\(\s*(?:const|let|var)\s+(\w+)\s+in\s+(.+?)\)\s*\{?\s*$', line)
    if m:
        return f'for {m.group(1)} in {pythonize_expression(m.group(2))}:'

    # C-style for loop: for (let i = 0; i < n; i++)
    m = re.match(r'for\s*\(\s*(?:let|var)\s+(\w+)\s*=\s*(\d+)\s*;\s*\w+\s*<\s*(.+?)\s*;\s*\w+\+\+\s*\)\s*\{?\s*$', line)
    if m:
        return f'for {m.group(1)} in range({m.group(2)}, {pythonize_expression(m.group(3))}):'

    # while
    m = re.match(r'while\s*\((.+)\)\s*\{?\s*$', line)
    if m:
        return f'while {pythonize_expression(m.group(1))}:'

    # try/catch/finally
    if line.startswith('try') and '{' in line:
        return 'try:'
    m = re.match(r'\}\s*catch\s*\((\w+)(?:\s*:\s*\w+)?\)\s*\{?\s*$', line)
    if m:
        return f'except Exception as {m.group(1)}:'
    if re.match(r'\}\s*catch\s*\{', line):
        return 'except Exception:'
    if re.match(r'\}\s*finally\s*\{', line):
        return 'finally:'

    # throw
    m = re.match(r'throw\s+new\s+(\w+)\((.+?)\)\s*;?$', line)
    if m:
        return f'raise {m.group(1)}({pythonize_expression(m.group(2))})'
    if line.startswith('throw '):
        return f'raise Exception({pythonize_expression(line[6:].rstrip(";"))})'

    # Arrow functions assigned to const (extract as def)
    m = re.match(r'(?:export\s+)?(?:const|let)\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s+)?\(([^)]*)\)\s*(?::\s*[^=]+?)?\s*=>\s*\{?', line)
    if m:
        fname = m.group(1)
        is_async = 'async' in line[:line.index('=>')]
        prefix = 'async ' if is_async else ''
        params = convert_params(m.group(2))
        return f'# Arrow function: {prefix}def {fname}({params}):'

    # Skip lines that are clearly part of a multiline ternary
    if re.match(r'^\s*\?\s+', line) or re.match(r'^\s*:\s+', line):
        return f'# {line}'

    # Skip "as" type assertions
    # e.g., "const x = foo as Bar"

    # Generic expressions
    result = pythonize_expression(line.rstrip(';'))

    # Skip if it looks like JSX
    if result.startswith('<') or result.endswith('/>') or result.endswith('>'):
        return None

    # Skip React compiler runtime patterns
    if re.match(r'^t\d+\s*=\s*', result) and ('$[' in result or '_c(' in result):
        return None

    return result


def extract_functions(content: str) -> list[str]:
    """Extract function declarations and convert to Python."""
    results = []

    # Match exported/non-exported functions
    pattern = r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)\s*(?::\s*([^{]+))?\s*\{'

    for m in re.finditer(pattern, content):
        func_name = m.group(1)
        params_str = m.group(2)
        return_type = m.group(3)

        start = m.end() - 1
        body = extract_braced_block(content, start)
        if body is None:
            continue

        # Check if this is a React component (returns JSX)
        is_component = (
            func_name[0].isupper() and
            (re.search(r'return\s*\(?\s*<', body) or '<Box' in body or '<Text' in body)
        )

        is_async = 'async ' in content[max(0, m.start()-10):m.start()+20]

        py_params = convert_params(params_str)

        py_return = ''
        if return_type:
            rt = return_type.strip()
            if rt not in ('React.ReactNode', 'JSX.Element', 'ReactNode'):
                py_return = f' -> {ts_type_to_python(rt)}'

        py_body = convert_function_body(body)
        if not py_body.strip():
            py_body = '    pass'

        prefix = 'async ' if is_async else ''

        # Add docstring for components
        if is_component:
            docstring = f'    """React component {func_name} - UI rendering logic omitted."""\n'
            results.append(f'{prefix}def {func_name}({py_params}){py_return}:\n{docstring}{py_body}')
        else:
            results.append(f'{prefix}def {func_name}({py_params}){py_return}:\n{py_body}')

    return results


def extract_react_component_logic(content: str) -> list[str]:
    """Extract business logic from React components."""
    results = []

    # Extract useState
    for m in re.finditer(r'const\s+\[(\w+),\s*(\w+)\]\s*=\s*(?:React\.)?useState(?:<[^>]+>)?\(([^)]*)\)', content):
        state_name = m.group(1)
        setter = m.group(2)
        initial = convert_value(m.group(3)) if m.group(3) else 'None'
        results.append(f'# State: {state_name} = {initial}  (setter: {setter})')

    # Extract useRef
    for m in re.finditer(r'const\s+(\w+)\s*=\s*(?:React\.)?useRef(?:<[^>]+>)?\(([^)]*)\)', content):
        ref_name = m.group(1)
        initial = convert_value(m.group(2)) if m.group(2) else 'None'
        results.append(f'# Ref: {ref_name} = {initial}')

    # Extract useCallback/useMemo
    for m in re.finditer(r'const\s+(\w+)\s*=\s*(?:React\.)?(?:useCallback|useMemo)\(', content):
        results.append(f'# Memoized: {m.group(1)}')

    return results


def convert_file(filepath: str) -> str:
    """Convert a TypeScript/TSX file to Python."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove source map comments
    content = re.sub(r'//# sourceMappingURL=.*$', '', content, flags=re.MULTILINE)

    parts = []
    imports_needed = set()

    filename = os.path.basename(filepath)

    parts.append(f'"""')
    parts.append(f'Converted from {filename}')
    parts.append(f'Business logic extracted from TypeScript/TSX source.')
    parts.append(f'"""')
    parts.append('')

    # Determine needed imports based on content
    has_types = 'interface ' in content or re.search(r'\btype\s+\w+\s*=\s*\{', content)
    has_optional = '?' in content or 'undefined' in content or 'null' in content
    has_enum = re.search(r'\benum\s+', content)
    has_async = 'async ' in content
    has_path = 'path' in content.lower() and ('join(' in content or 'dirname' in content)
    has_re = re.search(r'\.test\(|\.match\(|RegExp|/[^/]+/[gim]*', content)
    has_json = 'JSON.' in content or 'json' in content.lower()

    if has_types:
        imports_needed.add('from dataclasses import dataclass, field')
    if has_optional:
        imports_needed.add('from typing import Optional, Any, Literal, Callable, Awaitable')
    else:
        imports_needed.add('from typing import Any, Literal, Callable, Awaitable')
    if has_enum:
        imports_needed.add('from enum import Enum, auto')
    if has_re:
        imports_needed.add('import re')
    if has_async:
        imports_needed.add('import asyncio')
    if has_path:
        imports_needed.add('from pathlib import Path')
        imports_needed.add('import os')
    if has_json:
        imports_needed.add('import json')

    for imp in sorted(imports_needed):
        parts.append(imp)
    parts.append('')
    parts.append('')

    # Extract enums
    enums = extract_enums(content)
    for e in enums:
        parts.append(e)
        parts.append('')
        parts.append('')

    # Extract interfaces/types
    types = extract_interfaces_and_types(content)
    for t in types:
        parts.append(t)
        parts.append('')
        parts.append('')

    # Extract constants
    constants = extract_constants(content)
    for c in constants:
        parts.append(c)
    if constants:
        parts.append('')
        parts.append('')

    # Extract React component logic
    if filepath.endswith('.tsx'):
        component_logic = extract_react_component_logic(content)
        if component_logic:
            for cl in component_logic:
                parts.append(cl)
            parts.append('')

    # Extract functions
    functions = extract_functions(content)
    for f in functions:
        parts.append(f)
        parts.append('')
        parts.append('')

    result = '\n'.join(parts).rstrip()
    if len(result.split('\n')) <= 8:
        parts.append('# This file primarily contained JSX/UI rendering logic.')
        parts.append('# No significant business logic to extract.')
        parts.append('')
        result = '\n'.join(parts).rstrip()

    return result + '\n'


def process_directory(root_dir: str, force: bool = False) -> tuple[int, int]:
    """Process all .ts/.tsx files in the directory tree."""
    converted = 0
    skipped = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        for filename in sorted(filenames):
            if not (filename.endswith('.ts') or filename.endswith('.tsx')):
                continue

            filepath = os.path.join(dirpath, filename)
            py_filename = os.path.splitext(filename)[0] + '.py'
            py_filepath = os.path.join(dirpath, py_filename)

            if os.path.exists(py_filepath) and not force:
                skipped += 1
                continue

            try:
                py_content = convert_file(filepath)
                with open(py_filepath, 'w', encoding='utf-8') as f:
                    f.write(py_content)
                converted += 1
                print(f'  Converted: {os.path.relpath(filepath, root_dir)}')
            except Exception as e:
                print(f'  ERROR: {os.path.relpath(filepath, root_dir)}: {e}')
                skipped += 1

    return converted, skipped


if __name__ == '__main__':
    root = '/home/ulrich/Documents/Projects/jarvis/src/components'
    force = '--force' in sys.argv
    print(f'Converting TypeScript files in {root}...')
    if force:
        print('Force mode: overwriting existing .py files')
    converted, skipped = process_directory(root, force=force)
    print(f'\nDone: {converted} converted, {skipped} skipped')
