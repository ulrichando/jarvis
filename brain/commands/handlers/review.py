"""Structured codebase review -- multi-pass approach that works with small models.

Instead of asking the LLM to review everything at once, this breaks the review
into small pieces that a 7B model can handle:

Pass 1: List all files and categorize them (no LLM needed)
Pass 2: Read key files (main.py, config, etc.) and summarize structure
Pass 3: For each subsystem, read 2-3 core files and note issues
Pass 4: Compile final report

Also includes PR and commit review commands.
"""

import os
import re
import subprocess
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


def _run(cmd: list[str], timeout: int = 120, cwd: str = "") -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": timeout}
        if cwd:
            kwargs["cwd"] = cwd
        r = subprocess.run(cmd, **kwargs)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"


def _scan_project(root: str) -> dict:
    """Scan the project directory and categorize files. No LLM needed."""
    categories = {}
    total_lines = 0
    total_files = 0

    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules",
                 ".mypy_cache", ".pytest_cache", "claw-code-main", ".jarvis",
                 ".egg-info", "dist", "build", ".tox", ".cache", "target",
                 "static", "assets", "vendor", ".cargo", "release", "debug"}
    skip_exts = {".pyc", ".pyo", ".so", ".o", ".lock", ".wasm", ".bin",
                 ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
                 ".woff2", ".ttf", ".eot", ".map", ".min.js", ".min.css"}

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip_dirs IN-PLACE so os.walk doesn't descend into them
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        for fname in sorted(filenames):
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            ext = os.path.splitext(fname)[1]

            if ext in skip_exts:
                continue

            # Categorize by top-level directory
            parts = rel.split(os.sep)
            if len(parts) >= 2:
                category = parts[0]
                if len(parts) >= 3 and parts[0] == "brain":
                    category = f"brain/{parts[1]}"
            else:
                category = "(root)"

            if category not in categories:
                categories[category] = []

            try:
                size = os.path.getsize(fpath)
                if size > 500_000:  # Skip files > 500KB for line counting
                    lines = size // 40  # Estimate
                else:
                    lines = len(open(fpath, errors="replace").readlines())
            except Exception as e:
                lines = 0

            categories[category].append({"path": rel, "ext": ext, "lines": lines})
            total_lines += lines
            total_files += 1

    return {
        "root": root,
        "categories": categories,
        "total_files": total_files,
        "total_lines": total_lines,
    }


def _read_file_summary(path: str, max_lines: int = 50) -> str:
    """Read the first N lines of a file and extract key info."""
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        head = lines[:max_lines]
        text = "".join(head)

        # Extract classes and functions
        classes = [l.strip() for l in lines if l.strip().startswith("class ")]
        functions = [l.strip() for l in lines if l.strip().startswith("def ") or l.strip().startswith("async def ")]
        imports = [l.strip() for l in lines[:30] if l.strip().startswith(("import ", "from "))]

        return {
            "total_lines": total,
            "classes": classes[:10],
            "functions": functions[:20],
            "imports": imports[:15],
            "docstring": text.split('"""')[1][:200] if '"""' in text and text.count('"""') >= 2 else "",
            "head": "".join(head[:20]),
        }
    except Exception as e:
        return {"error": str(e)}


def _format_scan(scan: dict) -> str:
    """Format the scan results as a readable report."""
    lines = []
    lines.append(f"JARVIS Codebase Review")
    lines.append(f"{'=' * 60}")
    lines.append(f"  Root:        {scan['root']}")
    lines.append(f"  Total files: {scan['total_files']}")
    lines.append(f"  Total lines: {scan['total_lines']:,}")
    lines.append("")

    # Summary by category
    lines.append(f"  {'Category':<30s} {'Files':>6s} {'Lines':>8s}")
    lines.append(f"  {'---' * 15}")

    sorted_cats = sorted(scan["categories"].items(), key=lambda x: -sum(f["lines"] for f in x[1]))
    for cat, files in sorted_cats:
        cat_lines = sum(f["lines"] for f in files)
        lines.append(f"  {cat:<30s} {len(files):>6d} {cat_lines:>8,d}")

    return "\n".join(lines)


def _format_subsystem_review(name: str, files: list, summaries: dict) -> str:
    """Format review of a single subsystem."""
    lines = []
    lines.append(f"\n  -- {name} ({len(files)} files) --")

    for f in files:
        path = f["path"]
        summary = summaries.get(path, {})
        if "error" in summary:
            lines.append(f"    {path}: error reading")
            continue

        total = summary.get("total_lines", 0)
        classes = summary.get("classes", [])
        functions = summary.get("functions", [])
        docstring = summary.get("docstring", "")

        lines.append(f"    {path} ({total} lines)")
        if docstring:
            lines.append(f"      {docstring[:100]}")
        if classes:
            for c in classes[:3]:
                lines.append(f"      {c}")
        if functions:
            fn_names = [fn.split("(")[0].replace("def ", "").replace("async ", "").strip() for fn in functions[:8]]
            lines.append(f"      Functions: {', '.join(fn_names)}")

    return "\n".join(lines)


def _analyze_file(filepath: str) -> list[dict]:
    """Analyze a single file for common issues. No LLM needed."""
    issues = []
    try:
        with open(filepath, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return issues

    fname = os.path.basename(filepath)
    ext = os.path.splitext(fname)[1]

    # Only analyze code files
    if ext not in (".py", ".rs", ".js", ".ts", ".go", ".sh"):
        return issues

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # -- Python-specific checks --
        if ext == ".py":
            # Bare except (swallows all errors silently)
            if stripped == "except:":
                # Check if next line is just pass
                if i < len(lines) and lines[i].strip() in ("pass", "pass  # noqa"):
                    issues.append({
                        "file": filepath, "line": i, "severity": "warning",
                        "msg": "Silent exception swallowing (except + pass)"
                    })

            # Hardcoded secrets
            for secret_pat in ("api_key = \"", "api_key = '", "password = \"", "password = '",
                               "secret = \"", "token = \"", "API_KEY = \""):
                if secret_pat in stripped and "environ" not in stripped and "get(" not in stripped:
                    issues.append({
                        "file": filepath, "line": i, "severity": "critical",
                        "msg": f"Possible hardcoded secret: {stripped[:60]}"
                    })

            # TODO/FIXME/HACK/XXX
            for marker in ("# TODO", "# FIXME", "# HACK", "# XXX", "# BUG"):
                if marker in stripped:
                    issues.append({
                        "file": filepath, "line": i, "severity": "info",
                        "msg": f"{marker}: {stripped[stripped.index(marker)+len(marker):].strip()[:60]}"
                    })

            # Import * (pollutes namespace)
            if stripped.startswith("from ") and "import *" in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "warning",
                    "msg": f"Wildcard import: {stripped[:60]}"
                })

            # eval/exec (security risk)
            if "eval(" in stripped or "exec(" in stripped:
                if not stripped.startswith("#"):
                    issues.append({
                        "file": filepath, "line": i, "severity": "critical",
                        "msg": f"eval/exec usage (security risk): {stripped[:60]}"
                    })

            # os.system (use subprocess instead)
            if "os.system(" in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "warning",
                    "msg": "os.system() -- use subprocess.run() instead"
                })

            # Shell=True without input validation
            if "shell=True" in stripped and "subprocess" in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "info",
                    "msg": "subprocess with shell=True -- ensure input is sanitized"
                })

            # print() in non-test/non-script files (should use logging)
            if stripped.startswith("print(") and "test_" not in fname and fname != "__main__.py":
                issues.append({
                    "file": filepath, "line": i, "severity": "info",
                    "msg": "print() in production code -- consider logging"
                })

        # -- Rust-specific checks --
        if ext == ".rs":
            if ".unwrap()" in stripped and "test" not in filepath:
                issues.append({
                    "file": filepath, "line": i, "severity": "warning",
                    "msg": f"unwrap() can panic: {stripped[:60]}"
                })
            if ".expect(" in stripped and "test" not in filepath:
                issues.append({
                    "file": filepath, "line": i, "severity": "info",
                    "msg": f"expect() can panic: {stripped[:60]}"
                })
            if "unsafe " in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "critical",
                    "msg": f"unsafe block: {stripped[:60]}"
                })

        # -- General checks (all languages) --
        # Very long lines
        if len(line.rstrip()) > 200:
            issues.append({
                "file": filepath, "line": i, "severity": "info",
                "msg": f"Line too long ({len(line.rstrip())} chars)"
            })

    # File-level checks
    if len(lines) > 500:
        issues.append({
            "file": filepath, "line": 0, "severity": "info",
            "msg": f"Large file ({len(lines)} lines) -- consider splitting"
        })

    if len(lines) == 0:
        issues.append({
            "file": filepath, "line": 0, "severity": "warning",
            "msg": "Empty file"
        })

    # Check for missing docstring (Python)
    if ext == ".py" and len(lines) > 10:
        has_docstring = any('"""' in line or "'''" in line for line in lines[:5])
        if not has_docstring and fname != "__init__.py":
            issues.append({
                "file": filepath, "line": 1, "severity": "info",
                "msg": "No module docstring"
            })

    return issues


def _analyze_file_security(filepath: str) -> list[dict]:
    """Security-focused file analysis."""
    issues = []
    try:
        with open(filepath, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return issues

    ext = os.path.splitext(filepath)[1]
    if ext not in (".py", ".js", ".ts", ".go", ".rs", ".sh", ".sql"):
        return issues

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        # SQL injection
        if ext in (".py", ".js", ".ts"):
            if re.search(r'(?:execute|query|raw)\s*\(.*["\'].*%s|\.format\(|f".*\{.*\}.*(?:SELECT|INSERT|UPDATE|DELETE)', stripped, re.I):
                if "parameterized" not in stripped.lower() and "?" not in stripped:
                    issues.append({
                        "file": filepath, "line": i, "severity": "critical",
                        "category": "sql_injection",
                        "msg": f"Possible SQL injection: {stripped[:80]}"
                    })

        # Command injection
        if ext == ".py":
            if re.search(r'os\.(?:system|popen)\s*\(.*(?:format|%s|\+.*input|\+.*request)', stripped, re.I):
                issues.append({
                    "file": filepath, "line": i, "severity": "critical",
                    "category": "command_injection",
                    "msg": f"Command injection risk: {stripped[:80]}"
                })

        # Path traversal
        if re.search(r'open\s*\(.*(?:request|input|param|arg)', stripped, re.I):
            if "os.path.join" not in stripped and "Path(" not in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "warning",
                    "category": "path_traversal",
                    "msg": f"Unvalidated file path: {stripped[:80]}"
                })

        # XSS
        if ext in (".js", ".ts", ".jsx", ".tsx"):
            if "innerHTML" in stripped or "dangerouslySetInnerHTML" in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "warning",
                    "category": "xss",
                    "msg": f"XSS risk (innerHTML): {stripped[:80]}"
                })

        # Hardcoded credentials
        for pat_name, pat in [
            ("API key", re.compile(r'(?:api[_-]?key|apikey)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', re.I)),
            ("Password", re.compile(r'(?:password|passwd|pwd)\s*[=:]\s*["\'][^"\']{4,}', re.I)),
            ("Token", re.compile(r'(?:token|secret)\s*[=:]\s*["\'][A-Za-z0-9_\-\.]{16,}', re.I)),
        ]:
            if pat.search(stripped) and "environ" not in stripped and "getenv" not in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "critical",
                    "category": "hardcoded_secret",
                    "msg": f"Hardcoded {pat_name}: {stripped[:60]}"
                })

    return issues


def _analyze_file_performance(filepath: str) -> list[dict]:
    """Performance-focused file analysis."""
    issues = []
    try:
        with open(filepath, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return issues

    ext = os.path.splitext(filepath)[1]
    if ext != ".py":
        return issues

    in_loop = False
    loop_start = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        # Track loops
        if re.match(r'for\s+.+\s+in\s+', stripped) or stripped.startswith("while "):
            in_loop = True
            loop_start = i

        # N+1 query pattern (database call inside loop)
        if in_loop and re.search(r'\.(?:execute|query|find|get|filter|all)\s*\(', stripped):
            issues.append({
                "file": filepath, "line": i, "severity": "warning",
                "category": "n_plus_1",
                "msg": f"Possible N+1 query (DB call inside loop from L{loop_start}): {stripped[:60]}"
            })

        # String concatenation in loop
        if in_loop and re.search(r'\+\s*=\s*["\']|["\'].*\+.*["\']', stripped):
            issues.append({
                "file": filepath, "line": i, "severity": "info",
                "category": "string_concat",
                "msg": f"String concatenation in loop (use list + join): {stripped[:60]}"
            })

        # Global variable in hot path
        if stripped.startswith("global "):
            issues.append({
                "file": filepath, "line": i, "severity": "info",
                "category": "global_var",
                "msg": f"Global variable access (slower than local): {stripped[:60]}"
            })

        # Sync I/O in async function context
        if "async def" in stripped:
            pass  # start tracking
        if re.search(r'(?:open|read|write)\s*\(', stripped) and not stripped.startswith("#"):
            # Simple heuristic - would need AST for accuracy
            pass

        # Large list comprehension with no limit
        if re.search(r'\[.+for\s+.+in\s+.+\]', stripped) and len(stripped) > 100:
            issues.append({
                "file": filepath, "line": i, "severity": "info",
                "category": "memory",
                "msg": f"Large list comprehension (consider generator): {stripped[:60]}"
            })

        # Reset loop tracking on dedent
        if in_loop and indent == 0 and stripped and not stripped.startswith(("#", "def", "class")):
            in_loop = False

    # File-level performance checks
    if len(lines) > 800:
        issues.append({
            "file": filepath, "line": 0, "severity": "info",
            "category": "complexity",
            "msg": f"Large module ({len(lines)} lines) -- may impact import time"
        })

    return issues


def _analyze_file_style(filepath: str) -> list[dict]:
    """Code style and convention analysis."""
    issues = []
    try:
        with open(filepath, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return issues

    ext = os.path.splitext(filepath)[1]
    fname = os.path.basename(filepath)
    if ext != ".py":
        return issues

    has_docstring = False
    prev_blank = False
    function_count = 0
    class_count = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Check module docstring
        if i <= 3 and ('"""' in stripped or "'''" in stripped):
            has_docstring = True

        # Naming conventions
        if stripped.startswith("def ") or stripped.startswith("async def "):
            function_count += 1
            match = re.match(r'(?:async )?def\s+([A-Za-z_]\w*)\s*\(', stripped)
            if match:
                name = match.group(1)
                if name != name.lower() and not name.startswith("_"):
                    if not re.match(r'^[a-z_][a-z0-9_]*$', name):
                        issues.append({
                            "file": filepath, "line": i, "severity": "info",
                            "category": "naming",
                            "msg": f"Non-snake_case function name: {name}"
                        })

        if stripped.startswith("class "):
            class_count += 1
            match = re.match(r'class\s+([A-Za-z_]\w*)', stripped)
            if match:
                name = match.group(1)
                if not name[0].isupper():
                    issues.append({
                        "file": filepath, "line": i, "severity": "info",
                        "category": "naming",
                        "msg": f"Class name should be PascalCase: {name}"
                    })

        # Line length
        if len(line.rstrip()) > 120:
            issues.append({
                "file": filepath, "line": i, "severity": "info",
                "category": "line_length",
                "msg": f"Line too long ({len(line.rstrip())} > 120 chars)"
            })

        # Multiple blank lines
        if stripped == "":
            if prev_blank:
                pass  # PEP 8 allows 2 blank lines between top-level defs
            prev_blank = True
        else:
            prev_blank = False

        # Trailing whitespace
        if line.rstrip() != line.rstrip("\n") and line.strip():
            issues.append({
                "file": filepath, "line": i, "severity": "info",
                "category": "whitespace",
                "msg": "Trailing whitespace"
            })

    # File-level style checks
    if not has_docstring and fname != "__init__.py" and len(lines) > 10:
        issues.append({
            "file": filepath, "line": 1, "severity": "info",
            "category": "documentation",
            "msg": "Missing module docstring"
        })

    if function_count > 20:
        issues.append({
            "file": filepath, "line": 0, "severity": "info",
            "category": "organization",
            "msg": f"Too many functions ({function_count}) -- consider splitting module"
        })

    return issues


@command("review-codebase", aliases=["rc", "full-review"],
         description="Structured multi-pass codebase review with issue detection",
         usage="/review-codebase [path] [--security] [--performance] [--style]",
         category="git", permission=PermLevel.READ_ONLY)
async def cmd_review_codebase(ctx: CommandContext) -> CommandResult:
    """Multi-pass codebase review that works with small models.

    Supports focus flags:
      --security     Focus on security vulnerabilities only
      --performance  Focus on performance issues
      --style        Focus on code style/conventions
      (no flag)      General review with all checks
    """
    raw_args = ctx.args.strip()
    parts = raw_args.split()

    # Parse flags
    security_mode = "--security" in parts
    performance_mode = "--performance" in parts
    style_mode = "--style" in parts
    path_parts = [p for p in parts if not p.startswith("--")]
    root = path_parts[0] if path_parts else os.getcwd()
    root = os.path.expanduser(root)

    # Determine review focus
    focused = security_mode or performance_mode or style_mode
    focus_label = []
    if security_mode:
        focus_label.append("Security")
    if performance_mode:
        focus_label.append("Performance")
    if style_mode:
        focus_label.append("Style")

    if not os.path.isdir(root) and not os.path.isfile(root):
        return CommandResult(text=f"Not a directory or file: {root}", success=False)

    # Single file review
    if os.path.isfile(root):
        report = [f"Review: {root}"]
        report.append(f"{'=' * 60}")
        if focus_label:
            report.append(f"Focus: {', '.join(focus_label)}")

        all_issues = []
        if security_mode or not focused:
            all_issues.extend(_analyze_file_security(root))
        if performance_mode or not focused:
            all_issues.extend(_analyze_file_performance(root))
        if style_mode or not focused:
            all_issues.extend(_analyze_file_style(root))
        if not focused:
            all_issues.extend(_analyze_file(root))

        # Deduplicate by (line, msg prefix)
        seen = set()
        unique = []
        for issue in all_issues:
            key = (issue.get("line", 0), issue["msg"][:40])
            if key not in seen:
                seen.add(key)
                unique.append(issue)

        critical = [i for i in unique if i["severity"] == "critical"]
        warnings = [i for i in unique if i["severity"] == "warning"]
        infos = [i for i in unique if i["severity"] == "info"]

        report.append(f"\n  {len(critical)} critical, {len(warnings)} warnings, {len(infos)} info\n")

        for sev_name, sev_list in [("CRITICAL", critical), ("WARNING", warnings), ("INFO", infos)]:
            if sev_list:
                report.append(f"  {sev_name} ({len(sev_list)})")
                report.append(f"  {'---' * 15}")
                for issue in sev_list[:30]:
                    cat = f"[{issue.get('category', '')}] " if issue.get('category') else ""
                    loc = f":{issue['line']}" if issue.get('line') else ""
                    report.append(f"    {cat}L{loc}: {issue['msg']}")
                if len(sev_list) > 30:
                    report.append(f"    ... +{len(sev_list) - 30} more")
                report.append("")

        if not unique:
            report.append("  No issues found.")

        return CommandResult(text="\n".join(report))

    # -- Directory review --

    # Pass 1: Scan and categorize (instant, no LLM)
    scan = _scan_project(root)
    report = [_format_scan(scan)]

    if focus_label:
        report.append(f"\n  Review Focus: {', '.join(focus_label)}")

    # Pass 2: Review ALL files in each subsystem (only for general review)
    if not focused:
        report.append(f"\n\n  SUBSYSTEM REVIEW")
        report.append(f"  {'=' * 50}")

        summaries = {}
        for cat, files in sorted(scan["categories"].items()):
            for f in files:
                full_path = os.path.join(root, f["path"])
                summaries[f["path"]] = _read_file_summary(full_path)
            report.append(_format_subsystem_review(cat, files, summaries))

        # Statistics
        report.append(f"\n\n  STATISTICS")
        report.append(f"  {'=' * 50}")

        test_files = [f for files in scan["categories"].values() for f in files if "test" in f["path"].lower()]
        all_classes = sum(len(s.get("classes", [])) for s in summaries.values())
        all_functions = sum(len(s.get("functions", [])) for s in summaries.values())
        report.append(f"  Test files:      {len(test_files)}")
        report.append(f"  Classes:         {all_classes}")
        report.append(f"  Functions:       {all_functions}")

    # Pass 3: Focused or general static analysis
    report.append(f"\n\n  CODE ANALYSIS -- Issues Found")
    report.append(f"  {'=' * 50}")

    all_issues = []
    for cat, files in scan["categories"].items():
        for f in files:
            full_path = os.path.join(root, f["path"])
            if security_mode or not focused:
                all_issues.extend(_analyze_file_security(full_path))
            if performance_mode or not focused:
                all_issues.extend(_analyze_file_performance(full_path))
            if style_mode or not focused:
                all_issues.extend(_analyze_file_style(full_path))
            if not focused:
                all_issues.extend(_analyze_file(full_path))

    # Deduplicate
    seen = set()
    unique_issues = []
    for issue in all_issues:
        key = (issue["file"], issue.get("line", 0), issue["msg"][:40])
        if key not in seen:
            seen.add(key)
            unique_issues.append(issue)

    # Count by severity
    critical = [i for i in unique_issues if i["severity"] == "critical"]
    warnings = [i for i in unique_issues if i["severity"] == "warning"]
    infos = [i for i in unique_issues if i["severity"] == "info"]

    report.append(f"  Critical:  {len(critical)}")
    report.append(f"  Warnings:  {len(warnings)}")
    report.append(f"  Info:      {len(infos)}")
    report.append(f"  Total:     {len(unique_issues)}")

    # Show critical issues first
    if critical:
        report.append(f"\n  CRITICAL ({len(critical)})")
        report.append(f"  {'---' * 15}")
        for issue in critical[:30]:
            rel = os.path.relpath(issue["file"], root)
            line = f":{issue['line']}" if issue.get("line") else ""
            cat = f" [{issue.get('category', '')}]" if issue.get('category') else ""
            report.append(f"    {rel}{line}{cat}")
            report.append(f"      {issue['msg']}")

    if warnings:
        report.append(f"\n  WARNINGS ({len(warnings)})")
        report.append(f"  {'---' * 15}")
        for issue in warnings[:30]:
            rel = os.path.relpath(issue["file"], root)
            line = f":{issue['line']}" if issue.get("line") else ""
            cat = f" [{issue.get('category', '')}]" if issue.get('category') else ""
            report.append(f"    {rel}{line}{cat}")
            report.append(f"      {issue['msg']}")
        if len(warnings) > 30:
            report.append(f"    ... +{len(warnings) - 30} more warnings")

    if infos:
        report.append(f"\n  INFO ({len(infos)})")
        report.append(f"  {'---' * 15}")
        for issue in infos[:20]:
            rel = os.path.relpath(issue["file"], root)
            line = f":{issue['line']}" if issue.get("line") else ""
            report.append(f"    {rel}{line}: {issue['msg']}")
        if len(infos) > 20:
            report.append(f"    ... +{len(infos) - 20} more")

    if not unique_issues:
        report.append(f"\n  No issues found. Code looks clean.")

    # Language breakdown (only for general review)
    if not focused:
        report.append(f"\n\n  LANGUAGE BREAKDOWN")
        report.append(f"  {'=' * 50}")
        ext_counts = {}
        for files in scan["categories"].values():
            for f in files:
                ext = f["ext"] or "(no ext)"
                if ext not in ext_counts:
                    ext_counts[ext] = {"files": 0, "lines": 0}
                ext_counts[ext]["files"] += 1
                ext_counts[ext]["lines"] += f["lines"]

        for ext, counts in sorted(ext_counts.items(), key=lambda x: -x[1]["lines"]):
            report.append(f"  {ext:<10s} {counts['files']:>5d} files  {counts['lines']:>8,d} lines")

    return CommandResult(text="\n".join(report))


# ── /review-pr ───────────────────────────────────────────────────────

@command("review-pr", aliases=["rpr"],
         description="Review a pull request with structured analysis",
         usage="/review-pr [number]", category="git", permission=PermLevel.READ_ONLY)
async def cmd_review_pr(ctx: CommandContext) -> CommandResult:
    """Fetch a PR diff, analyze changes, and provide structured review.

    Without a number, reviews the current branch's PR.
    With a number, reviews that specific PR.
    """
    pr_number = ctx.args.strip()

    # Get PR diff
    if pr_number:
        try:
            int(pr_number)
        except ValueError:
            return CommandResult(text=f"Invalid PR number: {pr_number}", success=False)
        rc, diff, err = _run(["gh", "pr", "diff", pr_number, "--color=never"], timeout=30)
        rc_info, info_out, _ = _run(["gh", "pr", "view", pr_number, "--json",
                                      "title,body,files,additions,deletions,author,state"], timeout=15)
    else:
        rc, diff, err = _run(["gh", "pr", "diff", "--color=never"], timeout=30)
        rc_info, info_out, _ = _run(["gh", "pr", "view", "--json",
                                      "title,body,files,additions,deletions,author,state"], timeout=15)

    if rc != 0:
        if "no pull requests found" in err.lower() or "could not find" in err.lower():
            return CommandResult(text="No pull request found for this branch. "
                               "Create one first with: gh pr create", success=False)
        return CommandResult(text=f"Failed to get PR diff: {err}", success=False)

    if not diff.strip():
        return CommandResult(text="PR has no changes.", success=False)

    # Parse PR info
    pr_title = ""
    pr_additions = 0
    pr_deletions = 0
    pr_files = []
    if rc_info == 0 and info_out.strip():
        try:
            import json
            info = json.loads(info_out)
            pr_title = info.get("title", "")
            pr_additions = info.get("additions", 0)
            pr_deletions = info.get("deletions", 0)
            pr_files = info.get("files", [])
        except Exception:
            pass

    lines = [f"PR Review: {pr_title or f'#{pr_number}' if pr_number else 'current branch'}",
             "=" * 60]
    if pr_additions or pr_deletions:
        lines.append(f"  +{pr_additions} / -{pr_deletions} across {len(pr_files)} file(s)")

    # Parse diff into per-file chunks
    file_diffs = {}
    current_file = None
    current_lines = []
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            if current_file:
                file_diffs[current_file] = "\n".join(current_lines)
            match = re.search(r'b/(.+)$', line)
            current_file = match.group(1) if match else "unknown"
            current_lines = [line]
        elif current_file:
            current_lines.append(line)
    if current_file:
        file_diffs[current_file] = "\n".join(current_lines)

    # Analyze each changed file
    all_findings = []
    for fname, fdiff in file_diffs.items():
        ext = os.path.splitext(fname)[1]
        added_lines = [l[1:] for l in fdiff.splitlines() if l.startswith("+") and not l.startswith("+++")]

        for i, added in enumerate(added_lines):
            stripped = added.strip()

            # Security checks on added lines
            if "eval(" in stripped or "exec(" in stripped:
                all_findings.append(("CRITICAL", fname, f"eval/exec in new code: {stripped[:60]}"))
            if "shell=True" in stripped:
                all_findings.append(("WARNING", fname, f"subprocess shell=True: {stripped[:60]}"))
            if re.search(r'(?:password|secret|api_key|token)\s*=\s*["\']', stripped, re.I):
                if "environ" not in stripped and "getenv" not in stripped:
                    all_findings.append(("CRITICAL", fname, f"Hardcoded secret: {stripped[:60]}"))
            if "TODO" in stripped or "FIXME" in stripped or "HACK" in stripped:
                all_findings.append(("INFO", fname, f"TODO/FIXME: {stripped[:60]}"))

            # Python-specific
            if ext == ".py":
                if stripped == "except:":
                    all_findings.append(("WARNING", fname, "Bare except clause"))
                if "import *" in stripped:
                    all_findings.append(("WARNING", fname, f"Wildcard import: {stripped[:60]}"))
                if stripped.startswith("print(") and "test" not in fname.lower():
                    all_findings.append(("INFO", fname, "print() in production code"))

    # Format findings
    if all_findings:
        critical = [f for f in all_findings if f[0] == "CRITICAL"]
        warnings = [f for f in all_findings if f[0] == "WARNING"]
        infos = [f for f in all_findings if f[0] == "INFO"]

        lines.append(f"\n  Static Analysis: {len(critical)} critical, {len(warnings)} warnings, {len(infos)} info")

        for sev, items in [("CRITICAL", critical), ("WARNING", warnings), ("INFO", infos)]:
            if items:
                lines.append(f"\n  {sev} ({len(items)})")
                lines.append(f"  {'---' * 15}")
                for _, fname, msg in items[:20]:
                    lines.append(f"    {fname}: {msg}")
    else:
        lines.append("\n  Static analysis: No issues found in diff.")

    # Use AI for deeper review if brain available
    brain = ctx.brain
    if brain and hasattr(brain, "think"):
        try:
            prompt = (
                f"Review this pull request diff. Provide a structured code review:\n\n"
                f"Title: {pr_title}\n"
                f"Files changed: {len(file_diffs)}\n\n"
                f"Diff (truncated):\n```diff\n{diff[:6000]}\n```\n\n"
                "Format your review as:\n"
                "1. Summary (1-2 sentences)\n"
                "2. Issues found (with severity: CRITICAL/HIGH/MEDIUM/LOW)\n"
                "3. Suggestions for improvement\n"
                "4. Overall assessment (approve/request changes/needs discussion)"
            )
            ai_review = await brain.think(prompt)
            lines.append(f"\n  AI Review")
            lines.append(f"  {'=' * 50}")
            lines.append(ai_review)
        except Exception as e:
            lines.append(f"\n  AI review unavailable: {e}")

    return CommandResult(text="\n".join(lines))


# ── /review-commit ───────────────────────────────────────────────────

@command("review-commit", aliases=["rcommit"],
         description="Review a specific commit for issues",
         usage="/review-commit [hash]", category="git", permission=PermLevel.READ_ONLY)
async def cmd_review_commit(ctx: CommandContext) -> CommandResult:
    """Analyze a specific commit for bugs, security issues, and quality.

    Without a hash, reviews the latest commit.
    """
    commit_hash = ctx.args.strip() or "HEAD"

    # Get commit info
    rc, info_out, err = _run(["git", "log", "-1", "--format=%H%n%an%n%ae%n%s%n%b", commit_hash], timeout=10)
    if rc != 0:
        return CommandResult(text=f"Failed to find commit: {err}", success=False)

    info_lines = info_out.strip().splitlines()
    full_hash = info_lines[0] if info_lines else commit_hash
    author = info_lines[1] if len(info_lines) > 1 else "unknown"
    subject = info_lines[3] if len(info_lines) > 3 else ""
    body = "\n".join(info_lines[4:]) if len(info_lines) > 4 else ""

    # Get commit diff
    rc, diff, _ = _run(["git", "diff", f"{commit_hash}^..{commit_hash}", "--no-color"], timeout=30)
    if rc != 0:
        # Maybe first commit
        rc, diff, _ = _run(["git", "diff", "--root", commit_hash, "--no-color"], timeout=30)

    # Get changed files
    rc_files, files_out, _ = _run(["git", "diff", "--stat", f"{commit_hash}^..{commit_hash}"], timeout=10)
    if rc_files != 0:
        rc_files, files_out, _ = _run(["git", "diff", "--stat", "--root", commit_hash], timeout=10)

    report = [f"Commit Review: {full_hash[:12]}", "=" * 60]
    report.append(f"  Author:  {author}")
    report.append(f"  Message: {subject}")
    if body.strip():
        report.append(f"  Body:    {body[:200]}")

    if files_out:
        report.append(f"\n  Changed Files:")
        for line in files_out.strip().splitlines()[-10:]:
            report.append(f"    {line}")

    # Analyze diff for issues
    if diff:
        findings = []
        added_lines = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]

        for line in added_lines:
            stripped = line[1:].strip()
            if not stripped or stripped.startswith("#"):
                continue

            if "eval(" in stripped or "exec(" in stripped:
                findings.append(("CRITICAL", f"eval/exec: {stripped[:60]}"))
            if re.search(r'(?:password|secret|api_key)\s*=\s*["\']', stripped, re.I):
                if "environ" not in stripped:
                    findings.append(("CRITICAL", f"Hardcoded secret: {stripped[:60]}"))
            if "shell=True" in stripped:
                findings.append(("WARNING", f"shell=True: {stripped[:60]}"))
            if stripped == "except:":
                findings.append(("WARNING", "Bare except clause"))
            if "import *" in stripped:
                findings.append(("WARNING", f"Wildcard import: {stripped[:60]}"))

        if findings:
            report.append(f"\n  Issues in Added Code ({len(findings)})")
            report.append(f"  {'---' * 15}")
            for sev, msg in findings[:20]:
                report.append(f"    [{sev}] {msg}")
        else:
            report.append(f"\n  No obvious issues in added code.")

        # Commit message quality check
        report.append(f"\n  Commit Message Quality")
        report.append(f"  {'---' * 15}")
        if len(subject) < 10:
            report.append("    WARNING: Subject too short")
        elif len(subject) > 72:
            report.append("    INFO: Subject over 72 chars (conventional limit)")
        else:
            report.append("    OK: Subject length is good")
        if not body.strip() and len(added_lines) > 20:
            report.append("    INFO: No commit body for a sizable change -- consider adding context")

        # Use AI for deeper review if available
        brain = ctx.brain
        if brain and hasattr(brain, "think"):
            try:
                prompt = (
                    f"Review this git commit. Be concise and actionable.\n\n"
                    f"Commit: {full_hash[:12]} by {author}\n"
                    f"Message: {subject}\n"
                    f"Body: {body[:200]}\n\n"
                    f"Diff (truncated):\n```diff\n{diff[:5000]}\n```\n\n"
                    "Provide:\n"
                    "1. Brief summary of what this commit does\n"
                    "2. Any bugs or issues introduced\n"
                    "3. Suggestions for improvement"
                )
                ai_review = await brain.think(prompt)
                report.append(f"\n  AI Analysis")
                report.append(f"  {'=' * 50}")
                report.append(ai_review)
            except Exception as e:
                report.append(f"\n  AI review unavailable: {e}")
    else:
        report.append("\n  No diff available for analysis.")

    return CommandResult(text="\n".join(report))
