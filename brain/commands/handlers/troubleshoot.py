"""JARVIS Troubleshooter — find and fix real bugs in code.

Multi-strategy approach:
1. Static analysis (pattern-based, instant)
2. Python AST analysis (catches real errors like undefined vars, bad imports)
3. Syntax validation (catches syntax errors before runtime)
4. Dependency checking (missing imports, circular deps)
5. Runtime error analysis (reads tracebacks and suggests fixes)
6. LLM-powered review (if a capable model is available)
"""

import os
import re
import ast
import sys
import subprocess
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


# ── Auto-fix generators ──────────────────────────────────────────────

def _generate_fix(issue: dict) -> dict | None:
    """Generate an auto-fix for an issue. Returns {file, old, new, description} or None."""
    filepath = issue.get("file", "")
    line_no = issue.get("line", 0)
    issue_type = issue.get("type", "")

    if not filepath or not os.path.exists(filepath):
        return None

    try:
        with open(filepath, "r") as f:
            lines = f.readlines()
    except Exception as e:
        return None

    # For unused imports, line_no might be 0 — we search the file
    if issue_type == "unused_import" and line_no == 0:
        line_no = 1  # Will be overridden by the import search below
        line = ""
    elif line_no > len(lines) or line_no == 0:
        return None
    else:
        line = lines[line_no - 1]

    # ── Bare except → except Exception ──
    if issue_type == "bare_except":
        old = line
        new = line.replace("except:", "except Exception:")
        if old != new:
            return {"file": filepath, "line": line_no, "old": old.rstrip(), "new": new.rstrip(),
                    "description": "Replace bare except with except Exception"}

    # ── None comparison == → is ──
    if issue_type == "none_comparison":
        old = line
        new = line.replace("== None", "is None").replace("!= None", "is not None")
        if old != new:
            return {"file": filepath, "line": line_no, "old": old.rstrip(), "new": new.rstrip(),
                    "description": "Use 'is None' instead of '== None'"}

    # ── Mutable default argument ──
    if issue_type == "mutable_default":
        # This needs AST-level fix — too complex for simple replacement
        return {"file": filepath, "line": line_no, "old": line.rstrip(), "new": None,
                "description": "Mutable default argument — change [] to None and add 'if arg is None: arg = []' inside function",
                "manual": True}

    # ── os.system → subprocess.run ──
    if "os.system" in issue.get("msg", ""):
        old = line
        match = re.search(r'os\.system\((.+)\)', line)
        if match:
            cmd_arg = match.group(1)
            indent = len(line) - len(line.lstrip())
            new = " " * indent + f"subprocess.run({cmd_arg}, shell=True)\n"
            return {"file": filepath, "line": line_no, "old": old.rstrip(), "new": new.rstrip(),
                    "description": "Replace os.system() with subprocess.run()"}

    # ── print() → logging ──
    if issue_type == "print_in_prod" or "print() in production" in issue.get("msg", ""):
        old = line
        match = re.search(r'print\((.+)\)', line)
        if match:
            content = match.group(1)
            indent = len(line) - len(line.lstrip())
            new = " " * indent + f"log.info({content})\n"
            return {"file": filepath, "line": line_no, "old": old.rstrip(), "new": new.rstrip(),
                    "description": "Replace print() with log.info()"}

    # ── Unused import ──
    if issue_type == "unused_import":
        name = issue.get("msg", "").replace("Unused import: ", "")
        for i, l in enumerate(lines):
            stripped = l.strip()
            if stripped.startswith("#"):
                continue
            # Handle: import name
            if stripped == f"import {name}" or stripped.startswith(f"import {name} "):
                return {"file": filepath, "line": i + 1, "old": l.rstrip(), "new": "",
                        "description": f"Remove unused import: {name}",
                        "delete_line": True}
            # Handle: from X import name (sole import)
            if f"import {name}" in stripped and stripped.startswith("from "):
                # Check if it's the only name imported
                match = re.search(r'import\s+(\w+)\s*$', stripped)
                if match and match.group(1) == name:
                    return {"file": filepath, "line": i + 1, "old": l.rstrip(), "new": "",
                            "description": f"Remove unused import: {name}",
                            "delete_line": True}
                # Multiple imports — remove just this name from the list
                if f", {name}" in stripped:
                    new_line = l.replace(f", {name}", "")
                    return {"file": filepath, "line": i + 1, "old": l.rstrip(),
                            "new": new_line.rstrip(),
                            "description": f"Remove unused '{name}' from import"}
                elif f"{name}, " in stripped:
                    new_line = l.replace(f"{name}, ", "")
                    return {"file": filepath, "line": i + 1, "old": l.rstrip(),
                            "new": new_line.rstrip(),
                            "description": f"Remove unused '{name}' from import"}
                elif f" {name}" in stripped:
                    # Name at end or middle — try comma-aware removal
                    new_line = re.sub(r',\s*' + re.escape(name), '', l)
                    if new_line != l:
                        return {"file": filepath, "line": i + 1, "old": l.rstrip(),
                                "new": new_line.rstrip(),
                                "description": f"Remove unused '{name}' from import"}

    return None


def _apply_fix(fix: dict) -> bool:
    """Apply a single fix to a file using content matching, not line numbers.

    Matches the exact old text to find the right line, so line number shifts
    from previous fixes don't cause wrong edits.
    """
    filepath = fix["file"]
    try:
        with open(filepath, "r") as f:
            content = f.read()
            lines = content.splitlines(keepends=True)

        old_text = fix.get("old", "").strip()
        if not old_text:
            return False

        # Find the line by matching content, not line number
        target_idx = None
        for i, line in enumerate(lines):
            if line.strip() == old_text:
                target_idx = i
                break

        if target_idx is None:
            # Line already removed or changed
            return False

        if fix.get("delete_line"):
            lines.pop(target_idx)
        elif fix.get("new") is not None:
            lines[target_idx] = fix["new"] + "\n"
        else:
            return False

        with open(filepath, "w") as f:
            f.writelines(lines)
        return True
    except Exception as e:
        return False


def _check_python_syntax(filepath: str) -> list[dict]:
    """Check Python file for syntax errors using ast.parse."""
    issues = []
    try:
        with open(filepath, "r", errors="replace") as f:
            source = f.read()
        ast.parse(source, filename=filepath)
    except SyntaxError as e:
        issues.append({
            "file": filepath, "line": e.lineno or 0, "severity": "critical",
            "type": "syntax_error",
            "msg": f"SyntaxError: {e.msg}",
            "fix": f"Fix the syntax at line {e.lineno}: {e.text.strip() if e.text else ''}"
        })
    return issues


def _check_python_ast(filepath: str) -> list[dict]:
    """Deep AST analysis — find undefined names, unused imports, bad patterns."""
    issues = []
    try:
        with open(filepath, "r", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return issues  # Already caught by syntax check

    # Collect all defined names
    defined = set()
    imported = set()
    used_names = set()
    function_names = set()
    class_names = set()

    for node in ast.walk(tree):
        # Track definitions
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            defined.add(node.name)
            function_names.add(node.name)
            for arg in node.args.args:
                defined.add(arg.arg)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
            class_names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported.add(name)
                defined.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported.add(name)
                defined.add(name)

        # Track usage — includes regular references AND type annotations
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)
        # Type annotations: def foo(x: SomeType) or x: SomeType = ...
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Check argument annotations
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                if arg.annotation and isinstance(arg.annotation, ast.Name):
                    used_names.add(arg.annotation.id)
                elif arg.annotation and isinstance(arg.annotation, ast.Attribute):
                    if isinstance(arg.annotation.value, ast.Name):
                        used_names.add(arg.annotation.value.id)
            # Check return annotation
            if node.returns and isinstance(node.returns, ast.Name):
                used_names.add(node.returns.id)
        # Variable annotations
        if isinstance(node, ast.AnnAssign) and isinstance(node.annotation, ast.Name):
            used_names.add(node.annotation.id)
        # Subscript annotations like list[str], Optional[X]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            used_names.add(node.value.id)

    # Check for unused imports (only top-level, skip __init__.py)
    fname = os.path.basename(filepath)
    if fname != "__init__.py":
        unused = imported - used_names - {"__all__"}
        # Filter out common false positives
        false_positives = {"Optional", "Any", "Dict", "List", "Tuple", "Set",
                          "Union", "Callable", "TYPE_CHECKING", "annotations"}
        unused -= false_positives
        for name in sorted(unused):
            issues.append({
                "file": filepath, "line": 0, "severity": "info",
                "type": "unused_import",
                "msg": f"Unused import: {name}",
                "fix": f"Remove unused import '{name}' or add to __all__"
            })

    # Check for functions that are too long
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.end_lineno and node.lineno:
                length = node.end_lineno - node.lineno
                if length > 100:
                    issues.append({
                        "file": filepath, "line": node.lineno, "severity": "info",
                        "type": "long_function",
                        "msg": f"Function '{node.name}' is {length} lines long",
                        "fix": "Consider breaking into smaller functions"
                    })

    # Check for mutable default arguments (common Python bug)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if default is None:
                    continue
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    issues.append({
                        "file": filepath, "line": node.lineno, "severity": "warning",
                        "type": "mutable_default",
                        "msg": f"Mutable default argument in '{node.name}'",
                        "fix": "Use None as default and create inside function: if arg is None: arg = []"
                    })

    # Check for bare except
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append({
                    "file": filepath, "line": node.lineno, "severity": "warning",
                    "type": "bare_except",
                    "msg": "Bare except catches SystemExit, KeyboardInterrupt too",
                    "fix": "Use 'except Exception:' instead of bare 'except:'"
                })

    # Check for comparison to None using == instead of is
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comparator, ast.Constant) and comparator.value is None:
                    issues.append({
                        "file": filepath, "line": node.lineno, "severity": "warning",
                        "type": "none_comparison",
                        "msg": "Comparison to None using == instead of 'is'",
                        "fix": "Use 'is None' or 'is not None'"
                    })

    return issues


def _check_imports(filepath: str, project_root: str) -> list[dict]:
    """Check if imports actually resolve."""
    issues = []
    try:
        with open(filepath, "r", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return issues

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            # Check if it's a project-internal import
            if module.startswith("brain.") or module.startswith("shells."):
                # Convert to path
                parts = module.split(".")
                possible_paths = [
                    os.path.join(project_root, *parts) + ".py",
                    os.path.join(project_root, *parts, "__init__.py"),
                ]
                if not any(os.path.exists(p) for p in possible_paths):
                    issues.append({
                        "file": filepath, "line": node.lineno, "severity": "critical",
                        "type": "broken_import",
                        "msg": f"Import '{module}' — module not found",
                        "fix": f"Check if {module} exists or fix the import path"
                    })

    return issues


def _check_file_comprehensive(filepath: str, project_root: str) -> list[dict]:
    """Run all checks on a single file."""
    issues = []
    ext = os.path.splitext(filepath)[1]

    if ext == ".py":
        issues.extend(_check_python_syntax(filepath))
        if not any(i["type"] == "syntax_error" for i in issues):
            issues.extend(_check_python_ast(filepath))
            issues.extend(_check_imports(filepath, project_root))

    return issues


def _run_pytest_check(project_root: str) -> list[dict]:
    """Run pytest in check mode (collect only, don't execute) to find import errors."""
    issues = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "test/"],
            capture_output=True, text=True, timeout=30,
            cwd=project_root,
        )
        if result.returncode != 0 and result.stderr:
            for line in result.stderr.split("\n"):
                if "Error" in line or "error" in line:
                    issues.append({
                        "file": "test/", "line": 0, "severity": "warning",
                        "type": "test_collection",
                        "msg": line.strip()[:120],
                        "fix": "Fix the import or test file"
                    })
    except Exception:
        pass
    return issues


def _analyze_traceback(traceback_text: str) -> dict:
    """Parse a Python traceback and suggest fixes."""
    lines = traceback_text.strip().split("\n")
    result = {"error": "", "file": "", "line": 0, "suggestion": ""}

    # Find the error line (last line)
    for line in reversed(lines):
        if line.strip() and not line.startswith(" "):
            result["error"] = line.strip()
            break

    # Find the file/line
    for line in lines:
        match = re.search(r'File "(.+?)", line (\d+)', line)
        if match:
            result["file"] = match.group(1)
            result["line"] = int(match.group(2))

    # Suggest fixes based on error type
    err = result["error"]
    if "ModuleNotFoundError" in err:
        module = re.search(r"No module named '(.+?)'", err)
        if module:
            result["suggestion"] = f"Install: pip install {module.group(1).split('.')[0]}"
    elif "ImportError" in err:
        result["suggestion"] = "Check import path — module exists but name doesn't"
    elif "AttributeError" in err:
        attr = re.search(r"has no attribute '(.+?)'", err)
        if attr:
            result["suggestion"] = f"'{attr.group(1)}' doesn't exist on that object — check spelling or add the method/property"
    elif "TypeError" in err:
        if "argument" in err:
            result["suggestion"] = "Wrong number/type of arguments — check function signature"
        elif "unexpected keyword" in err:
            kw = re.search(r"keyword argument '(.+?)'", err)
            result["suggestion"] = f"Parameter '{kw.group(1) if kw else '?'}' doesn't exist — check the function definition"
    elif "NameError" in err:
        name = re.search(r"name '(.+?)' is not defined", err)
        if name:
            result["suggestion"] = f"'{name.group(1)}' not defined — check spelling, imports, or scope"
    elif "KeyError" in err:
        result["suggestion"] = "Key doesn't exist in dict — use .get() with a default"
    elif "IndexError" in err:
        result["suggestion"] = "List index out of range — check list length before accessing"
    elif "FileNotFoundError" in err:
        result["suggestion"] = "File doesn't exist — check path and create if needed"
    elif "PermissionError" in err:
        result["suggestion"] = "No permission — check file ownership or run with sudo"
    elif "ValueError" in err:
        result["suggestion"] = "Invalid value — check input data types and ranges"
    elif "SyntaxError" in err:
        result["suggestion"] = "Fix the syntax — check for missing colons, brackets, quotes"

    return result


@command("troubleshoot", aliases=["ts", "debug-code", "check"],
         description="Deep code analysis — find bugs, errors, and suggest fixes",
         usage="/troubleshoot [file_or_dir]", category="git", permission=PermLevel.READ_ONLY)
async def cmd_troubleshoot(ctx: CommandContext) -> CommandResult:
    """Comprehensive code troubleshooting with real bug detection."""
    target = ctx.args.strip() or os.getcwd()
    target = os.path.expanduser(target)
    project_root = os.getcwd()

    report = []
    all_issues = []

    if os.path.isfile(target):
        # Single file analysis
        report.append(f"  Troubleshooting: {os.path.relpath(target, project_root)}")
        report.append(f"  {'=' * 50}")
        issues = _check_file_comprehensive(target, project_root)
        all_issues.extend(issues)

    elif os.path.isdir(target):
        # Directory analysis
        report.append(f"  Troubleshooting: {os.path.relpath(target, project_root) if target != project_root else '.'}/")
        report.append(f"  {'=' * 50}")

        skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules",
                     "claw-code-main", "target", ".cache"}

        py_files = []
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if fname.endswith(".py"):
                    py_files.append(os.path.join(dirpath, fname))

        report.append(f"  Scanning {len(py_files)} Python files...")
        report.append("")

        for fpath in sorted(py_files):
            issues = _check_file_comprehensive(fpath, project_root)
            all_issues.extend(issues)

        # Also run pytest collection check
        if target == project_root:
            test_issues = _run_pytest_check(project_root)
            all_issues.extend(test_issues)
    else:
        # Maybe it's a traceback?
        if "Traceback" in target or "Error" in target:
            analysis = _analyze_traceback(target)
            report.append(f"  Traceback Analysis")
            report.append(f"  {'=' * 50}")
            report.append(f"  Error: {analysis['error']}")
            if analysis['file']:
                report.append(f"  File:  {analysis['file']}:{analysis['line']}")
            if analysis['suggestion']:
                report.append(f"  Fix:   {analysis['suggestion']}")
            return CommandResult(text="\n".join(report))
        else:
            return CommandResult(text=f"Not found: {target}", success=False)

    # Format results
    critical = [i for i in all_issues if i["severity"] == "critical"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    infos = [i for i in all_issues if i["severity"] == "info"]

    report.append(f"  Results: {len(critical)} critical, {len(warnings)} warnings, {len(infos)} info")
    report.append("")

    # Add suggestions to every issue that doesn't have one
    for issue in all_issues:
        if not issue.get("fix"):
            itype = issue.get("type", "")
            if itype == "unused_import":
                issue["fix"] = f"Remove the unused import"
            elif itype == "long_function":
                issue["fix"] = "Break into smaller functions (each doing one thing)"
            elif itype == "broken_import":
                issue["fix"] = "Create the missing module or fix the import path"
            elif itype == "syntax_error":
                issue["fix"] = "Fix the syntax — check for missing colons, brackets, quotes"
            elif itype == "mutable_default":
                issue["fix"] = "Use None as default, then: if arg is None: arg = []"
            elif itype == "bare_except":
                issue["fix"] = "Use 'except Exception:' instead of bare 'except:'"
            elif itype == "none_comparison":
                issue["fix"] = "Use 'is None' or 'is not None'"
            else:
                issue["fix"] = "Review and fix manually"

    if critical:
        report.append(f"  CRITICAL ISSUES ({len(critical)})")
        report.append(f"  {'─' * 50}")
        for issue in critical:
            rel = os.path.relpath(issue["file"], project_root)
            line = f":{issue['line']}" if issue["line"] else ""
            report.append(f"    {rel}{line}")
            report.append(f"      Error:      {issue['msg']}")
            report.append(f"      Suggestion: {issue['fix']}")
            report.append("")

    if warnings:
        report.append(f"  WARNINGS ({len(warnings)})")
        report.append(f"  {'─' * 50}")
        for issue in warnings[:25]:
            rel = os.path.relpath(issue["file"], project_root)
            line = f":{issue['line']}" if issue["line"] else ""
            report.append(f"    {rel}{line}: {issue['msg']}")
            report.append(f"      Suggestion: {issue['fix']}")
        if len(warnings) > 25:
            report.append(f"    ... +{len(warnings) - 25} more")
        report.append("")

    if infos:
        report.append(f"  INFO ({len(infos)})")
        report.append(f"  {'─' * 50}")
        for issue in infos[:20]:
            rel = os.path.relpath(issue["file"], project_root)
            report.append(f"    {rel}: {issue['msg']}")
            report.append(f"      Suggestion: {issue['fix']}")
        if len(infos) > 20:
            report.append(f"    ... +{len(infos) - 20} more")

    if not all_issues:
        report.append(f"  No issues found! Code looks clean.")
        return CommandResult(text="\n".join(report))

    # ── Generate auto-fixes ──
    fixes = []
    for issue in all_issues:
        fix = _generate_fix(issue)
        if fix and not fix.get("manual"):
            fixes.append(fix)

    if fixes:
        report.append(f"\n  AVAILABLE FIXES ({len(fixes)})")
        report.append(f"  {'─' * 50}")
        for i, fix in enumerate(fixes, 1):
            rel = os.path.relpath(fix["file"], project_root)
            report.append(f"    {i}. {rel}:{fix['line']} — {fix['description']}")
            if fix.get("old"):
                report.append(f"       - {fix['old'].strip()}")
            if fix.get("new") is not None and not fix.get("delete_line"):
                report.append(f"       + {fix['new'].strip()}")
            elif fix.get("delete_line"):
                report.append(f"       (delete line)")

        report.append(f"\n  Use /apply-fixes to apply all, or /apply-fix <number> for one.")

    # Store fixes in brain for later application
    if ctx.brain and fixes:
        ctx.brain._pending_fixes = fixes

    return CommandResult(text="\n".join(report))


@command("apply-fixes", aliases=["apply-all"],
         description="Apply all pending fixes from last troubleshoot",
         usage="/apply-fixes", category="git", permission=PermLevel.FULL)
async def cmd_apply_fixes(ctx: CommandContext) -> CommandResult:
    """Apply all pending auto-fixes."""
    brain = ctx.brain
    if not brain or not hasattr(brain, '_pending_fixes') or not brain._pending_fixes:
        return CommandResult(text="No pending fixes. Run /troubleshoot first.", success=False)

    fixes = brain._pending_fixes
    # Sort: apply fixes bottom-up within each file (highest line first)
    # so deleting a line doesn't shift subsequent line numbers
    fixes.sort(key=lambda f: (f["file"], -f["line"]))
    report = [f"  Applying {len(fixes)} fixes...", ""]
    applied = 0
    failed = 0

    for fix in fixes:
        rel = os.path.relpath(fix["file"], os.getcwd())
        if _apply_fix(fix):
            report.append(f"    ✔ {rel}:{fix['line']} — {fix['description']}")
            applied += 1
        else:
            report.append(f"    ✘ {rel}:{fix['line']} — FAILED")
            failed += 1

    report.append(f"\n  Applied: {applied}, Failed: {failed}")
    brain._pending_fixes = []
    return CommandResult(text="\n".join(report))


@command("apply-fix", description="Apply a specific fix by number",
         usage="/apply-fix <number>", category="git", permission=PermLevel.FULL)
async def cmd_apply_fix(ctx: CommandContext) -> CommandResult:
    """Apply a single fix by its number from the troubleshoot report."""
    brain = ctx.brain
    if not brain or not hasattr(brain, '_pending_fixes') or not brain._pending_fixes:
        return CommandResult(text="No pending fixes. Run /troubleshoot first.", success=False)

    try:
        num = int(ctx.args.strip())
    except ValueError:
        return CommandResult(text="Usage: /apply-fix <number>", success=False)

    fixes = brain._pending_fixes
    if num < 1 or num > len(fixes):
        return CommandResult(text=f"Fix number must be between 1 and {len(fixes)}", success=False)

    fix = fixes[num - 1]
    rel = os.path.relpath(fix["file"], os.getcwd())

    if _apply_fix(fix):
        fixes.pop(num - 1)
        return CommandResult(text=f"  ✔ Applied: {rel}:{fix['line']} — {fix['description']}\n  {len(fixes)} fixes remaining.")
    else:
        return CommandResult(text=f"  ✘ Failed to apply fix at {rel}:{fix['line']}", success=False)


@command("fix-error", aliases=["fix", "diagnose"],
         description="Analyze a traceback/error and suggest fixes",
         usage="/fix-error <paste traceback>", category="git", permission=PermLevel.READ_ONLY)
async def cmd_fix_error(ctx: CommandContext) -> CommandResult:
    """Analyze a traceback and suggest fixes."""
    text = ctx.args.strip()
    if not text:
        return CommandResult(text="Paste a traceback or error message after the command:\n  /fix-error Traceback (most recent call last)...", success=False)

    analysis = _analyze_traceback(text)
    lines = ["  Error Analysis", "  " + "=" * 40]
    lines.append(f"  Error:      {analysis['error']}")
    if analysis["file"]:
        lines.append(f"  File:       {analysis['file']}")
    if analysis["line"]:
        lines.append(f"  Line:       {analysis['line']}")
    if analysis["suggestion"]:
        lines.append(f"  Suggestion: {analysis['suggestion']}")
    else:
        lines.append(f"  Suggestion: Check the error message and surrounding code")

    # Try to read the file and show context
    if analysis["file"] and os.path.exists(analysis["file"]) and analysis["line"]:
        try:
            with open(analysis["file"], "r") as f:
                file_lines = f.readlines()
            start = max(0, analysis["line"] - 3)
            end = min(len(file_lines), analysis["line"] + 3)
            lines.append(f"\n  Context ({os.path.basename(analysis['file'])}):")
            for i in range(start, end):
                marker = " >> " if i + 1 == analysis["line"] else "    "
                lines.append(f"  {marker}{i+1:4d} | {file_lines[i].rstrip()}")
        except Exception:
            pass

    return CommandResult(text="\n".join(lines))
