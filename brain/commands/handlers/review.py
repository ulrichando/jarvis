"""Structured codebase review — multi-pass approach that works with small models.

Instead of asking the LLM to review everything at once, this breaks the review
into small pieces that a 7B model can handle:

Pass 1: List all files and categorize them (no LLM needed)
Pass 2: Read key files (main.py, config, etc.) and summarize structure
Pass 3: For each subsystem, read 2-3 core files and note issues
Pass 4: Compile final report
"""

import os
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


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
            except Exception:
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
    lines.append(f"  {'─' * 46}")

    sorted_cats = sorted(scan["categories"].items(), key=lambda x: -sum(f["lines"] for f in x[1]))
    for cat, files in sorted_cats:
        cat_lines = sum(f["lines"] for f in files)
        lines.append(f"  {cat:<30s} {len(files):>6d} {cat_lines:>8,d}")

    return "\n".join(lines)


def _format_subsystem_review(name: str, files: list, summaries: dict) -> str:
    """Format review of a single subsystem."""
    lines = []
    lines.append(f"\n  ── {name} ({len(files)} files) ──")

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

        # ── Python-specific checks ──
        if ext == ".py":
            # Bare except (swallows all errors silently)
            if stripped in ("except:", "except Exception:", "except Exception as e:"):
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
                    "msg": "os.system() — use subprocess.run() instead"
                })

            # Shell=True without input validation
            if "shell=True" in stripped and "subprocess" in stripped:
                issues.append({
                    "file": filepath, "line": i, "severity": "info",
                    "msg": "subprocess with shell=True — ensure input is sanitized"
                })

            # print() in non-test/non-script files (should use logging)
            if stripped.startswith("print(") and "test_" not in fname and fname != "__main__.py":
                issues.append({
                    "file": filepath, "line": i, "severity": "info",
                    "msg": "print() in production code — consider logging"
                })

        # ── Rust-specific checks ──
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

        # ── General checks (all languages) ──
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
            "msg": f"Large file ({len(lines)} lines) — consider splitting"
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


@command("review-codebase", aliases=["rc", "full-review"],
         description="Structured multi-pass codebase review with issue detection",
         usage="/review-codebase [path]", category="git", permission=PermLevel.READ_ONLY)
async def cmd_review_codebase(ctx: CommandContext) -> CommandResult:
    """Multi-pass codebase review that works with small models."""
    root = ctx.args.strip() or os.getcwd()
    root = os.path.expanduser(root)

    if not os.path.isdir(root):
        return CommandResult(text=f"Not a directory: {root}", success=False)

    # ── Pass 1: Scan and categorize (instant, no LLM) ──
    scan = _scan_project(root)
    report = [_format_scan(scan)]

    # ── Pass 2: Review ALL files in each subsystem ──
    report.append(f"\n\n  SUBSYSTEM REVIEW")
    report.append(f"  {'=' * 50}")

    summaries = {}
    for cat, files in sorted(scan["categories"].items()):
        # Read every file in the category
        for f in files:
            full_path = os.path.join(root, f["path"])
            summaries[f["path"]] = _read_file_summary(full_path)

        report.append(_format_subsystem_review(cat, files, summaries))

    # ── Pass 3: Statistics ──
    report.append(f"\n\n  STATISTICS")
    report.append(f"  {'=' * 50}")

    test_files = [f for files in scan["categories"].values() for f in files if "test" in f["path"].lower()]
    all_classes = sum(len(s.get("classes", [])) for s in summaries.values())
    all_functions = sum(len(s.get("functions", [])) for s in summaries.values())
    report.append(f"  Test files:      {len(test_files)}")
    report.append(f"  Classes:         {all_classes}")
    report.append(f"  Functions:       {all_functions}")

    # ── Pass 4: Static Analysis (the real code review) ──
    report.append(f"\n\n  CODE ANALYSIS — Issues Found")
    report.append(f"  {'=' * 50}")

    all_issues = []
    for cat, files in scan["categories"].items():
        for f in files:
            full_path = os.path.join(root, f["path"])
            file_issues = _analyze_file(full_path)
            all_issues.extend(file_issues)

    # Count by severity
    critical = [i for i in all_issues if i["severity"] == "critical"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    infos = [i for i in all_issues if i["severity"] == "info"]

    report.append(f"  Critical:  {len(critical)}")
    report.append(f"  Warnings:  {len(warnings)}")
    report.append(f"  Info:      {len(infos)}")
    report.append(f"  Total:     {len(all_issues)}")

    # Show critical issues first
    if critical:
        report.append(f"\n  CRITICAL ({len(critical)})")
        report.append(f"  {'─' * 50}")
        for issue in critical[:30]:
            rel = os.path.relpath(issue["file"], root)
            line = f":{issue['line']}" if issue["line"] else ""
            report.append(f"    {rel}{line}")
            report.append(f"      {issue['msg']}")

    if warnings:
        report.append(f"\n  WARNINGS ({len(warnings)})")
        report.append(f"  {'─' * 50}")
        for issue in warnings[:30]:
            rel = os.path.relpath(issue["file"], root)
            line = f":{issue['line']}" if issue["line"] else ""
            report.append(f"    {rel}{line}")
            report.append(f"      {issue['msg']}")
        if len(warnings) > 30:
            report.append(f"    ... +{len(warnings) - 30} more warnings")

    if infos:
        report.append(f"\n  INFO ({len(infos)})")
        report.append(f"  {'─' * 50}")
        for issue in infos[:20]:
            rel = os.path.relpath(issue["file"], root)
            line = f":{issue['line']}" if issue["line"] else ""
            report.append(f"    {rel}{line}: {issue['msg']}")
        if len(infos) > 20:
            report.append(f"    ... +{len(infos) - 20} more")

    if not all_issues:
        report.append(f"\n  No issues found. Code looks clean.")

    # ── Pass 4: Language breakdown ──
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
