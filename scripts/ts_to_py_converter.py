#!/usr/bin/env python3
"""
Converts TypeScript/TSX files to idiomatic Python equivalents.
Handles: types, interfaces, classes, async/await, imports, JSX stripping, etc.
"""

import os
import re
import sys
from pathlib import Path


def convert_ts_to_py(ts_content: str, filename: str) -> str:
    """Convert TypeScript content to Python."""
    lines = ts_content.split('\n')

    # Remove sourcemap comments
    lines = [l for l in lines if not l.startswith('//# sourceMappingURL=')]

    content = '\n'.join(lines)

    is_tsx = filename.endswith('.tsx')

    # Track what we need to import
    needs_dataclass = False
    needs_typed_dict = False
    needs_enum = False
    needs_optional = False
    needs_any = False
    needs_callable = False
    needs_union = False
    needs_literal = False
    needs_abc = False

    # ---- Phase 1: Remove/transform imports ----
    import_lines = []
    other_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('import{') or stripped.startswith('import('):
            # Convert to Python imports where possible
            py_import = convert_import(stripped)
            if py_import:
                import_lines.append(py_import)
        elif stripped.startswith('export { ') or stripped.startswith('export {'):
            # Skip re-exports for now, handled by __all__
            pass
        else:
            other_lines.append(line)

    content = '\n'.join(other_lines)

    # ---- Phase 2: Strip JSX (for .tsx files) ----
    if is_tsx:
        content = strip_jsx_components(content)

    # ---- Phase 3: Convert TypeScript constructs ----

    # Remove 'export default'
    content = re.sub(r'\bexport\s+default\s+', '', content)

    # Convert interfaces to TypedDict or dataclass
    content, td_count = convert_interfaces(content)
    if td_count > 0:
        needs_typed_dict = True

    # Convert type aliases
    content, needs = convert_type_aliases(content)
    if 'Literal' in needs:
        needs_literal = True
    if 'Union' in needs:
        needs_union = True
    if 'TypedDict' in needs:
        needs_typed_dict = True

    # Convert enums
    content, enum_count = convert_enums(content)
    if enum_count > 0:
        needs_enum = True

    # Convert classes
    content = convert_classes(content)

    # Convert functions (export function, function, arrow functions)
    content = convert_functions(content)

    # Convert const/let/var declarations
    content = convert_variables(content)

    # Convert basic type annotations inline
    content = convert_type_annotations(content)

    # Convert common patterns
    content = convert_common_patterns(content)

    # Convert template literals to f-strings
    content = convert_template_literals(content)

    # Convert null/undefined to None
    content = convert_null_undefined(content)

    # Convert boolean literals
    content = re.sub(r'\btrue\b', 'True', content)
    content = re.sub(r'\bfalse\b', 'False', content)

    # Convert logical operators
    content = re.sub(r'\s&&\s', ' and ', content)
    content = re.sub(r'\s\|\|\s', ' or ', content)
    content = re.sub(r'(?<!\w)!(?!=)(?!\.)', 'not ', content)

    # Convert common JS methods
    content = convert_js_methods(content)

    # Convert throw to raise
    content = re.sub(r'\bthrow\s+new\s+(\w+)\(', r'raise \1(', content)
    content = re.sub(r'\bthrow\s+(\w+)', r'raise \1', content)

    # Convert try/catch to try/except
    content = re.sub(r'\bcatch\s*\((\w+)(?:\s*:\s*\w+)?\)\s*\{', r'except Exception as \1:', content)
    content = re.sub(r'\bcatch\s*\{', 'except Exception:', content)
    content = re.sub(r'\bfinally\s*\{', 'finally:', content)

    # Remove remaining type assertions
    content = re.sub(r'\bas\s+\w+(?:<[^>]+>)?', '', content)
    content = re.sub(r'<\w+(?:<[^>]+>)?>', '', content)  # generic type params in expressions

    # Remove semicolons at end of lines
    content = re.sub(r';(\s*$)', r'\1', content, flags=re.MULTILINE)

    # Convert braces to pass/colons (simplified)
    content = convert_braces(content)

    # Remove 'export ' keyword
    content = re.sub(r'\bexport\s+', '', content)

    # Clean up
    content = cleanup(content)

    # ---- Phase 4: Build header ----
    header_lines = ['"""']
    header_lines.append(f'Converted from {filename}')
    header_lines.append('"""')
    header_lines.append('')

    # Add Python imports
    typing_imports = []
    if needs_typed_dict:
        typing_imports.append('TypedDict')
    if needs_optional:
        typing_imports.append('Optional')
    if needs_any:
        typing_imports.append('Any')
    if needs_callable:
        typing_imports.append('Callable')
    if needs_union:
        typing_imports.append('Union')
    if needs_literal:
        typing_imports.append('Literal')

    # Scan content for typing needs
    if 'Optional[' in content:
        typing_imports.append('Optional')
    if 'Any' in content and 'Any' not in typing_imports:
        typing_imports.append('Any')
    if 'Callable[' in content:
        typing_imports.append('Callable')
    if 'Union[' in content and 'Union' not in typing_imports:
        typing_imports.append('Union')
    if 'Literal[' in content and 'Literal' not in typing_imports:
        typing_imports.append('Literal')
    if 'TypedDict' in content and 'TypedDict' not in typing_imports:
        typing_imports.append('TypedDict')

    typing_imports = sorted(set(typing_imports))

    std_imports = []
    if needs_dataclass or '@dataclass' in content:
        std_imports.append('from dataclasses import dataclass, field')
    if needs_enum or 'class ' in content and '(Enum)' in content:
        std_imports.append('from enum import Enum, auto')
    if 'asyncio' in content or 'async def' in content:
        std_imports.append('import asyncio')
    if re.search(r'\bos\.', content):
        std_imports.append('import os')
    if re.search(r'\bPath\b', content):
        std_imports.append('from pathlib import Path')
    if re.search(r'\bjson\.', content):
        std_imports.append('import json')
    if re.search(r'\bre\.', content):
        std_imports.append('import re')
    if re.search(r'\bhashlib\.', content):
        std_imports.append('import hashlib')
    if re.search(r'\btime\.', content) or re.search(r'\btime\(', content):
        std_imports.append('import time')
    if re.search(r'\bdatetime', content):
        std_imports.append('from datetime import datetime')
    if re.search(r'\bsubprocess', content):
        std_imports.append('import subprocess')

    if typing_imports:
        header_lines.append(f'from typing import {", ".join(typing_imports)}')
    if std_imports:
        for imp in sorted(set(std_imports)):
            header_lines.append(imp)

    # Add converted imports
    if import_lines:
        header_lines.append('')
        for imp in import_lines:
            header_lines.append(imp)

    header_lines.append('')
    header_lines.append('')

    result = '\n'.join(header_lines) + content

    # Final cleanup: remove excessive blank lines
    result = re.sub(r'\n{4,}', '\n\n\n', result)

    # Ensure file ends with newline
    if not result.endswith('\n'):
        result += '\n'

    return result


def convert_import(line: str) -> str | None:
    """Convert a TS import to Python import or None if not applicable."""
    # Skip React and UI imports entirely
    if any(pkg in line for pkg in ['react', 'ink', 'chalk', '@anthropic', 'zod']):
        return None

    # import { X, Y } from './module'
    m = re.match(r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]", line)
    if m:
        names = [n.strip().split(' as ') for n in m.group(1).split(',')]
        module = m.group(2)
        # Convert path
        py_module = convert_module_path(module)
        if py_module is None:
            return None
        import_names = []
        for parts in names:
            if len(parts) == 2:
                import_names.append(f'{parts[0].strip()} as {parts[1].strip()}')
            else:
                import_names.append(parts[0].strip())
        return f'from {py_module} import {", ".join(import_names)}'

    # import X from './module'
    m = re.match(r"import\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"]", line)
    if m:
        name = m.group(1)
        module = m.group(2)
        py_module = convert_module_path(module)
        if py_module is None:
            return None
        return f'from {py_module} import {name}'

    # import * as X from './module'
    m = re.match(r"import\s*\*\s*as\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"]", line)
    if m:
        name = m.group(1)
        module = m.group(2)
        py_module = convert_module_path(module)
        if py_module is None:
            return None
        return f'import {py_module} as {name}'

    return None


def convert_module_path(module: str) -> str | None:
    """Convert a JS module path to Python module path."""
    # Skip node built-ins and npm packages
    if not module.startswith('.') and not module.startswith('/'):
        # It's an npm package - skip most
        if module in ('fs', 'path', 'os', 'child_process', 'crypto', 'events',
                       'stream', 'util', 'url', 'http', 'https', 'net', 'tty',
                       'readline', 'assert', 'buffer', 'worker_threads'):
            return None
        if module.startswith('@'):
            return None
        if '/' in module:
            return None
        return None  # Skip external packages

    # Relative import
    module = module.replace('.js', '').replace('.ts', '').replace('.tsx', '')
    module = module.replace('/', '.')
    if module.startswith('..'):
        module = module.replace('..', '', 1)
        # Count levels up
        levels = module.count('..')
        module = module.replace('..', '')
        module = '.' * (levels + 2) + module.lstrip('.')
    elif module.startswith('.'):
        pass  # Already relative

    return module


def strip_jsx_components(content: str) -> str:
    """Remove React component functions and JSX from .tsx files, keep logic."""
    # Remove JSX expressions (simplified - removes <Tag>...</Tag> and <Tag />)
    content = re.sub(r'<[A-Z]\w+[^>]*/>',  '"""JSX_REMOVED"""', content)
    content = re.sub(r'<[A-Z]\w+[^>]*>.*?</[A-Z]\w+>', '"""JSX_REMOVED"""', content, flags=re.DOTALL)
    content = re.sub(r'<[a-z]\w+[^>]*/>',  '"""JSX_REMOVED"""', content)

    # Remove React hook calls
    content = re.sub(r'\b(?:useState|useEffect|useRef|useMemo|useCallback|useContext)\s*\([^)]*\)', 'None', content)

    return content


def convert_interfaces(content: str) -> tuple[str, int]:
    """Convert TS interfaces to TypedDict classes."""
    count = 0

    def replace_interface(m):
        nonlocal count
        count += 1
        name = m.group(1)
        extends = m.group(2) or ''
        body = m.group(3)

        parent = ''
        if extends:
            parent_name = extends.strip().strip(':').strip()
            parent = f'({parent_name})'

        fields = convert_interface_body(body)

        if not fields:
            return f'class {name}{parent}(TypedDict):\n    pass\n'

        return f'class {name}(TypedDict):\n{fields}\n'

    # interface Name { ... }
    content = re.sub(
        r'(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+(\w+))?\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
        replace_interface,
        content
    )

    return content, count


def convert_interface_body(body: str) -> str:
    """Convert interface body to Python class fields."""
    lines = []
    for line in body.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('//') or line.startswith('/*') or line.startswith('*'):
            if line.startswith('//'):
                lines.append(f'    # {line[2:].strip()}')
            continue

        # property?: type
        m = re.match(r'(\w+)\??\s*:\s*(.+?)(?:;|$)', line)
        if m:
            name = m.group(1)
            ts_type = m.group(2).strip()
            py_type = convert_type(ts_type)
            optional = '?' in line.split(':')[0]
            if optional:
                py_type = f'Optional[{py_type}]'
            lines.append(f'    {name}: {py_type}')

    return '\n'.join(lines)


def convert_type_aliases(content: str) -> tuple[str, set]:
    """Convert TS type aliases."""
    needs = set()

    def replace_type_alias(m):
        name = m.group(1)
        generic = m.group(2) or ''
        value = m.group(3).strip().rstrip(';')

        # Union of string literals -> Literal type
        if re.match(r"^'[^']*'(\s*\|\s*'[^']*')+$", value):
            literals = re.findall(r"'([^']*)'", value)
            needs.add('Literal')
            return f'{name} = Literal[{", ".join(repr(l) for l in literals)}]'

        # Simple union -> Union
        if '|' in value and not value.startswith('{'):
            parts = [convert_type(p.strip()) for p in value.split('|')]
            needs.add('Union')
            return f'{name} = Union[{", ".join(parts)}]'

        # Object type -> TypedDict
        if value.startswith('{') and value.endswith('}'):
            needs.add('TypedDict')
            fields = convert_interface_body(value[1:-1])
            if not fields:
                return f'class {name}(TypedDict):\n    pass'
            return f'class {name}(TypedDict):\n{fields}'

        py_type = convert_type(value)
        return f'{name} = {py_type}'

    content = re.sub(
        r'(?:export\s+)?type\s+(\w+)(<[^>]+>)?\s*=\s*(.+?)(?:;\s*$|\n)',
        replace_type_alias,
        content,
        flags=re.MULTILINE
    )

    return content, needs


def convert_enums(content: str) -> tuple[str, int]:
    """Convert TS enums to Python Enum classes."""
    count = 0

    def replace_enum(m):
        nonlocal count
        count += 1
        name = m.group(1)
        body = m.group(2)

        members = []
        for line in body.strip().split('\n'):
            line = line.strip().rstrip(',')
            if not line or line.startswith('//'):
                continue
            if '=' in line:
                mname, mval = line.split('=', 1)
                members.append(f'    {mname.strip()} = {mval.strip()}')
            else:
                members.append(f'    {line} = auto()')

        if not members:
            return f'class {name}(Enum):\n    pass\n'

        return f'class {name}(Enum):\n' + '\n'.join(members) + '\n'

    content = re.sub(
        r'(?:export\s+)?(?:const\s+)?enum\s+(\w+)\s*\{([^}]+)\}',
        replace_enum,
        content
    )

    return content, count


def convert_classes(content: str) -> str:
    """Convert TS classes to Python classes."""
    # class Name extends Parent { -> class Name(Parent):
    content = re.sub(
        r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:<[^>]+>)?\s+extends\s+(\w+)(?:<[^>]+>)?\s*\{',
        r'class \1(\2):',
        content
    )
    # class Name implements Interface { -> class Name:
    content = re.sub(
        r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:<[^>]+>)?\s+implements\s+\w+(?:<[^>]+>)?\s*\{',
        r'class \1:',
        content
    )
    # class Name { -> class Name:
    content = re.sub(
        r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:<[^>]+>)?\s*\{',
        r'class \1:',
        content
    )

    return content


def convert_functions(content: str) -> str:
    """Convert TS functions to Python functions."""

    # async function name(args): RetType { -> async def name(args) -> RetType:
    content = re.sub(
        r'(?:export\s+)?async\s+function\s+(\w+)(?:<[^>]+>)?\s*\(([^)]*)\)\s*(?::\s*Promise<([^>]+)>|:\s*(\w+))?\s*\{',
        lambda m: f'async def {m.group(1)}({convert_params(m.group(2))})' +
                  (f' -> {convert_type(m.group(3) or m.group(4))}' if m.group(3) or m.group(4) else '') + ':',
        content
    )

    # function name(args): RetType { -> def name(args) -> RetType:
    content = re.sub(
        r'(?:export\s+)?function\s+(\w+)(?:<[^>]+>)?\s*\(([^)]*)\)\s*(?::\s*(\w+(?:<[^>]+>)?))?\s*\{',
        lambda m: f'def {m.group(1)}({convert_params(m.group(2))})' +
                  (f' -> {convert_type(m.group(3))}' if m.group(3) else '') + ':',
        content
    )

    # Arrow functions: const name = (args) => { -> def name(args):
    content = re.sub(
        r'(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*(?::\s*\w+(?:<[^>]+>)?)?\s*=>\s*\{',
        lambda m: ('async ' if 'async' in content[max(0,m.start()-10):m.start()+5] else '') +
                  f'def {m.group(1)}({convert_params(m.group(2))}):',
        content
    )

    # Simple arrow functions (one-liner): const name = (args) => expr
    content = re.sub(
        r'(?:export\s+)?const\s+(\w+)\s*=\s*\(([^)]*)\)\s*(?::\s*\w+(?:<[^>]+>)?)?\s*=>\s*([^{;\n]+)',
        lambda m: f'def {m.group(1)}({convert_params(m.group(2))}): return {m.group(3).strip()}',
        content
    )

    return content


def convert_params(params: str) -> str:
    """Convert function parameters from TS to Python."""
    if not params or not params.strip():
        return ''

    result = []
    # Split on commas, but not within angle brackets or parens
    depth = 0
    current = ''
    for ch in params:
        if ch in '<(':
            depth += 1
        elif ch in '>)':
            depth -= 1
        elif ch == ',' and depth == 0:
            result.append(current.strip())
            current = ''
            continue
        current += ch
    if current.strip():
        result.append(current.strip())

    py_params = []
    for param in result:
        param = param.strip()
        if not param:
            continue

        # Destructured params {a, b}: Type -> just skip type
        if param.startswith('{'):
            # Simplified: just use **kwargs or keep the names
            names = re.findall(r'\w+', param.split(':')[0].strip('{}'))
            py_params.extend(names)
            continue

        # Remove readonly
        param = re.sub(r'\breadonly\s+', '', param)

        # param?: type = default
        m = re.match(r'(\w+)\??\s*:\s*(.+?)(?:\s*=\s*(.+))?$', param)
        if m:
            name = m.group(1)
            ts_type = m.group(2).strip()
            default = m.group(3)
            py_type = convert_type(ts_type)
            optional = '?' in param.split(':')[0]

            if optional and not default:
                py_params.append(f'{name}: Optional[{py_type}] = None')
            elif default:
                py_default = convert_default_value(default)
                py_params.append(f'{name}: {py_type} = {py_default}')
            else:
                py_params.append(f'{name}: {py_type}')
        else:
            # Just a name
            m2 = re.match(r'(\w+)\s*=\s*(.+)', param)
            if m2:
                py_params.append(f'{m2.group(1)} = {convert_default_value(m2.group(2))}')
            else:
                py_params.append(param.split(':')[0].strip().rstrip('?'))

    return ', '.join(py_params)


def convert_type(ts_type: str) -> str:
    """Convert a TypeScript type to Python type hint."""
    if not ts_type:
        return 'Any'

    ts_type = ts_type.strip()

    type_map = {
        'string': 'str',
        'number': 'int | float',
        'boolean': 'bool',
        'void': 'None',
        'any': 'Any',
        'unknown': 'Any',
        'never': 'None',
        'undefined': 'None',
        'null': 'None',
        'object': 'dict',
        'Function': 'Callable',
        'Date': 'datetime',
        'RegExp': 'str',
        'Buffer': 'bytes',
        'Uint8Array': 'bytes',
        'ArrayBuffer': 'bytes',
        'Record': 'dict',
        'Promise': 'Any',  # handled specially in function return types
    }

    # Direct mapping
    if ts_type in type_map:
        return type_map[ts_type]

    # Array types: string[] -> list[str], Array<string> -> list[str]
    m = re.match(r'(\w+)\[\]$', ts_type)
    if m:
        inner = convert_type(m.group(1))
        return f'list[{inner}]'

    m = re.match(r'Array<(.+)>$', ts_type)
    if m:
        inner = convert_type(m.group(1))
        return f'list[{inner}]'

    # Map<K, V> -> dict[K, V]
    m = re.match(r'Map<(.+),\s*(.+)>$', ts_type)
    if m:
        k = convert_type(m.group(1))
        v = convert_type(m.group(2))
        return f'dict[{k}, {v}]'

    # Set<T> -> set[T]
    m = re.match(r'Set<(.+)>$', ts_type)
    if m:
        inner = convert_type(m.group(1))
        return f'set[{inner}]'

    # Promise<T> -> T (used for return types)
    m = re.match(r'Promise<(.+)>$', ts_type)
    if m:
        return convert_type(m.group(1))

    # Record<K, V> -> dict[K, V]
    m = re.match(r'Record<(.+),\s*(.+)>$', ts_type)
    if m:
        k = convert_type(m.group(1))
        v = convert_type(m.group(2))
        return f'dict[{k}, {v}]'

    # T | null | undefined -> Optional[T]
    if '|' in ts_type:
        parts = [p.strip() for p in ts_type.split('|')]
        has_null = any(p in ('null', 'undefined') for p in parts)
        non_null = [convert_type(p) for p in parts if p not in ('null', 'undefined')]
        if has_null and len(non_null) == 1:
            return f'Optional[{non_null[0]}]'
        elif len(non_null) > 1:
            return ' | '.join(non_null)
        elif non_null:
            return non_null[0]

    # Readonly<T> -> T
    m = re.match(r'Readonly<(.+)>$', ts_type)
    if m:
        return convert_type(m.group(1))

    # Partial<T> -> T (simplified)
    m = re.match(r'Partial<(.+)>$', ts_type)
    if m:
        return convert_type(m.group(1))

    # Keep custom types as-is
    return ts_type


def convert_default_value(val: str) -> str:
    """Convert a JS default value to Python."""
    val = val.strip().rstrip(',')
    if val == 'true':
        return 'True'
    if val == 'false':
        return 'False'
    if val == 'null' or val == 'undefined':
        return 'None'
    if val == '[]':
        return '[]'
    if val == '{}':
        return '{}'
    if val.startswith("'") or val.startswith('"'):
        return val
    return val


def convert_type_annotations(content: str) -> str:
    """Convert remaining inline type annotations."""
    # Remove 'as const' assertions
    content = re.sub(r'\s+as\s+const\b', '', content)
    # Remove angle bracket type assertions
    content = re.sub(r'<(\w+)>', '', content)
    return content


def convert_common_patterns(content: str) -> str:
    """Convert common JS/TS patterns to Python."""
    # console.log -> print
    content = re.sub(r'\bconsole\.log\(', 'print(', content)
    content = re.sub(r'\bconsole\.error\(', 'print(', content)
    content = re.sub(r'\bconsole\.warn\(', 'print(', content)

    # typeof x === 'string' -> isinstance(x, str)
    content = re.sub(r"typeof\s+(\w+)\s*===?\s*'string'", r'isinstance(\1, str)', content)
    content = re.sub(r"typeof\s+(\w+)\s*===?\s*'number'", r'isinstance(\1, (int, float))', content)
    content = re.sub(r"typeof\s+(\w+)\s*===?\s*'boolean'", r'isinstance(\1, bool)', content)
    content = re.sub(r"typeof\s+(\w+)\s*===?\s*'object'", r'isinstance(\1, dict)', content)
    content = re.sub(r"typeof\s+(\w+)\s*===?\s*'function'", r'callable(\1)', content)
    content = re.sub(r"typeof\s+(\w+)\s*===?\s*'undefined'", r'\1 is None', content)

    # === to ==, !== to !=
    content = content.replace('===', '==')
    content = content.replace('!==', '!=')

    # Object.keys(x) -> list(x.keys())
    content = re.sub(r'Object\.keys\((\w+)\)', r'list(\1.keys())', content)
    content = re.sub(r'Object\.values\((\w+)\)', r'list(\1.values())', content)
    content = re.sub(r'Object\.entries\((\w+)\)', r'list(\1.items())', content)

    # new Map() -> {}
    content = re.sub(r'new\s+Map(?:<[^>]+>)?\(\)', '{}', content)
    # new Set() -> set()
    content = re.sub(r'new\s+Set(?:<[^>]+>)?\(\)', 'set()', content)
    # new Set(x) -> set(x)
    content = re.sub(r'new\s+Set(?:<[^>]+>)?\(', 'set(', content)
    # new Map(x) -> dict(x)
    content = re.sub(r'new\s+Map(?:<[^>]+>)?\(', 'dict(', content)

    # x.has(y) for Map/Set -> y in x
    # x.delete(y) -> x.discard(y) or del x[y]
    # x.get(y) stays the same for dict

    # Array.isArray(x) -> isinstance(x, list)
    content = re.sub(r'Array\.isArray\((\w+)\)', r'isinstance(\1, list)', content)

    # x.length -> len(x) (when not assignment)
    content = re.sub(r'(\w+)\.length\b(?!\s*=)', r'len(\1)', content)

    # x.push(y) -> x.append(y)
    content = re.sub(r'\.push\(', '.append(', content)

    # x.includes(y) -> y in x
    content = re.sub(r'(\w+)\.includes\(([^)]+)\)', r'\2 in \1', content)

    # x.forEach(fn) -> for item in x: fn(item)
    content = re.sub(r'(\w+)\.forEach\(', r'for _item in \1: # ', content)

    # Math.min/max -> min/max
    content = re.sub(r'Math\.min\(', 'min(', content)
    content = re.sub(r'Math\.max\(', 'max(', content)
    content = re.sub(r'Math\.floor\(', 'int(', content)
    content = re.sub(r'Math\.ceil\(', 'import math; math.ceil(', content)
    content = re.sub(r'Math\.round\(', 'round(', content)
    content = re.sub(r'Math\.abs\(', 'abs(', content)
    content = re.sub(r'Math\.random\(\)', 'import random; random.random()', content)

    # JSON.stringify -> json.dumps
    content = re.sub(r'JSON\.stringify\(', 'json.dumps(', content)
    content = re.sub(r'JSON\.parse\(', 'json.loads(', content)

    # Date.now() -> time.time() * 1000
    content = re.sub(r'Date\.now\(\)', 'int(time.time() * 1000)', content)

    # new Date() -> datetime.now()
    content = re.sub(r'new\s+Date\(\)', 'datetime.now()', content)

    # setTimeout/setInterval -> asyncio patterns (simplified)
    content = re.sub(r'setTimeout\(([^,]+),\s*(\d+)\)', r'await asyncio.sleep(\2 / 1000); \1()', content)

    # for...of -> for...in
    content = re.sub(r'\bfor\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\s+', r'for \1 in ', content)
    content = re.sub(r'\bfor\s*\(\s*(?:const|let|var)\s+\[(\w+),\s*(\w+)\]\s+of\s+', r'for \1, \2 in ', content)

    # for...in (JS) -> for...in (Python)
    content = re.sub(r'\bfor\s*\(\s*(?:const|let|var)\s+(\w+)\s+in\s+', r'for \1 in ', content)

    return content


def convert_template_literals(content: str) -> str:
    """Convert template literals to f-strings."""
    def replace_template(m):
        s = m.group(0)
        # Replace ${expr} with {expr}
        s = re.sub(r'\$\{([^}]+)\}', r'{\1}', s)
        # Change backticks to quotes
        s = 'f"' + s[1:-1] + '"'
        return s

    content = re.sub(r'`[^`]*`', replace_template, content)
    return content


def convert_null_undefined(content: str) -> str:
    """Convert null/undefined to None."""
    content = re.sub(r'\bundefined\b', 'None', content)
    content = re.sub(r'\bnull\b', 'None', content)
    # Optional chaining x?.y -> x.y if x is not None else None (simplified to getattr)
    content = re.sub(r'(\w+)\?\.\s*(\w+)', r'getattr(\1, "\2", None)', content)
    # Nullish coalescing x ?? y -> x if x is not None else y
    content = re.sub(r'(\w+)\s*\?\?\s*([^\n;,]+)', r'(\1 if \1 is not None else \2)', content)
    return content


def convert_js_methods(content: str) -> str:
    """Convert common JS method calls."""
    # .map() stays the same (Python has map() too, or list comprehension)
    # .filter() -> [x for x in ... if ...]
    # .find() -> next((x for x in ... if ...), None)
    # .some() -> any(...)
    # .every() -> all(...)
    # .join(sep) stays similar
    # .trim() -> .strip()
    content = re.sub(r'\.trim\(\)', '.strip()', content)
    content = re.sub(r'\.trimStart\(\)', '.lstrip()', content)
    content = re.sub(r'\.trimEnd\(\)', '.rstrip()', content)
    # .startsWith -> .startswith
    content = re.sub(r'\.startsWith\(', '.startswith(', content)
    # .endsWith -> .endswith
    content = re.sub(r'\.endsWith\(', '.endswith(', content)
    # .toLowerCase() -> .lower()
    content = re.sub(r'\.toLowerCase\(\)', '.lower()', content)
    # .toUpperCase() -> .upper()
    content = re.sub(r'\.toUpperCase\(\)', '.upper()', content)
    # .replace() stays the same
    # .split() stays the same
    # .slice() -> slicing
    content = re.sub(r'\.slice\((\d+)\)', r'[\1:]', content)
    content = re.sub(r'\.slice\((\d+),\s*(\d+)\)', r'[\1:\2]', content)
    # .substring() -> slicing
    content = re.sub(r'\.substring\((\d+)\)', r'[\1:]', content)
    content = re.sub(r'\.substring\((\d+),\s*(\d+)\)', r'[\1:\2]', content)
    # .indexOf() -> .index() or .find()
    content = re.sub(r'\.indexOf\(', '.find(', content)
    # .padStart -> .rjust (simplified)
    content = re.sub(r'\.padStart\((\d+),\s*[\'"](.+?)[\'"]\)', r'.rjust(\1, "\2")', content)
    # .padEnd -> .ljust
    content = re.sub(r'\.padEnd\((\d+),\s*[\'"](.+?)[\'"]\)', r'.ljust(\1, "\2")', content)
    # .toString() -> str()
    content = re.sub(r'(\w+)\.toString\(\)', r'str(\1)', content)
    # parseInt -> int
    content = re.sub(r'parseInt\(', 'int(', content)
    # parseFloat -> float
    content = re.sub(r'parseFloat\(', 'float(', content)
    # Number() -> float() or int()
    content = re.sub(r'Number\(', 'float(', content)
    # String() -> str()
    content = re.sub(r'String\(', 'str(', content)
    # Boolean() -> bool()
    content = re.sub(r'Boolean\(', 'bool(', content)

    return content


def convert_braces(content: str) -> str:
    """
    Simple brace-to-colon conversion. This is necessarily imperfect for a
    regex-based approach, but handles the common cases.
    """
    # Already handled in function/class/if/for conversions
    # Just clean up remaining opening braces after control flow
    content = re.sub(r'\)\s*\{(\s*)$', r'):\1', content, flags=re.MULTILINE)
    content = re.sub(r'\belse\s*\{', 'else:', content)
    content = re.sub(r'\belse\s+if\s*\(', 'elif (', content)
    content = re.sub(r'\belif\s*\(([^)]+)\)\s*\{', r'elif \1:', content)
    content = re.sub(r'\bif\s*\(([^)]+)\)\s*\{', r'if \1:', content)
    content = re.sub(r'\bwhile\s*\(([^)]+)\)\s*\{', r'while \1:', content)
    content = re.sub(r'\btry\s*\{', 'try:', content)

    # Remove closing braces (simplified - will leave some artifacts)
    lines = content.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped == '}' or stripped == '};' or stripped == '},':
            continue  # Remove lone closing braces
        # Remove trailing } after content
        if stripped.endswith('}') and not stripped.startswith('{') and not stripped.startswith('f"') and not stripped.startswith("f'"):
            # Don't remove if it's part of a dict/set literal or f-string
            if not re.search(r'[{:]\s*$', stripped[:-1]) and not re.search(r'\{[^}]*\}', stripped):
                line = line.rstrip()
                if line.rstrip().endswith('}'):
                    line = line.rstrip()[:-1].rstrip()
        cleaned.append(line)

    return '\n'.join(cleaned)


def convert_variables(content: str) -> str:
    """Convert const/let/var to Python variable declarations."""
    # const x: type = value -> x: type = value
    content = re.sub(r'\b(?:const|let|var)\s+(\w+)\s*:\s*(\w+(?:<[^>]+>)?(?:\[\])?)\s*=',
                     lambda m: f'{m.group(1)}: {convert_type(m.group(2))} =', content)
    # const x = value -> x = value
    content = re.sub(r'\b(?:const|let|var)\s+(\w+)\s*=', r'\1 =', content)
    # const { a, b } = obj -> a, b = obj.a, obj.b (simplified, just destructure)
    content = re.sub(r'\b(?:const|let|var)\s+\{\s*([^}]+)\s*\}\s*=\s*(\w+)',
                     lambda m: convert_destructure(m.group(1), m.group(2)), content)
    # const [a, b] = arr -> a, b = arr
    content = re.sub(r'\b(?:const|let|var)\s+\[\s*([^\]]+)\s*\]\s*=\s*(.+)',
                     lambda m: f'{m.group(1).strip()} = {m.group(2).strip()}', content)
    return content


def convert_destructure(names_str: str, obj: str) -> str:
    """Convert object destructuring."""
    names = [n.strip().split(':')[0].strip() for n in names_str.split(',') if n.strip()]
    assignments = [f'{n} = {obj}["{n}"]' for n in names if n]
    return '\n'.join(assignments) if assignments else f'# destructure from {obj}'


def cleanup(content: str) -> str:
    """Final cleanup pass."""
    lines = content.split('\n')
    result = []
    for line in lines:
        # Remove 'declare ' keyword
        line = re.sub(r'\bdeclare\s+', '', line)
        # Remove 'readonly ' keyword
        line = re.sub(r'\breadonly\s+', '', line)
        # Remove 'static ' before methods (keep for class methods)
        # Remove 'private ' / 'protected ' / 'public '
        line = re.sub(r'\b(?:private|protected|public)\s+', '', line)
        # Remove 'implements X'
        line = re.sub(r'\bimplements\s+\w+', '', line)
        # Remove type-only lines
        stripped = line.strip()
        if stripped.startswith('type ') and '=' not in stripped:
            continue
        result.append(line)
    return '\n'.join(result)


def process_file(ts_path: str) -> str:
    """Process a single TypeScript file and return the Python output path."""
    with open(ts_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    filename = os.path.basename(ts_path)
    py_content = convert_ts_to_py(content, filename)

    # Determine output path
    base = os.path.splitext(ts_path)[0]
    py_path = base + '.py'

    with open(py_path, 'w', encoding='utf-8') as f:
        f.write(py_content)

    return py_path


def main():
    base_dir = '/home/ulrich/Documents/Projects/jarvis/src/utils'

    ts_files = []
    for root, dirs, files in os.walk(base_dir):
        for f in files:
            if f.endswith('.ts') or f.endswith('.tsx'):
                ts_files.append(os.path.join(root, f))

    ts_files.sort()

    print(f"Found {len(ts_files)} TypeScript files to convert")

    success = 0
    errors = []

    for ts_path in ts_files:
        try:
            py_path = process_file(ts_path)
            success += 1
            rel = os.path.relpath(py_path, base_dir)
            if success % 50 == 0:
                print(f"  Converted {success}/{len(ts_files)}...")
        except Exception as e:
            errors.append((ts_path, str(e)))
            print(f"  ERROR: {os.path.relpath(ts_path, base_dir)}: {e}")

    print(f"\nDone: {success} converted, {len(errors)} errors")

    if errors:
        print("\nErrors:")
        for path, err in errors:
            print(f"  {os.path.relpath(path, base_dir)}: {err}")


if __name__ == '__main__':
    main()
